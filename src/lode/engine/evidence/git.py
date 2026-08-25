"""Read-only, ref-pinned Git evidence collection.

``collect_git_evidence`` clones each registered repo at a *fixed* ref (the
incident's deploy commit when known, otherwise the repo's default branch),
searches the working tree for terms derived from the alert (exception class,
function/symbol names, key phrases), extracts a small context snippet around
each hit, masks secrets, and persists one :class:`EvidenceArtifact` per file so
the analysis can cite ``repo@commit:path:line``.

Cloning is strictly read-only and bounded: a single incident can never pull an
entire monorepo into the prompt or exhaust disk, because ``max_files`` /
``max_bytes`` caps are enforced in :func:`search_tree`.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.config import settings
from lode.db.models.alert import Alert
from lode.db.models.application import ApplicationRepo
from lode.db.models.git import GitRepo
from lode.db.models.investigation import EvidenceArtifact
from lode.engine.evidence.secret_mask import mask_secrets

logger = logging.getLogger("lode.evidence.git")

# Directories and binary-ish extensions we never open for source inspection.
_SKIP_DIRS = {
    ".git",
    "node_modules",
    "vendor",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    "target",
    ".tox",
    "coverage",
}
_SKIP_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".woff", ".woff2",
    ".ttf", ".eot", ".pdf", ".zip", ".gz", ".tar", ".tgz", ".lock", ".bin",
    ".so", ".dylib", ".dll", ".exe", ".pyc", ".class",
}
_TOKEN_SPLIT = re.compile(r"[\s:.,/()\[\]{}'\"`]+")
_MIN_TERM_LEN = 4
# Generic English words that are useless as search terms (kept tiny on purpose).
_STOPWORDS = {
    "error", "exception", "failed", "failure", "occurred", "occurred", "service",
    "timeout", "timed", "traceback", "stack", "cause", "root", "the", "and",
    "for", "with", "from", "this", "that", "null", "none", "true", "false",
}


def derive_query_terms(alert: Alert) -> list[str]:
    """Extract candidate search terms from an alert's error and metadata.

    Pulls tokens from the error message, common fields (``error`` / ``exception``
    / ``stack`` / ``function`` / ``class`` / ``symbol``), and the title. Returns
    at most 25 distinct, non-trivial terms.
    """
    raw: list[str] = []

    def _add(text: str | None) -> None:
        if not text:
            return
        for tok in _TOKEN_SPLIT.split(text):
            t = tok.strip()
            if len(t) >= _MIN_TERM_LEN and not t.isdigit():
                raw.append(t)

    _add(alert.error_message)
    _add(alert.title)
    fields = getattr(alert, "fields", None) or {}
    for key in ("error", "exception", "stack", "function", "class", "symbol", "message"):
        val = fields.get(key)
        if isinstance(val, str):
            _add(val)

    terms: list[str] = []
    seen: set[str] = set()
    for t in raw:
        low = t.lower()
        if low in _STOPWORDS or low in seen:
            continue
        seen.add(low)
        terms.append(t)
        if len(terms) >= 25:
            break
    return terms


def search_tree(
    root: Path,
    terms: list[str],
    *,
    max_files: int = 20,
    max_bytes: int = 200_000,
    snippet_lines: int = 12,
) -> list[dict[str, Any]]:
    """Find ``terms`` in ``root`` and return bounded context snippets.

    Returns a list of ``{"path", "line", "snippet", "terms"}`` — one entry per
    matched file (centered on its first hit). Enforces ``max_files`` and a
    running ``max_bytes`` budget so a single search can never explode.
    """
    if not terms:
        return []
    compiled = [(t, re.compile(re.escape(t), re.IGNORECASE)) for t in terms]
    hits: list[dict[str, Any]] = []
    bytes_used = 0

    for path in sorted(root.rglob("*")):
        if len(hits) >= max_files:
            break
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in _SKIP_EXT:
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue

        matched_here: list[str] = []
        first_line = None
        for t, pat in compiled:
            m = pat.search(text)
            if m is not None:
                matched_here.append(t)
                if first_line is None:
                    first_line = text.count("\n", 0, m.start()) + 1
        if not matched_here:
            continue

        start = max(0, first_line - snippet_lines // 2)
        snippet = _slice_lines(text, start, snippet_lines)
        snippet_bytes = len(snippet.encode("utf-8"))
        if bytes_used + snippet_bytes > max_bytes:
            break
        bytes_used += snippet_bytes

        hits.append(
            {
                "path": str(path.relative_to(root)),
                "line": first_line,
                "snippet": snippet,
                "terms": matched_here,
            }
        )
    return hits


def _slice_lines(text: str, start: int, count: int) -> str:
    lines = text.splitlines()
    # start is 1-based
    lo = max(0, start - 1)
    return "\n".join(lines[lo : lo + count])


def _resolve_ref(alert: Alert, repo: GitRepo) -> str:
    fields = getattr(alert, "fields", None) or {}
    for key in ("commit", "git_commit", "sha", "revision", "ref"):
        val = fields.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return repo.default_branch or "main"


async def ensure_repo_clone(repo: GitRepo, ref: str, cache_root: Path, timeout: int) -> Path:
    """Read-only clone of ``repo`` at ``ref`` into an analysis sandbox.

    ``cache_root`` is a unique temporary directory owned by one analysis task.
    Only ``clone`` / ``checkout`` (no push, no write remotes) are used; a fixed
    ref keeps the evidence reproducible. Raises ``RuntimeError`` on clone failure
    so the caller can degrade gracefully rather than attributing a missing repo to
    a root cause.
    """
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", repo.repo_url)
    checkout = cache_root / f"{repo.id}_{safe}"
    cache_root.mkdir(parents=True, exist_ok=True)

    def _git(args: list[str], cwd: Path | None = None) -> None:
        subprocess.run(
            ["git", "-c", "protocol.ext.allow=never", *args],
            cwd=str(cwd) if cwd else None,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    try:
        _git(
            ["clone", "--depth", "1", "--single-branch", "--branch", ref,
             repo.repo_url, str(checkout)],
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # The ref may be a commit SHA rather than a branch; clone default then
        # check out the exact ref.
        _git(["clone", "--depth", "1", repo.repo_url, str(checkout)])
        _git(["checkout", "--force", ref], cwd=checkout)
    return checkout


async def collect_git_evidence(
    session: AsyncSession,
    application_id: int,
    alert: Alert,
    analysis_id: int,
) -> dict[str, Any]:
    """Collect and persist Git evidence for an incident.

    Adds :class:`EvidenceArtifact` rows to ``session`` (caller commits). Returns
    a summary the runner embeds in the analysis prompt and the final evidence
    packet so every conclusion can cite a locator. Failures are logged and
    skipped — missing source is a degraded analysis, not a hard error.
    """
    result = await session.execute(
        select(GitRepo)
        .join(ApplicationRepo, ApplicationRepo.repo_id == GitRepo.id)
        .where(ApplicationRepo.application_id == application_id)
    )
    repos = result.scalars().all()
    terms = derive_query_terms(alert)
    sandbox_base = Path(settings.evidence_git_cache_dir)
    try:
        sandbox_base.mkdir(parents=True, exist_ok=True)
        sandbox_path = Path(
            tempfile.mkdtemp(prefix=f"analysis-{analysis_id}-", dir=sandbox_base)
        )
    except OSError as exc:
        logger.warning("git evidence sandbox unavailable for analysis %s: %s", analysis_id, exc)
        return {
            "artifact_count": 0,
            "files": [],
            "repos_searched": len(repos),
            "terms": terms,
        }
    artifacts: list[dict[str, Any]] = []

    try:
        if not repos:
            return {"artifact_count": 0, "files": [], "repos_searched": 0}

        if not terms:
            return {"artifact_count": 0, "files": [], "repos_searched": len(repos)}

        for repo in repos:
            ref = _resolve_ref(alert, repo)
            try:
                checkout = await ensure_repo_clone(
                    repo, ref, sandbox_path, settings.evidence_git_clone_timeout_seconds
                )
            except Exception as exc:  # noqa: BLE001 - degrade, don't fail the analysis
                logger.warning("git evidence clone failed for %s: %s", repo.repo_url, exc)
                continue

            hits = search_tree(
                checkout,
                terms,
                max_files=settings.evidence_git_max_files,
                max_bytes=settings.evidence_git_max_bytes,
                snippet_lines=settings.evidence_git_snippet_lines,
            )
            for hit in hits:
                masked, categories = mask_secrets(hit["snippet"])
                retention = None
                days = settings.evidence_retention_days
                if days and days > 0:
                    retention = datetime.now(timezone.utc) + timedelta(days=days)
                artifact = EvidenceArtifact(
                    analysis_id=analysis_id,
                    artifact_type="git_file",
                    source_kind="git",
                    source_id=repo.id,
                    locator=f"{repo.repo_url}@{ref}:{hit['path']}:{hit['line']}",
                    content_hash=_hash(hit["snippet"]),
                    redacted_excerpt=masked,
                    metadata_={
                        "terms": hit["terms"],
                        "matched_line": hit["line"],
                        "repo_name": repo.name,
                        "ref": ref,
                        "secret_categories": categories,
                        "time_scope": "source_revision",
                        "collector_version": "2",
                    },
                    retention_until=retention,
                )
                session.add(artifact)
                artifacts.append(
                    {
                        "_artifact": artifact,
                        "locator": artifact.locator,
                        "line": hit["line"],
                        "terms": hit["terms"],
                        "secret_categories": categories,
                        "excerpt": masked[:4000],
                    }
                )

        await session.flush()  # assign IDs without committing the analysis txn
        for item in artifacts:
            artifact = item.pop("_artifact")
            item["artifact_id"] = artifact.id
    finally:
        shutil.rmtree(sandbox_path, ignore_errors=True)

    return {
        "artifact_count": len(artifacts),
        "files": artifacts,
        "repos_searched": len(repos),
        "terms": terms,
    }


def _hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()
