"""Fail-closed command execution inside the dedicated runner container."""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from time import monotonic

from lode.command_runner.protocol import RunnerAction, RunnerResult
from lode.evidence_connectors.safety import sanitize_evidence
from lode.evidence_connectors.types import ProviderExecutionError

_BWRAP = "/usr/bin/bwrap"


class RunnerRejected(RuntimeError):
    pass


class CommandExecutor:
    def __init__(
        self,
        *,
        worksets_root: str = "/worksets",
        executable_path: str = "/usr/bin/rg",
        bubblewrap_path: str = _BWRAP,
        use_bubblewrap: bool = True,
    ) -> None:
        self.worksets_root = Path(worksets_root).resolve()
        self.executable_path = Path(executable_path)
        self.bubblewrap_path = Path(bubblewrap_path)
        self.use_bubblewrap = use_bubblewrap

    async def preflight(self, action: RunnerAction) -> dict[str, object]:
        executable, root, file_path = self._validate(action)
        return {
            "profile": action.profile,
            "binary_sha256": self._sha256(executable),
            "working_root": action.working_root,
            "file": str(file_path.relative_to(root)),
            "network": "none",
            "filesystem": "read_only",
        }

    async def execute(self, action: RunnerAction) -> RunnerResult:
        try:
            executable, root, file_path = self._validate(action)
        except RunnerRejected:
            return RunnerResult(
                status="failed",
                exit_code=None,
                stdout="",
                stderr="",
                output_bytes=0,
                duration_ms=0,
                failure_code="sandbox_violation",
                secret_categories=[],
                prompt_injection_detected=False,
                output_sha256=None,
            )
        try:
            command = self._sandbox_command(action, executable, root, file_path)
        except RunnerRejected:
            return self._failure(monotonic(), "sandbox_violation")
        started = monotonic()
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(root),
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": "/nonexistent",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "PAGER": "cat",
                "GIT_PAGER": "cat",
                "LANG": "C.UTF-8",
            },
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
        )
        try:
            async with asyncio.timeout(action.timeout_ms / 1_000):
                stdout, stderr = await self._communicate_bounded(process, action.output_bytes)
        except TimeoutError:
            process.kill()
            await process.wait()
            return self._failure(started, "provider_timeout")
        except RunnerRejected:
            process.kill()
            await process.wait()
            return self._failure(started, "cost_exceeded")
        exit_code = await process.wait()
        duration_ms = max(0, int((monotonic() - started) * 1_000))
        if exit_code not in {0, 1} or stderr:
            return RunnerResult(
                status="failed",
                exit_code=exit_code,
                stdout="",
                stderr="",
                output_bytes=0,
                duration_ms=duration_ms,
                failure_code="sandbox_violation" if self.use_bubblewrap else "invalid_response",
                secret_categories=[],
                prompt_injection_detected=False,
                output_sha256=None,
            )
        try:
            stdout_text = stdout.decode("utf-8", errors="strict")
            stderr_text = stderr.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return self._failure(started, "invalid_response", exit_code=exit_code)
        try:
            sanitized, categories, injection = sanitize_evidence(
                {"stdout": stdout_text, "stderr": stderr_text}
            )
        except ProviderExecutionError:
            return self._failure(started, "invalid_response", exit_code=exit_code)
        if {"aws_access_key", "private_key"} & set(categories):
            return RunnerResult(
                status="failed",
                exit_code=exit_code,
                stdout="",
                stderr="",
                output_bytes=0,
                duration_ms=duration_ms,
                failure_code="invalid_response",
                secret_categories=list(categories),
                prompt_injection_detected=injection,
                output_sha256=hashlib.sha256(stdout + b"\0" + stderr).hexdigest(),
            )
        return RunnerResult(
            status="succeeded",
            exit_code=exit_code,
            stdout=sanitized["stdout"],
            stderr=sanitized["stderr"],
            output_bytes=len(stdout) + len(stderr),
            duration_ms=duration_ms,
            failure_code=None,
            secret_categories=list(categories),
            prompt_injection_detected=injection,
            output_sha256=None,
        )

    def catalog(self) -> dict[str, object]:
        executable = self.executable_path
        if not executable.is_file() or executable.is_symlink():
            raise RunnerRejected("runner binary is unavailable")
        return {
            "runner_version": "isolated-command-runner.1",
            "profiles": {
                "rg_fixed_search": {
                    "executable": "/usr/bin/rg",
                    "binary_sha256": self._sha256(executable),
                }
            },
        }

    def _validate(self, action: RunnerAction) -> tuple[Path, Path, Path]:
        executable = self.executable_path
        if not executable.is_file() or executable.is_symlink():
            raise RunnerRejected("runner executable is invalid")
        if self._sha256(executable) != action.binary_sha256:
            raise RunnerRejected("runner executable hash changed")
        if action.working_root != f"/worksets/{action.working_set_id}":
            raise RunnerRejected("runner working root is invalid")
        root = self.worksets_root / action.working_set_id
        try:
            resolved_root = root.resolve(strict=True)
        except OSError as exc:
            raise RunnerRejected("runner working root is unavailable") from exc
        if resolved_root.parent != self.worksets_root or root.is_symlink():
            raise RunnerRejected("runner working root escaped")
        maximum = re.fullmatch(r"--max-count=([1-9][0-9]{0,3})", action.argv[5])
        if (
            action.argv[:5]
            != [
                "--fixed-strings",
                "--line-number",
                "--no-heading",
                "--color=never",
                "--no-config",
            ]
            or maximum is None
            or int(maximum.group(1)) != action.result_limit
            or action.argv[6] != "--"
        ):
            raise RunnerRejected("runner argv grammar changed")
        if action.argv[8:] != action.allowed_files or len(action.allowed_files) != 1:
            raise RunnerRejected("runner file list changed")
        relative = Path(action.allowed_files[0])
        if relative.is_absolute() or ".." in relative.parts:
            raise RunnerRejected("runner file path is invalid")
        cursor = resolved_root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise RunnerRejected("runner file path contains a symlink")
        try:
            file_path = cursor.resolve(strict=True)
        except OSError as exc:
            raise RunnerRejected("runner file is unavailable") from exc
        if not file_path.is_file() or not file_path.is_relative_to(resolved_root):
            raise RunnerRejected("runner file escaped working set")
        return executable, resolved_root, file_path

    def _sandbox_command(
        self, action: RunnerAction, executable: Path, root: Path, file_path: Path
    ) -> list[str]:
        if not self.use_bubblewrap:
            return [str(executable), *action.argv]
        if not self.bubblewrap_path.is_file() or self.bubblewrap_path.is_symlink():
            raise RunnerRejected("bubblewrap sandbox is unavailable")
        relative = file_path.relative_to(root)
        command = [
            str(self.bubblewrap_path),
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--ro-bind",
            "/usr",
            "/usr",
            "--symlink",
            "usr/bin",
            "/bin",
            "--symlink",
            "usr/lib",
            "/lib",
            "--dir",
            "/workspace",
        ]
        cursor = Path("/workspace")
        for part in relative.parent.parts:
            cursor /= part
            command.extend(("--dir", str(cursor)))
        command.extend(
            (
                "--ro-bind",
                str(file_path),
                str(Path("/workspace") / relative),
                "--chdir",
                "/workspace",
                "--clearenv",
                "--setenv",
                "PATH",
                "/usr/bin:/bin",
                str(executable),
                *action.argv,
            )
        )
        return command

    @staticmethod
    async def _communicate_bounded(
        process: asyncio.subprocess.Process, maximum: int
    ) -> tuple[bytes, bytes]:
        async def read(stream: asyncio.StreamReader | None) -> bytes:
            if stream is None:
                return b""
            chunks: list[bytes] = []
            size = 0
            while chunk := await stream.read(16_384):
                size += len(chunk)
                if size > maximum:
                    raise RunnerRejected("runner output exceeded budget")
                chunks.append(chunk)
            return b"".join(chunks)

        stdout, stderr = await asyncio.gather(read(process.stdout), read(process.stderr))
        if len(stdout) + len(stderr) > maximum:
            raise RunnerRejected("runner output exceeded budget")
        return stdout, stderr

    @staticmethod
    def _failure(
        started: float, failure_code: str, *, exit_code: int | None = None
    ) -> RunnerResult:
        return RunnerResult(
            status="failed",
            exit_code=exit_code,
            stdout="",
            stderr="",
            output_bytes=0,
            duration_ms=max(0, int((monotonic() - started) * 1_000)),
            failure_code=failure_code,
            secret_categories=[],
            prompt_injection_detected=False,
            output_sha256=None,
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
