import pytest

from agent_persistence import Persistence
from credential_store import CredentialPayload, CredentialStore
from multi_provider_client import AgentTurnResult, NormalizedToolCall
from runtime_manager import RuntimeManager


@pytest.fixture()
def isolated_persistence(tmp_path):
    previous = Persistence._instance
    Persistence._instance = Persistence(str(tmp_path / "agent_state.db"))
    yield Persistence._instance
    Persistence._instance = previous


class HandoffProviderClient:
    def __init__(self):
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return AgentTurnResult(
                content="Preciso que voce escolha uma opcao.",
                tool_calls=[
                    NormalizedToolCall(
                        id="handoff-1",
                        name="request_user_input",
                        arguments={
                            "title": "Escolha uma opcao",
                            "prompt": "Encontrei duas alternativas. Qual devo seguir?",
                            "allow_free_text": True,
                            "options": [
                                {"id": "a", "label": "Opcao A", "value": "A"},
                                {"id": "b", "label": "Opcao B", "value": "B"},
                            ],
                        },
                    )
                ],
            )
        return AgentTurnResult(content="Continuando com a resposta do usuario.", tool_calls=[])


@pytest.mark.asyncio
async def test_runtime_manager_agentic_pause_reply_resume(monkeypatch, isolated_persistence):
    secrets = {}
    store = CredentialStore(isolated_persistence)

    monkeypatch.setattr(store, "_set_password", lambda provider, secret: secrets.__setitem__(provider, secret))
    monkeypatch.setattr("credential_store.keyring.get_password", lambda service, provider: secrets.get(provider))
    monkeypatch.setattr(store, "_delete_password", lambda provider: secrets.pop(provider, None))

    await store.save_credential(
        "openai",
        CredentialPayload(api_key="sk-test-1234", default_model="gpt-4.1-mini"),
    )

    events = []

    async def emitter(message):
        events.append(message)

    manager = RuntimeManager(emitter, credential_store=store, provider_client=HandoffProviderClient())
    request_id = await manager.submit_run(
        {"text": "me ajude a escolher"},
        runtime_mode="agentic",
        provider="openai",
        model="gpt-4.1-mini",
    )
    paused_state = await manager.wait_for_run(request_id, timeout=10)

    assert paused_state["status"] == "awaiting_input"
    assert paused_state["pending_handoff"]["prompt"] == "Encontrei duas alternativas. Qual devo seguir?"
    assert any(event["type"] == "user_input_requested" for event in events)

    await manager.reply_to_run(request_id, {"text": "Escolha a opcao A"})
    await manager.resume_run(request_id)
    final_state = await manager.wait_for_run(request_id, timeout=10)

    assert final_state["status"] == "completed"
    assert final_state["outputs"]["agent_final_response"]["text"] == "Continuando com a resposta do usuario."
    assert final_state["pending_handoff"] is None
    assert any(event["type"] == "run_resumed" for event in events)


@pytest.mark.asyncio
async def test_runtime_manager_api_first_falls_back_to_manual_assist(isolated_persistence):
    async def emitter(message):
        return None

    manager = RuntimeManager(emitter)
    request_id = await manager.submit_run({"text": "find executable chrome"}, runtime_mode="agentic")
    final_state = await manager.wait_for_run(request_id, timeout=10)

    assert final_state["status"] == "completed"
    assert final_state["outputs"]["execution_mode"] == "manual_assist"
    assert final_state["tasks"][0]["status"] == "manual_step"


@pytest.mark.asyncio
async def test_runtime_manager_rehydrates_pending_handoff(monkeypatch, isolated_persistence):
    secrets = {}
    store = CredentialStore(isolated_persistence)

    monkeypatch.setattr(store, "_set_password", lambda provider, secret: secrets.__setitem__(provider, secret))
    monkeypatch.setattr("credential_store.keyring.get_password", lambda service, provider: secrets.get(provider))
    monkeypatch.setattr(store, "_delete_password", lambda provider: secrets.pop(provider, None))

    await store.save_credential(
        "openai",
        CredentialPayload(api_key="sk-test-1234", default_model="gpt-4.1-mini"),
    )

    async def emitter(message):
        return None

    manager = RuntimeManager(emitter, credential_store=store, provider_client=HandoffProviderClient())
    request_id = await manager.submit_run(
        {"text": "me ajude a escolher"},
        runtime_mode="agentic",
        provider="openai",
        model="gpt-4.1-mini",
    )
    paused_state = await manager.wait_for_run(request_id, timeout=10)
    assert paused_state["status"] == "awaiting_input"

    new_manager = RuntimeManager(emitter, credential_store=store, provider_client=HandoffProviderClient())
    recovered = await new_manager.rehydrate_runs()
    recovered_state = await new_manager.get_state(request_id)

    assert any(item["request_id"] == request_id for item in recovered)
    assert recovered_state["status"] == "awaiting_input"
    assert recovered_state["pending_handoff"]["handoff_id"] == paused_state["pending_handoff"]["handoff_id"]
