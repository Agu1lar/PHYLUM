"""Sandbox Executor: controlled execution of dynamic Python and PowerShell scripts.

Provides an isolated execution environment with:
- Temp directory per execution for file I/O
- Timeout enforcement
- Output capture (stdout, stderr, return code)
- Optional working directory override
- Cancellation support
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import textwrap
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SANDBOX_ROOT = Path(tempfile.gettempdir()) / "agente_sandbox"
MAX_SCRIPT_LENGTH = 64_000
MAX_OUTPUT_LENGTH = 256_000
DEFAULT_TIMEOUT = 60


def _ensure_sandbox_root() -> Path:
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    return SANDBOX_ROOT


class SandboxResult:
    __slots__ = ("ok", "stdout", "stderr", "returncode", "script_path", "work_dir", "artifacts", "error")

    def __init__(
        self,
        *,
        ok: bool,
        stdout: str = "",
        stderr: str = "",
        returncode: Optional[int] = None,
        script_path: Optional[str] = None,
        work_dir: Optional[str] = None,
        artifacts: Optional[List[str]] = None,
        error: Optional[str] = None,
    ):
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.script_path = script_path
        self.work_dir = work_dir
        self.artifacts = artifacts or []
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "stdout": self.stdout[:MAX_OUTPUT_LENGTH],
            "stderr": self.stderr[:MAX_OUTPUT_LENGTH],
            "returncode": self.returncode,
            "script_path": self.script_path,
            "work_dir": self.work_dir,
            "artifacts": self.artifacts,
            "error": self.error,
        }


class SandboxExecutor:
    def __init__(self, *, root: Optional[Path] = None, default_timeout: int = DEFAULT_TIMEOUT):
        self.root = root or SANDBOX_ROOT
        self.default_timeout = default_timeout

    def _create_work_dir(self) -> Path:
        _ensure_sandbox_root()
        work_dir = self.root / f"run_{uuid.uuid4().hex[:12]}"
        work_dir.mkdir(parents=True, exist_ok=True)
        return work_dir

    async def execute_python(
        self,
        code: str,
        *,
        timeout: Optional[int] = None,
        work_dir: Optional[str] = None,
        cancel_event: Optional[asyncio.Event] = None,
        input_files: Optional[Dict[str, str]] = None,
    ) -> SandboxResult:
        if len(code) > MAX_SCRIPT_LENGTH:
            return SandboxResult(ok=False, error=f"Script exceeds maximum length of {MAX_SCRIPT_LENGTH} characters")

        effective_timeout = timeout or self.default_timeout
        sandbox_dir = Path(work_dir) if work_dir else self._create_work_dir()
        sandbox_dir.mkdir(parents=True, exist_ok=True)

        if input_files:
            for name, content in input_files.items():
                (sandbox_dir / name).write_text(content, encoding="utf-8")

        script_path = sandbox_dir / f"script_{uuid.uuid4().hex[:8]}.py"
        script_path.write_text(code, encoding="utf-8")

        return await self._run_process(
            ["python", str(script_path)],
            work_dir=sandbox_dir,
            timeout=effective_timeout,
            cancel_event=cancel_event,
            script_path=str(script_path),
        )

    async def execute_powershell(
        self,
        code: str,
        *,
        timeout: Optional[int] = None,
        work_dir: Optional[str] = None,
        cancel_event: Optional[asyncio.Event] = None,
        input_files: Optional[Dict[str, str]] = None,
    ) -> SandboxResult:
        if len(code) > MAX_SCRIPT_LENGTH:
            return SandboxResult(ok=False, error=f"Script exceeds maximum length of {MAX_SCRIPT_LENGTH} characters")

        effective_timeout = timeout or self.default_timeout
        sandbox_dir = Path(work_dir) if work_dir else self._create_work_dir()
        sandbox_dir.mkdir(parents=True, exist_ok=True)

        if input_files:
            for name, content in input_files.items():
                (sandbox_dir / name).write_text(content, encoding="utf-8")

        script_path = sandbox_dir / f"script_{uuid.uuid4().hex[:8]}.ps1"
        script_path.write_text(code, encoding="utf-8")

        return await self._run_process(
            ["powershell", "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", str(script_path)],
            work_dir=sandbox_dir,
            timeout=effective_timeout,
            cancel_event=cancel_event,
            script_path=str(script_path),
        )

    async def _run_process(
        self,
        cmd: List[str],
        *,
        work_dir: Path,
        timeout: int,
        cancel_event: Optional[asyncio.Event],
        script_path: str,
    ) -> SandboxResult:
        env = dict(os.environ)
        env["AGENTE_SANDBOX"] = "1"
        env["AGENTE_SANDBOX_DIR"] = str(work_dir)

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(work_dir),
                env=env,
            )
        except FileNotFoundError as exc:
            return SandboxResult(ok=False, error=f"Interpreter not found: {exc}", script_path=script_path, work_dir=str(work_dir))
        except Exception as exc:
            return SandboxResult(ok=False, error=str(exc), script_path=script_path, work_dir=str(work_dir))

        try:
            if cancel_event is not None:
                cancel_task = asyncio.create_task(cancel_event.wait())
                comm_task = asyncio.create_task(process.communicate())
                done, pending = await asyncio.wait(
                    {cancel_task, comm_task},
                    timeout=timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for p in pending:
                    p.cancel()
                if cancel_task in done:
                    process.kill()
                    return SandboxResult(ok=False, error="cancelled", returncode=-1, script_path=script_path, work_dir=str(work_dir))
                if comm_task in done:
                    stdout_bytes, stderr_bytes = comm_task.result()
                else:
                    process.kill()
                    return SandboxResult(ok=False, error="timeout", returncode=-1, script_path=script_path, work_dir=str(work_dir))
            else:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            return SandboxResult(ok=False, error="timeout", returncode=-1, script_path=script_path, work_dir=str(work_dir))

        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        artifacts = self._collect_artifacts(work_dir, script_path)

        return SandboxResult(
            ok=process.returncode == 0,
            stdout=stdout,
            stderr=stderr,
            returncode=process.returncode,
            script_path=script_path,
            work_dir=str(work_dir),
            artifacts=artifacts,
        )

    def _collect_artifacts(self, work_dir: Path, script_path: str) -> List[str]:
        artifacts: List[str] = []
        try:
            script_name = Path(script_path).name
            for item in work_dir.iterdir():
                if item.name == script_name:
                    continue
                artifacts.append(str(item))
        except Exception:
            pass
        return artifacts
