import asyncio
import logging
import subprocess
import shlex
import time
from typing import Optional, Dict, Any, List

from models import StructuredResponse, ExecutionMeta, CommandResult, ExecutionRisk
from command_validator import CommandValidator
from risk_classifier import classify
from permission_layer import ensure_permissions, is_admin

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class ShellExecutor:
    def __init__(self, *, blacklist: Optional[List[str]] = None, whitelist: Optional[List[str]] = None, default_retries: int = 2):
        self.validator = CommandValidator(blacklist=blacklist, whitelist=whitelist)
        self.default_retries = default_retries

    async def _terminate_process_tree(self, pid: Optional[int]) -> None:
        if not pid:
            return
        try:
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
                text=True,
            )
        except Exception:
            logger.exception("Error terminating process tree for pid=%s", pid)

    async def _spawn(self, cmd_list: List[str], timeout: int, cancel_event: Optional[asyncio.Event] = None) -> Dict[str, Any]:
        logger.debug("Spawning process: %s", cmd_list)
        start = time.time()
        proc = await asyncio.create_subprocess_exec(*cmd_list, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        pid = proc.pid

        # cancellation monitor
        async def _cancel_watcher():
            if cancel_event is None:
                return
            await cancel_event.wait()
            try:
                logger.info("Cancellation requested - terminating pid %s", pid)
                await self._terminate_process_tree(pid)
            except ProcessLookupError:
                logger.warning("Process already exited")

        cancel_task = None
        if cancel_event is not None:
            cancel_task = asyncio.create_task(_cancel_watcher())

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            duration = time.time() - start
            stdout = stdout_bytes.decode(errors='ignore') if stdout_bytes else ''
            stderr = stderr_bytes.decode(errors='ignore') if stderr_bytes else ''
            logger.debug("Process %s exited with %s", pid, proc.returncode)
            cancelled = bool(cancel_event and cancel_event.is_set())
            return {
                "stdout": stdout,
                "stderr": stderr,
                "returncode": proc.returncode,
                "pid": pid,
                "duration": duration,
                "cancelled": cancelled,
                "timed_out": False,
            }
        except asyncio.TimeoutError:
            logger.warning("Process %s timed out after %s seconds - killing", pid, timeout)
            try:
                await self._terminate_process_tree(pid)
            except Exception:
                logger.exception("Error killing process %s", pid)
            duration = time.time() - start
            return {
                "stdout": '',
                "stderr": 'timeout',
                "returncode": -1,
                "pid": pid,
                "duration": duration,
                "cancelled": False,
                "timed_out": True,
            }
        except asyncio.CancelledError:
            logger.warning("Process %s cancelled by asyncio task", pid)
            await self._terminate_process_tree(pid)
            duration = time.time() - start
            raise asyncio.CancelledError() from None
        finally:
            if cancel_task is not None:
                cancel_task.cancel()

    async def execute(self, command: str, *, shell: str = 'powershell', timeout: int = 30, retries: Optional[int] = None, require_admin: bool = False, cancel_event: Optional[asyncio.Event] = None, allow_protected_paths: bool = False) -> StructuredResponse:
        """Execute a command safely on Windows. command is a string.
        shell: 'powershell' or 'cmd'
        timeout: mandatory per-attempt timeout in seconds
        retries: number of attempts (>=1)
        require_admin: whether admin privileges are required (will wrap with Start-Process -Verb RunAs)
        cancel_event: asyncio.Event that when set will attempt to terminate running process
        """
        if timeout is None or timeout <= 0:
            raise ValueError("timeout is mandatory and must be > 0")
        retries = retries if retries is not None else self.default_retries

        # Validate command
        allowed, reason = self.validator.validate(command)
        classification = classify(command)

        meta = ExecutionMeta(attempted_at=__import__('datetime').datetime.utcnow(), attempt=0, retries=retries, timeout_seconds=timeout, shell=shell, command=command, allowed=allowed, admin_requested=require_admin, admin_granted=False)

        if not allowed:
            logger.warning("Command validation failed: %s", reason)
            resp = StructuredResponse(ok=False, meta=meta, result=None, risk=ExecutionRisk(level=classification['level'], tags=classification['tags'], reason=classification['reason']), error=f"validation: {reason}", cancelled=False)
            return resp

        last_err = None
        for attempt in range(1, retries + 1):
            meta.attempt = attempt
            try:
                cmd_list, elevated = await ensure_permissions(command, require_admin=require_admin, shell=shell)
                meta.admin_granted = elevated or (require_admin and is_admin())
                logger.info("Executing attempt %s/%s: %s (shell=%s elevated=%s)", attempt, retries, command, shell, meta.admin_granted)
                result = await self._spawn(cmd_list, timeout=timeout, cancel_event=cancel_event)
                res_model = CommandResult(stdout=result['stdout'], stderr=result['stderr'], returncode=result['returncode'], duration_seconds=result['duration'], pid=result.get('pid'))
                timed_out = bool(result.get("timed_out"))
                cancelled = bool(result.get("cancelled"))
                ok = result['returncode'] == 0 and not timed_out and not cancelled
                error = None
                if cancelled:
                    error = 'cancelled'
                elif timed_out:
                    error = 'timeout'
                elif not ok:
                    error = 'non-zero-exit'
                structured = StructuredResponse(
                    ok=ok,
                    meta=meta,
                    result=res_model,
                    risk=ExecutionRisk(level=classification['level'], tags=classification['tags'], reason=classification['reason']),
                    error=error,
                    cancelled=cancelled,
                    raw=result,
                )
                logger.info("Execution finished (attempt %s) pid=%s rc=%s duration=%.2fs", attempt, result.get('pid'), result.get('returncode'), result.get('duration'))
                if ok or cancelled:
                    return structured
                if timed_out:
                    last_err = structured
                    await asyncio.sleep(min(2 ** attempt, 10))
                    continue
                else:
                    last_err = structured
                    # backoff before retry
                    await asyncio.sleep(min(2 ** attempt, 10))
            except asyncio.CancelledError:
                logger.warning("Execution cancelled by asyncio.CancelledError")
                return StructuredResponse(
                    ok=False,
                    meta=meta,
                    result=None,
                    risk=ExecutionRisk(level=classification['level'], tags=classification['tags'], reason=classification['reason']),
                    error='cancelled',
                    cancelled=True,
                    raw={"cancelled": True, "timed_out": False},
                )
            except Exception as exc:
                logger.exception("Execution attempt %s failed: %s", attempt, exc)
                last_err = StructuredResponse(ok=False, meta=meta, result=None, risk=ExecutionRisk(level=classification['level'], tags=classification['tags'], reason=classification['reason']), error=str(exc), cancelled=False)
                await asyncio.sleep(min(2 ** attempt, 10))

        # all retries exhausted
        logger.error("All %s attempts failed for command: %s", retries, command)
        return last_err or StructuredResponse(ok=False, meta=meta, result=None, risk=ExecutionRisk(level=classification['level'], tags=classification['tags'], reason=classification['reason']), error='unknown', cancelled=False)


# Convenience sync wrapper
def run_sync(command: str, **kwargs) -> StructuredResponse:
    loop = asyncio.get_event_loop()
    exec = ShellExecutor()
    return loop.run_until_complete(exec.execute(command, **kwargs))
