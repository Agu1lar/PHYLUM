import asyncio
import pytest
import sys
from shell_executor import ShellExecutor


@pytest.mark.asyncio
async def test_shell_executor_echo():
    exec = ShellExecutor(default_retries=1)
    # Use python one-liner to be cross-platform
    cmd = f"{sys.executable} -c \"print('hello_test')\""
    resp = await exec.execute(cmd, shell='powershell' if sys.platform.startswith('win') else 'cmd', timeout=10, retries=1)
    assert resp.result is not None
    out = resp.result.stdout
    assert 'hello_test' in out
