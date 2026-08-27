"""Bounded, read-only Git access pinned to immutable revisions."""

from __future__ import annotations

import asyncio
import os
import stat
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from lode.config import settings
from lode.engine.evidence.git import related_symbol_hits, search_tree, stack_hits
from lode.runtime_defaults import (
    SOURCE_GIT_TIMEOUT_SECONDS,
    SOURCE_MAX_BYTES,
    SOURCE_MAX_FILES,
    SOURCE_SNIPPET_LINES,
)


class GitSourceUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GitCredentialMaterial:
    auth_type: str
    username: str
    secret: str


@dataclass(frozen=True, slots=True)
class GitSourceHit:
    path: str
    symbol: str | None
    start_line: int
    end_line: int
    content: str
    selection_reason: str


class GitRevisionResolver(Protocol):
    async def resolve_branch(
        self,
        *,
        repo_url: str,
        branch: str,
        credential: GitCredentialMaterial | None,
    ) -> str | None: ...

    async def resolve_revision(
        self,
        *,
        repo_url: str,
        revision: str,
        credential: GitCredentialMaterial | None,
    ) -> str | None: ...


class GitRemoteRevisionResolver:
    """Resolve a declared branch without cloning or accepting symbolic ambiguity."""

    def __init__(self, *, timeout_seconds: float | None = None) -> None:
        self.timeout_seconds = timeout_seconds or min(10.0, SOURCE_GIT_TIMEOUT_SECONDS)

    async def resolve_branch(
        self,
        *,
        repo_url: str,
        branch: str,
        credential: GitCredentialMaterial | None,
    ) -> str | None:
        validate_git_remote(repo_url)
        _validate_branch(branch)
        with _git_auth(credential) as environment:
            result = await _run_git(
                "ls-remote",
                "--refs",
                repo_url,
                f"refs/heads/{branch}",
                environment=environment,
                timeout_seconds=self.timeout_seconds,
            )
        if result.returncode != 0:
            return None
        rows = [line.split() for line in result.stdout.splitlines() if line.strip()]
        matches = [row[0] for row in rows if len(row) == 2 and row[1] == f"refs/heads/{branch}"]
        if len(matches) != 1 or not _is_sha(matches[0]):
            return None
        return matches[0]

    async def resolve_revision(
        self,
        *,
        repo_url: str,
        revision: str,
        credential: GitCredentialMaterial | None,
    ) -> str | None:
        validate_git_remote(repo_url)
        if not _is_sha(revision):
            raise ValueError("source revision must be a complete lowercase SHA")
        base = Path(settings.evidence_git_cache_dir).resolve()
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="revision-", dir=base) as temporary:
            root = Path(temporary).resolve()
            with _git_auth(credential) as environment:
                for arguments in (
                    ("init", "--quiet", str(root)),
                    ("-C", str(root), "remote", "add", "origin", repo_url),
                    (
                        "-C",
                        str(root),
                        "fetch",
                        "--quiet",
                        "--depth=1",
                        "--no-tags",
                        "origin",
                        revision,
                    ),
                ):
                    result = await _run_git(
                        *arguments,
                        environment=environment,
                        timeout_seconds=self.timeout_seconds,
                    )
                    if result.returncode != 0:
                        return None
            verified = await _run_git(
                "-C",
                str(root),
                "rev-parse",
                "FETCH_HEAD",
                environment={"GIT_TERMINAL_PROMPT": "0"},
                timeout_seconds=self.timeout_seconds,
            )
            return (
                revision
                if verified.returncode == 0 and verified.stdout.strip() == revision
                else None
            )


class GitSourceReader:
    """Fetch one SHA into a disposable worktree and derive server-owned excerpts."""

    def __init__(self, *, timeout_seconds: float | None = None) -> None:
        self.timeout_seconds = timeout_seconds or SOURCE_GIT_TIMEOUT_SECONDS

    async def collect(
        self,
        *,
        repo_url: str,
        revision: str,
        credential: GitCredentialMaterial | None,
        stack: str,
        query_terms: Sequence[str],
    ) -> tuple[GitSourceHit, ...]:
        validate_git_remote(repo_url)
        if not _is_sha(revision):
            raise ValueError("source revision must be a complete lowercase SHA")
        base = Path(settings.evidence_git_cache_dir).resolve()
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="source-", dir=base) as temporary:
            root = Path(temporary).resolve()
            if not root.is_relative_to(base):
                raise RuntimeError("temporary Git workspace escaped its configured root")
            await self._checkout(root, repo_url, revision, credential)
            maximum_files = SOURCE_MAX_FILES
            maximum_bytes = SOURCE_MAX_BYTES
            primary = stack_hits(
                root,
                stack,
                max_files=maximum_files,
                max_bytes=maximum_bytes,
            )
            remaining_files = max(0, maximum_files - len(primary))
            lexical = search_tree(
                root,
                list(query_terms),
                max_files=remaining_files,
                max_bytes=maximum_bytes,
                snippet_lines=max(12, SOURCE_SNIPPET_LINES * 4),
            )
            selected = [*primary, *lexical]
            remaining_files = max(0, maximum_files - len(selected))
            related = related_symbol_hits(
                root,
                selected,
                max_files=remaining_files,
                max_bytes=maximum_bytes,
            )
            return tuple(_source_hit(value) for value in (*selected, *related))

    async def _checkout(
        self,
        root: Path,
        repo_url: str,
        revision: str,
        credential: GitCredentialMaterial | None,
    ) -> None:
        with _git_auth(credential) as environment:
            for arguments in (
                ("init", "--quiet", str(root)),
                ("-C", str(root), "remote", "add", "origin", repo_url),
                (
                    "-C",
                    str(root),
                    "fetch",
                    "--quiet",
                    "--depth=1",
                    "--no-tags",
                    "origin",
                    revision,
                ),
                ("-C", str(root), "checkout", "--quiet", "--detach", "FETCH_HEAD"),
            ):
                result = await _run_git(
                    *arguments,
                    environment=environment,
                    timeout_seconds=self.timeout_seconds,
                )
                if result.returncode != 0:
                    raise GitSourceUnavailable(
                        f"Git read failed at {arguments[0]} ({result.returncode})"
                    )
        verified = await _run_git(
            "-C",
            str(root),
            "rev-parse",
            "HEAD",
            environment={"GIT_TERMINAL_PROMPT": "0"},
            timeout_seconds=self.timeout_seconds,
        )
        if verified.returncode != 0 or verified.stdout.strip() != revision:
            raise GitSourceUnavailable("Git checkout did not resolve to the requested SHA")


@dataclass(frozen=True, slots=True)
class _GitResult:
    returncode: int
    stdout: str


async def _run_git(
    *arguments: str,
    environment: Mapping[str, str],
    timeout_seconds: float,
) -> _GitResult:
    process = await asyncio.create_subprocess_exec(
        "git",
        "-c",
        "protocol.file.allow=always",
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, **environment},
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise GitSourceUnavailable("Git read timed out") from exc
    return _GitResult(process.returncode or 0, stdout.decode("utf-8", errors="replace"))


@contextmanager
def _git_auth(
    credential: GitCredentialMaterial | None,
) -> Iterator[dict[str, str]]:
    environment = {"GIT_TERMINAL_PROMPT": "0"}
    if credential is None:
        yield environment
        return
    if credential.auth_type == "https":
        with tempfile.TemporaryDirectory(prefix="lode-git-auth-") as temporary:
            askpass = Path(temporary) / "askpass"
            askpass.write_text(
                '#!/bin/sh\ncase "$1" in *Username*) printf \'%s\' "$LODE_GIT_USER" ;; '
                "*) printf '%s' \"$LODE_GIT_SECRET\" ;; esac\n",
                encoding="utf-8",
            )
            askpass.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            yield {
                **environment,
                "GIT_ASKPASS": str(askpass),
                "LODE_GIT_USER": credential.username,
                "LODE_GIT_SECRET": credential.secret,
            }
        return
    if credential.auth_type == "ssh":
        with tempfile.TemporaryDirectory(prefix="lode-git-auth-") as temporary:
            key = Path(temporary) / "identity"
            key.write_text(credential.secret, encoding="utf-8")
            key.chmod(stat.S_IRUSR | stat.S_IWUSR)
            yield {
                **environment,
                "GIT_SSH_COMMAND": (
                    f"ssh -i {key} -o IdentitiesOnly=yes -o BatchMode=yes "
                    "-o StrictHostKeyChecking=yes"
                ),
            }
        return
    raise ValueError("unsupported Git credential type")


def validate_git_remote(repo_url: str) -> None:
    value = repo_url.strip()
    parsed = urlsplit(value)
    is_scp = value.startswith("git@") and ":" in value
    is_local = parsed.scheme == "file" or (not parsed.scheme and Path(value).is_absolute())
    if parsed.scheme not in {"https", "ssh", "file"} and not is_scp and not is_local:
        raise ValueError("Git remote scheme is not allowed")
    if parsed.scheme == "https" and (parsed.username is not None or parsed.password is not None):
        raise ValueError("Git credentials must not be embedded in repository URLs")


def _validate_branch(branch: str) -> None:
    if (
        not branch
        or branch.startswith("-")
        or ".." in branch
        or branch.endswith("/")
        or any(character.isspace() for character in branch)
    ):
        raise ValueError("Git branch name is invalid")


def _is_sha(value: str) -> bool:
    return len(value) == 40 and all(character in "0123456789abcdef" for character in value)


def _source_hit(value: Mapping[str, object]) -> GitSourceHit:
    path = str(value["path"])
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in path:
        raise GitSourceUnavailable("Git evidence path is outside the repository")
    return GitSourceHit(
        path=path,
        symbol=str(value["symbol"]) if value.get("symbol") else None,
        start_line=int(value["snippet_start_line"]),
        end_line=int(value["snippet_end_line"]),
        content=str(value["snippet"]),
        selection_reason=str(value["selection_reason"]),
    )
