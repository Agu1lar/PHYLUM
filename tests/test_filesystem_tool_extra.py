import pytest
from pathlib import Path
from tool_filesystem import FileSystemTool


@pytest.mark.asyncio
async def test_filesystem_mkdir_and_move(tmp_path):
    tool = FileSystemTool(default_timeout=5, default_retries=1)
    d = tmp_path / "a"
    payload = {"action": "mkdir", "path": str(d)}
    res = await tool.run(payload)
    assert res.status == "succeeded"
    # create a file and move
    f = d / "f.txt"
    f.write_text("hi")
    dest = tmp_path / "b" / "f.txt"
    payload_mv = {"action": "move", "path": str(f), "dest": str(dest), "backup": False}
    res2 = await tool.run(payload_mv)
    assert res2.status == "succeeded"
    assert dest.exists()


@pytest.mark.asyncio
async def test_filesystem_allows_readonly_unc_paths():
    tool = FileSystemTool(default_timeout=5, default_retries=1)

    await tool.validate(tool.InputModel(action="list", path=r"\\servidor-2\share"))
    await tool.validate(tool.InputModel(action="stat", path=r"\\servidor-2\share"))


@pytest.mark.asyncio
async def test_filesystem_blocks_mutating_unc_paths():
    tool = FileSystemTool(default_timeout=5, default_retries=1)

    with pytest.raises(ValueError, match="path not allowed by sandbox"):
        await tool.validate(tool.InputModel(action="write", path=r"\\servidor-2\share\file.txt", content="x"))


@pytest.mark.asyncio
async def test_filesystem_allows_outside_sandbox_when_explicitly_approved():
    tool = FileSystemTool(default_timeout=5, default_retries=1)

    await tool.validate(tool.InputModel(action="list", path=r"C:\Windows", allow_outside_sandbox=True))
