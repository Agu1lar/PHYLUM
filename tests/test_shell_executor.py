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


@pytest.mark.asyncio
async def test_shell_executor_cancellation_sets_cancelled():
    exec = ShellExecutor(default_retries=1)
    cancel_event = asyncio.Event()
    cmd = f"{sys.executable} -c \"import time; time.sleep(30)\""

    task = asyncio.create_task(
        exec.execute(
            cmd,
            shell='powershell' if sys.platform.startswith('win') else 'cmd',
            timeout=60,
            retries=1,
            cancel_event=cancel_event,
        )
    )
    await asyncio.sleep(0.3)
    cancel_event.set()
    resp = await asyncio.wait_for(task, timeout=10)

    assert resp.cancelled is True
    assert resp.error == 'cancelled'
