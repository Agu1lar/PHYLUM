import asyncio
import logging
from typing import Tuple, List

logger = logging.getLogger(__name__)


def is_windows() -> bool:
    import platform
    return platform.system().lower() == 'windows'


def is_admin() -> bool:
    try:
        if not is_windows():
            return False
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        logger.exception("is_admin check failed")
        return False


async def prepare_elevated_command(command: str, shell: str = 'powershell') -> Tuple[List[str], bool]:
    """Return a command list suitable for create_subprocess_exec that will prompt for elevation.
    The function does not automatically bypass UAC. It constructs a PowerShell Start-Process invocation.
    Returns (cmd_list, is_powered) where is_powered indicates if elevation wrapper was used.
    """
    if not is_windows():
        # non-windows no-op
        if shell == 'powershell':
            return (['pwsh', '-NoProfile', '-NonInteractive', '-Command', command], False)
        return (['/bin/sh', '-c', command], False)

    # Use powershell Start-Process -Verb RunAs to trigger elevation
    # Construct single-quoted command escaping single quotes
    safe_command = command.replace("'", "''")
    ps_cmd = f"Start-Process -FilePath '{'powershell.exe' if shell=='powershell' else 'cmd.exe'}' -ArgumentList '-NoProfile','-NonInteractive','-Command','{safe_command}' -Verb RunAs"
    # Run via powershell exe
    return (['powershell', '-NoProfile', '-NonInteractive', '-Command', ps_cmd], True)


async def ensure_permissions(command: str, require_admin: bool = False, shell: str = 'powershell') -> Tuple[List[str], bool]:
    """Return (cmd_list, elevated_flag). If require_admin and not admin, prepare wrapper to prompt UAC.
    """
    if require_admin:
        if is_admin():
            # already admin, run normally
            if shell == 'powershell':
                return (['powershell', '-NoProfile', '-NonInteractive', '-Command', command], False)
            return (['cmd', '/C', command], False)
        else:
            return await prepare_elevated_command(command, shell)
    else:
        if shell == 'powershell':
            return (['powershell', '-NoProfile', '-NonInteractive', '-Command', command], False)
        return (['cmd', '/C', command], False)
