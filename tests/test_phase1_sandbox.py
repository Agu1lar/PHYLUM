"""Tests for Phase 1: Sandbox Executor, Artifact Processor, Dynamic Tool Creator."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# --- Sandbox Executor ---

from sandbox_executor import SandboxExecutor, SandboxResult


@pytest.fixture
def sandbox():
    tmp = Path(tempfile.mkdtemp(prefix="agente_test_sandbox_"))
    return SandboxExecutor(root=tmp)


@pytest.mark.asyncio
async def test_sandbox_python_hello(sandbox):
    result = await sandbox.execute_python("print('hello from sandbox')")
    assert result.ok is True
    assert "hello from sandbox" in result.stdout
    assert result.returncode == 0


@pytest.mark.asyncio
async def test_sandbox_python_error(sandbox):
    result = await sandbox.execute_python("raise ValueError('boom')")
    assert result.ok is False
    assert result.returncode != 0
    assert "boom" in result.stderr


@pytest.mark.asyncio
async def test_sandbox_python_input_files(sandbox):
    code = "import json; data = json.load(open('data.json')); print(data['key'])"
    input_files = {"data.json": json.dumps({"key": "value123"})}
    result = await sandbox.execute_python(code, input_files=input_files)
    assert result.ok is True
    assert "value123" in result.stdout


@pytest.mark.asyncio
async def test_sandbox_python_timeout(sandbox):
    result = await sandbox.execute_python("import time; time.sleep(60)", timeout=1)
    assert result.ok is False
    assert result.error == "timeout"


@pytest.mark.asyncio
async def test_sandbox_python_artifacts(sandbox):
    code = "with open('output.txt', 'w') as f: f.write('artifact content')"
    result = await sandbox.execute_python(code)
    assert result.ok is True
    assert any("output.txt" in a for a in result.artifacts)


@pytest.mark.asyncio
async def test_sandbox_python_cancel(sandbox):
    cancel = asyncio.Event()
    cancel.set()
    result = await sandbox.execute_python("import time; time.sleep(30)", cancel_event=cancel)
    assert result.ok is False


@pytest.mark.asyncio
async def test_sandbox_python_max_length(sandbox):
    result = await sandbox.execute_python("x" * 100_000)
    assert result.ok is False
    assert "maximum length" in result.error


@pytest.mark.asyncio
async def test_sandbox_result_to_dict(sandbox):
    result = await sandbox.execute_python("print(42)")
    d = result.to_dict()
    assert "ok" in d
    assert "stdout" in d
    assert "stderr" in d
    assert "returncode" in d


# --- Sandbox Tool ---

from tool_sandbox import SandboxTool


@pytest.fixture
def sandbox_tool():
    return SandboxTool()


@pytest.mark.asyncio
async def test_sandbox_tool_python(sandbox_tool):
    result = await sandbox_tool.run({"action": "execute_python", "code": "print('tool ok')"})
    assert result.ok is True
    assert result.success is True
    assert "tool ok" in result.message


@pytest.mark.asyncio
async def test_sandbox_tool_invalid_action(sandbox_tool):
    with pytest.raises(Exception):
        await sandbox_tool.run({"action": "invalid_action", "code": "x"})


# --- Artifact Processor ---

from artifact_processor import ArtifactProcessor


@pytest.fixture
def processor():
    return ArtifactProcessor()


@pytest.fixture
def text_file(tmp_path):
    p = tmp_path / "test.txt"
    p.write_text("line 1\nline 2\nline 3\nhello world\n", encoding="utf-8")
    return str(p)


@pytest.fixture
def csv_file(tmp_path):
    p = tmp_path / "data.csv"
    p.write_text("name,age,city\nAlice,30,SP\nBob,25,RJ\n", encoding="utf-8")
    return str(p)


@pytest.fixture
def json_file(tmp_path):
    p = tmp_path / "data.json"
    p.write_text(json.dumps({"items": [1, 2, 3], "count": 3}), encoding="utf-8")
    return str(p)


@pytest.mark.asyncio
async def test_artifact_load_text(processor, text_file):
    result = await processor.load_and_read(text_file)
    assert result.ok is True
    assert "line 1" in result.data
    assert result.artifact_type == "text"


@pytest.mark.asyncio
async def test_artifact_load_csv(processor, csv_file):
    result = await processor.load_and_read(csv_file)
    assert result.ok is True
    assert result.artifact_type == "tabular"
    assert result.data["total_rows"] == 2


@pytest.mark.asyncio
async def test_artifact_load_json(processor, json_file):
    result = await processor.load_and_read(json_file)
    assert result.ok is True
    assert result.artifact_type == "json"


@pytest.mark.asyncio
async def test_artifact_load_not_found(processor):
    result = await processor.load_and_read("/nonexistent/file.txt")
    assert result.ok is False
    assert "not found" in result.error.lower()


@pytest.mark.asyncio
async def test_artifact_transform_summarize(processor, text_file):
    result = await processor.transform(text_file, "summarize")
    assert result.ok is True
    assert result.artifact_type == "summary"


@pytest.mark.asyncio
async def test_artifact_transform_filter_lines(processor, text_file):
    result = await processor.transform(text_file, "filter_lines", {"pattern": "hello"})
    assert result.ok is True
    assert "hello world" in result.data


@pytest.mark.asyncio
async def test_artifact_transform_stats(processor, text_file):
    result = await processor.transform(text_file, "stats")
    assert result.ok is True
    assert "chars" in result.data


@pytest.mark.asyncio
async def test_artifact_write_result(processor, tmp_path):
    out = str(tmp_path / "output.txt")
    result = await processor.write_result("hello output", out)
    assert result.ok is True
    assert Path(out).read_text() == "hello output"


@pytest.mark.asyncio
async def test_artifact_transform_convert_json(processor, json_file):
    result = await processor.transform(json_file, "convert_json")
    assert result.ok is True


# --- Artifact Tool ---

from tool_artifact import ArtifactTool


@pytest.fixture
def artifact_tool():
    return ArtifactTool()


@pytest.mark.asyncio
async def test_artifact_tool_load(artifact_tool, text_file):
    result = await artifact_tool.run({"action": "load", "path": text_file})
    assert result.ok is True


@pytest.mark.asyncio
async def test_artifact_tool_transform(artifact_tool, text_file):
    result = await artifact_tool.run({"action": "transform", "path": text_file, "operation": "stats"})
    assert result.ok is True


@pytest.mark.asyncio
async def test_artifact_tool_missing_path(artifact_tool):
    with pytest.raises(Exception):
        await artifact_tool.run({"action": "load"})


# --- Dynamic Tool Creator ---

from dynamic_tool_creator import DynamicToolCreator


@pytest.fixture
def creator(tmp_path):
    return DynamicToolCreator(tools_dir=tmp_path / "dynamic_tools")


@pytest.mark.asyncio
async def test_dynamic_create_and_execute(creator):
    spec = await creator.create_tool(
        name="adder",
        description="Adds two numbers",
        code="def run(params):\n    return params.get('a', 0) + params.get('b', 0)\n",
    )
    assert spec.name == "adder"
    assert spec.tool_id.startswith("dyn_")

    result = await creator.execute_tool(spec.tool_id, params={"a": 3, "b": 5})
    assert result.ok is True
    assert result.output == 8


@pytest.mark.asyncio
async def test_dynamic_list_tools(creator):
    await creator.create_tool(name="t1", description="", code="def run(p): pass\n")
    await creator.create_tool(name="t2", description="", code="def run(p): pass\n", tags=["math"])
    tools = await creator.list_tools()
    assert len(tools) == 2
    tools_math = await creator.list_tools(tag="math")
    assert len(tools_math) == 1


@pytest.mark.asyncio
async def test_dynamic_delete_tool(creator):
    spec = await creator.create_tool(name="temp", description="", code="def run(p): pass\n")
    deleted = await creator.delete_tool(spec.tool_id)
    assert deleted is True
    assert await creator.get_tool(spec.tool_id) is None


@pytest.mark.asyncio
async def test_dynamic_execute_not_found(creator):
    result = await creator.execute_tool("nonexistent_id")
    assert result.ok is False


@pytest.mark.asyncio
async def test_dynamic_tool_error_handling(creator):
    spec = await creator.create_tool(
        name="crasher",
        description="Always fails",
        code="def run(params):\n    raise RuntimeError('intentional failure')\n",
    )
    result = await creator.execute_tool(spec.tool_id)
    assert result.ok is False
    assert "intentional failure" in result.error


# --- Dynamic Tool Tool ---

from tool_dynamic import DynamicToolTool


@pytest.fixture
def dynamic_tool_tool(tmp_path):
    tool = DynamicToolTool()
    tool.creator = DynamicToolCreator(tools_dir=tmp_path / "dyn_tools")
    return tool


@pytest.mark.asyncio
async def test_dynamic_tool_tool_create_and_list(dynamic_tool_tool):
    result = await dynamic_tool_tool.run({
        "action": "create",
        "name": "doubler",
        "description": "Doubles a number",
        "code": "def run(params):\n    return params.get('x', 0) * 2\n",
    })
    assert result.ok is True
    assert "doubler" in result.message

    list_result = await dynamic_tool_tool.run({"action": "list"})
    assert list_result.ok is True
    assert len(list_result.details["tools"]) == 1


# --- Canonical tools integration ---


def test_canonical_tools_include_phase1():
    from canonical_tools import supported_tools, tool_definitions, action_metadata
    tools = supported_tools()
    assert "sandbox" in tools
    assert "artifact" in tools
    assert "dynamic_tool" in tools

    defs = tool_definitions()
    names = [d["function"]["name"] for d in defs]
    assert "sandbox" in names
    assert "artifact" in names
    assert "dynamic_tool" in names

    meta = action_metadata("sandbox", "execute_python")
    assert meta["semantic_type"] == "execution"
    assert meta["mutates_state"] is True


def test_tool_registry_has_phase1():
    from tool_registry import ToolRegistry
    registry = ToolRegistry()
    assert registry.supports("sandbox")
    assert registry.supports("artifact")
    assert registry.supports("dynamic_tool")


# --- Planner integration ---


@pytest.mark.asyncio
async def test_planner_run_python_script():
    from planner_agent import PlannerAgent
    planner = PlannerAgent()
    plan, val = await planner.parse("run python script print('hello')")
    assert any(t.tool == "sandbox" and t.action == "execute_python" for t in plan.tasks)


@pytest.mark.asyncio
async def test_planner_analyze_file():
    from planner_agent import PlannerAgent
    planner = PlannerAgent()
    plan, val = await planner.parse("analyze file C:\\Temp\\data.csv")
    assert any(t.tool == "artifact" for t in plan.tasks)


@pytest.mark.asyncio
async def test_planner_summarize_file():
    from planner_agent import PlannerAgent
    planner = PlannerAgent()
    plan, val = await planner.parse("summarize file C:\\Temp\\report.txt")
    assert any(t.tool == "artifact" and t.action == "transform" for t in plan.tasks)
