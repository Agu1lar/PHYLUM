import sys
import pytest
from shell_executor import ShellExecutor


@pytest.mark.asyncio
async def test_shell_timeout_behavior():
    exec = ShellExecutor(default_retries=1)
    # short sleeping command
    cmd = f"{sys.executable} -c \"import time; time.sleep(2)\""
    resp = await exec.execute(cmd, shell='powershell' if sys.platform.startswith('win') else 'cmd', timeout=1, retries=1)
    assert resp.cancelled or (resp.result and resp.result.returncode == -1)
