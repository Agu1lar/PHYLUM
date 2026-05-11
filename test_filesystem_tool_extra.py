import pytest
from pathlib import Path
from tool_filesystem import FileSystemTool


@pytest.mark.asyncio
async def test_filesystem_mkdir_and_move(tmp_path):
    tool = FileSystemTool(default_timeout=5, default_retries=1)
    d = tmp_path / "a"
    payload = {"action": "mkdir", "path": str(d)}
    res = await tool.run(payload)
    assert res.success
    # create a file and move
    f = d / "f.txt"
    f.write_text("hi")
    dest = tmp_path / "b" / "f.txt"
    payload_mv = {"action": "move", "path": str(f), "dest": str(dest), "backup": False}
    res2 = await tool.run(payload_mv)
    assert res2.success
    assert dest.exists()
