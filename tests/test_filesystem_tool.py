import asyncio
import pytest
from tool_filesystem import FileSystemTool
from pathlib import Path


@pytest.mark.asyncio
async def test_filesystem_write_and_read(tmp_path):
    tool = FileSystemTool(default_timeout=5, default_retries=1)
    file = tmp_path / "test.txt"
    payload = {"action": "write", "path": str(file), "content": "hello world", "backup": False}
    res = await tool.run(payload)
    assert res.status == "succeeded"
    # read back
    payload2 = {"action": "read", "path": str(file)}
    res2 = await tool.run(payload2)
    assert res2.status == "succeeded"
    assert 'hello world' in res2.data.get('content')
