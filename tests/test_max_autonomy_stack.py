import pytest

from agent_persistence import Persistence
from canonical_tools import action_metadata, supported_tools, tool_definitions
from planner_agent import PlannerAgent
from policy_engine import PolicyEngine
from recovery_engine import RecoveryEngine
from runtime_manager import RuntimeManager
from tool_memory import MemoryTool


@pytest.fixture()
def isolated_persistence(tmp_path):
    previous = Persistence._instance
    Persistence._instance = Persistence(str(tmp_path / "autonomy_state.db"))
    yield Persistence._instance
    Persistence._instance = previous


def test_canonical_tools_expose_max_autonomy_surface():
    tool_names = set(supported_tools())
    assert {"windows_ui", "share_discovery", "document_intelligence", "office"}.issubset(tool_names)

    schema_names = {item["function"]["name"] for item in tool_definitions()}
    assert {"windows_ui", "share_discovery", "document_intelligence", "office"}.issubset(schema_names)

    assert action_metadata("windows_ui", "inspect_window")["approval_mode"] == "none"
    assert action_metadata("windows_ui", "invoke_element")["approval_mode"] == "single"
    assert action_metadata("share_discovery", "inspect_share")["approval_mode"] == "none"
    assert action_metadata("office", "export_pdf")["approval_mode"] == "single"


@pytest.mark.asyncio
async def test_planner_routes_share_and_office_requests():
    planner = PlannerAgent()

    share_plan, share_validation = await planner.parse(r"inspect share \\server\contratos")
    assert share_validation.ok
    assert share_plan.tasks[0].tool == "share_discovery"
    assert share_plan.tasks[0].action == "inspect_share"
    assert share_plan.tasks[0].params["path"] == r"\\server\contratos"

    office_plan, office_validation = await planner.parse(r"export pdf C:\Docs\contrato.docx")
    assert office_validation.ok
    assert office_plan.tasks[0].tool == "office"
    assert office_plan.tasks[0].action == "export_pdf"
    assert office_plan.tasks[0].params["path"].lower() == r"c:\docs\contrato.docx"


def test_policy_requires_approval_for_windows_ui_mutation():
    policy = PolicyEngine()

    inspect_result = policy.evaluate(
        {
            "runtime_mode": "agentic",
            "current_task": {"tool": "windows_ui", "action": "inspect_window", "params": {"title": "Word"}},
        }
    )
    assert inspect_result["status"] == "allow"

    mutate_result = policy.evaluate(
        {
            "runtime_mode": "agentic",
            "current_task": {
                "tool": "windows_ui",
                "action": "invoke_element",
                "params": {"title": "Word", "selector": {"title": "Salvar"}},
            },
        }
    )
    assert mutate_result["status"] == "require_approval"
    assert mutate_result["approval"]["mode"] == "single"


def test_recovery_engine_replans_when_goal_needs_verification():
    recovery = RecoveryEngine().classify_action_result(
        task={"tool": "desktop", "action": "open_app"},
        action_result={
            "status": "partial",
            "summary": "Opened app, but verification is still pending.",
            "goal": {
                "satisfied": False,
                "rationale": "Need to confirm the resulting window.",
                "recommended_followups": ["desktop.wait_for_window"],
            },
        },
        attempt=1,
        max_attempts=2,
    )
    assert recovery["classification"] == "replan_required"
    assert recovery["suggested_action"] == "verify_outcome"
    assert "desktop.wait_for_window" in recovery["recommended_followups"]


@pytest.mark.asyncio
async def test_memory_tool_supports_world_model_actions(isolated_persistence):
    tool = MemoryTool(default_retries=1)

    upsert = await tool.run(
        {
            "action": "upsert_entity",
            "entity_type": "share",
            "key": "server-2",
            "attributes": {"path": r"\\server\share", "label": "Servidor 2"},
        }
    )
    assert upsert.success is True

    queried = await tool.run({"action": "query_entities", "entity_type": "share", "query": "servidor"})
    assert queried.success is True
    assert queried.items

    recorded = await tool.run(
        {
            "action": "record_observation",
            "entity_type": "share",
            "attributes": {"path": r"\\server\share", "status": "reachable"},
        }
    )
    assert recorded.success is True


@pytest.mark.asyncio
async def test_runtime_manager_persists_autonomy_checkpoint(monkeypatch, isolated_persistence):
    events = []

    async def emitter(message):
        events.append(message)

    manager = RuntimeManager(emitter)

    async def configured(_provider):
        return True

    async def resolve_config(provider, model=None):
        return {"provider": provider, "model": model or "test-model", "api_key": "secret"}

    async def fake_run(*, checkpoint, **kwargs):
        await checkpoint(
            {
                "messages": [{"role": "assistant", "content": "Investigando o share"}],
                "pending_subgoal": "descobrir o share correto",
                "hypothesis": {"tool": "share_discovery", "action": "discover_targets"},
                "observation": {"kind": "checkpoint", "note": "teste"},
            }
        )
        return {
            "status": "completed",
            "final_text": "Consegui concluir.",
            "steps": 2,
            "session": {"messages": [{"role": "assistant", "content": "Consegui concluir."}], "step": 2},
        }

    monkeypatch.setattr(manager.credential_store, "is_configured", configured)
    monkeypatch.setattr(manager.credential_store, "resolve_runtime_config", resolve_config)
    monkeypatch.setattr(manager.agentic_loop, "run", fake_run)

    request_id = await manager.submit_run({"text": "descubra o share correto"}, runtime_mode="agentic", provider="openai")
    final_state = await manager.wait_for_run(request_id, timeout=10)

    assert final_state["status"] == "completed"
    assert "descobrir o share correto" in final_state["autonomy"]["pending_subgoals"]
    assert final_state["autonomy"]["hypotheses"]
    assert final_state["autonomy"]["observations"]
    assert any(event["type"] == "run_finished" for event in events)

