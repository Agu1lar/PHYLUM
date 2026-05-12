import json

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


@pytest.mark.asyncio
async def test_credential_store_returns_metadata_only(monkeypatch, isolated_persistence):
    secrets = {}
    store = CredentialStore(isolated_persistence)

    monkeypatch.setattr(store, "_set_password", lambda provider, secret: secrets.__setitem__(provider, secret))
    monkeypatch.setattr("credential_store.keyring.get_password", lambda service, provider: secrets.get(provider))
    monkeypatch.setattr(store, "_delete_password", lambda provider: secrets.pop(provider, None))

    settings = await store.save_credential(
        "openai",
        CredentialPayload(api_key="sk-secret-1234", default_model="gpt-4.1-mini"),
    )
    resolved = await store.resolve_runtime_config("openai")

    assert settings["configured"] is True
    assert settings["last4"] == "1234"
    assert "api_key" not in settings
    assert resolved["api_key"] == "sk-secret-1234"
    assert resolved["model"] == "gpt-4.1-mini"


class FakeProviderClient:
    def __init__(self):
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return AgentTurnResult(
                content="Vou guardar isso na memoria.",
                tool_calls=[
                    NormalizedToolCall(
                        id="tool_call_1",
                        name="memory",
                        arguments={"action": "set", "key": "project", "value": {"text": "agente"}},
                    )
                ],
            )
        return AgentTurnResult(content="Memoria atualizada com sucesso.", tool_calls=[])


@pytest.mark.asyncio
async def test_runtime_manager_agentic_uses_provider_without_persisting_secret(monkeypatch, isolated_persistence):
    secrets = {}
    store = CredentialStore(isolated_persistence)

    monkeypatch.setattr(store, "_set_password", lambda provider, secret: secrets.__setitem__(provider, secret))
    monkeypatch.setattr("credential_store.keyring.get_password", lambda service, provider: secrets.get(provider))
    monkeypatch.setattr(store, "_delete_password", lambda provider: secrets.pop(provider, None))

    await store.save_credential(
        "openai",
        CredentialPayload(api_key="sk-agentic-secret-9999", default_model="gpt-4.1-mini"),
    )

    events = []

    async def emitter(message):
        events.append(message)

    manager = RuntimeManager(emitter, credential_store=store, provider_client=FakeProviderClient())
    request_id = await manager.submit_run(
        {"text": "remember that the project is agente"},
        runtime_mode="agentic",
        provider="openai",
        model="gpt-4.1-mini",
    )
    final_state = await manager.wait_for_run(request_id, timeout=10)

    assert final_state["status"] == "completed"
    assert final_state["runtime_mode"] == "agentic"
    assert final_state["provider"] == "openai"
    assert final_state["outputs"]["agent_final_response"]["text"] == "Memoria atualizada com sucesso."
    assert any(key.startswith("agentic-") for key in final_state["outputs"])
    assert any(event["type"] == "tool_call_proposed" for event in events)
    assert any(event["type"] == "agent_step" for event in events)
    serialized_state = json.dumps(final_state)
    assert "sk-agentic-secret-9999" not in serialized_state
