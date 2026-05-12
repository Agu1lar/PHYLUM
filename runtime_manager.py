from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from pydantic import BaseModel

from action_executor import ActionExecutor, RunPausedError
from agent_persistence import Persistence
from agentic_loop import AgenticLoop
from canonical_tools import action_metadata, normalize_agentic_task, supported_tools as canonical_supported_tools, task_title
from credential_store import CredentialStore
from multi_provider_client import MultiProviderClient
from nodes_reflection import ReflectionNode
from nodes_safety import SafetyNode
from nodes_tool_router import ToolRouterNode
from planner_agent import PlannerAgent
from planner_models import Task
from recovery_engine import RecoveryEngine
from risk_classifier import explain_command, normalize_command

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.utcnow().isoformat()


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return json.loads(value.json())
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _first_non_empty(*values: Any) -> Optional[str]:
    for value in values:
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    return None


class RuntimeManager:
    def __init__(
        self,
        emitter: Callable[[Dict[str, Any]], Awaitable[None]],
        *,
        credential_store: Optional[CredentialStore] = None,
        provider_client: Optional[MultiProviderClient] = None,
    ):
        self.persistence = Persistence.get()
        self.emitter = emitter
        self.credential_store = credential_store or CredentialStore(self.persistence)
        self.provider_client = provider_client or MultiProviderClient()
        self.planner = PlannerAgent(supported_tools=canonical_supported_tools())
        self.safety = SafetyNode("safety")
        self.tool_router = ToolRouterNode("tool_router")
        self.reflection = ReflectionNode("reflection")
        self.recovery_engine = RecoveryEngine()
        self.action_executor = ActionExecutor(self)
        self.agentic_loop = AgenticLoop(
            client=self.provider_client,
            safety=self.safety,
            tool_router=self.tool_router,
            reflection=self.reflection,
        )
        self.active_runs: Dict[str, Dict[str, Any]] = {}
        self.run_tasks: Dict[str, asyncio.Task] = {}
        self.run_cancel_events: Dict[str, asyncio.Event] = {}
        self.approval_waiters: Dict[str, asyncio.Future] = {}
        self.approval_run_map: Dict[str, str] = {}

    async def submit_run(
        self,
        inputs: Dict[str, Any],
        *,
        runtime_mode: str = "agentic",
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> str:
        request_id = str(uuid.uuid4())
        state = self._new_state(request_id, inputs, runtime_mode=runtime_mode, provider=provider, model=model)
        self.active_runs[request_id] = state
        self.run_cancel_events[request_id] = asyncio.Event()
        await self._persist_state(state)
        self.run_tasks[request_id] = asyncio.create_task(self._run_pipeline(request_id))
        return request_id

    async def rehydrate_runs(self) -> List[Dict[str, Any]]:
        recovered: List[Dict[str, Any]] = []
        for state in await self.persistence.list_states():
            status = state.get("status")
            if status in {"completed", "failed", "cancelled"}:
                continue
            request_id = state["request_id"]
            self.active_runs[request_id] = state
            self.run_cancel_events.setdefault(request_id, asyncio.Event())

            pending_handoff = state.get("pending_handoff")
            pending_approvals = [approval for approval in state.get("approvals", []) if approval.get("status") == "pending"]
            if pending_handoff and not pending_handoff.get("response"):
                state["status"] = "awaiting_input"
            elif pending_approvals:
                state["status"] = "awaiting_approval"
                for approval in pending_approvals:
                    approval_id = approval["approval_id"]
                    self.approval_run_map[approval_id] = request_id
                    self.approval_waiters.setdefault(approval_id, asyncio.get_event_loop().create_future())
            elif status in {"planning", "running", "resuming", "recovering", "cancelling", "queued"}:
                state["status"] = "paused"
                state.setdefault("recovery", {})["rehydrated"] = True
                state.setdefault("recovery", {})["reason"] = "backend restarted before run reached a terminal state"

            await self._persist_state(state)
            recovered.append(state)
        return recovered

    async def cancel_run(self, request_id: str) -> Dict[str, Any]:
        state = self.active_runs.get(request_id)
        if state is None:
            persisted = await self.persistence.get_kv(f"state:{request_id}")
            if persisted is None:
                raise KeyError(request_id)
            return {
                "request_id": request_id,
                "status": persisted.get("status"),
                "already_terminal": True,
            }
        if state["status"] in {"completed", "failed", "cancelled"}:
            return {
                "request_id": request_id,
                "status": state["status"],
                "already_terminal": True,
            }

        cancel_event = self.run_cancel_events.setdefault(request_id, asyncio.Event())
        cancel_event.set()
        await self._set_run_status(state, "cancelling")
        await self._emit("run_cancellation_requested", {"request_id": request_id, "status": "cancelling"}, state=state)

        task_to_cancel = None
        current_task_id = state.get("current_task_id")
        if current_task_id:
            task_to_cancel = next((task for task in state.get("tasks", []) if task.get("id") == current_task_id), None)
        if task_to_cancel is None:
            task_to_cancel = next(
                (
                    task
                    for task in state.get("tasks", [])
                    if task.get("status") not in {"completed", "failed", "cancelled", "rejected"}
                ),
                None,
            )
        if task_to_cancel is not None and task_to_cancel.get("status") != "cancelled":
            task_to_cancel["status"] = "cancelled"
            task_to_cancel["error"] = task_to_cancel.get("error") or "cancelled"
            await self._emit(
                "task_cancelled",
                {"request_id": request_id, "task_id": task_to_cancel["id"], "error": "cancelled"},
                state=state,
            )
        elif current_task_id or state.get("tasks"):
            fallback_task_id = current_task_id or state["tasks"][0]["id"]
            await self._emit(
                "task_cancelled",
                {"request_id": request_id, "task_id": fallback_task_id, "error": "cancelled"},
                state=state,
            )

        for approval_id, run_id in list(self.approval_run_map.items()):
            if run_id != request_id:
                continue
            waiter = self.approval_waiters.get(approval_id)
            if waiter is not None and not waiter.done():
                waiter.set_result("cancelled")

        task = self.run_tasks.get(request_id)
        if task is not None and not task.done():
            task.cancel()
        return {"request_id": request_id, "status": "cancelling", "already_terminal": False}

    async def reply_to_run(self, request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        state = await self._ensure_active_state(request_id)
        handoff = state.get("pending_handoff")
        if not handoff:
            raise ValueError("run does not have a pending handoff")
        handoff["response"] = _jsonable(payload)
        handoff["status"] = "answered"
        state["pending_handoff"] = handoff
        state["last_updated"] = _now()
        await self._set_run_status(state, "paused", current_node="handoff")
        await self._emit(
            "user_input_received",
            {"request_id": request_id, "handoff_id": handoff["handoff_id"], "response": handoff["response"]},
            state=state,
        )
        return {"request_id": request_id, "handoff": handoff}

    async def resume_run(self, request_id: str) -> Dict[str, Any]:
        state = await self._ensure_active_state(request_id)
        if state["status"] in {"completed", "failed", "cancelled"}:
            return {"request_id": request_id, "status": state["status"], "already_terminal": True}

        pending_handoff = state.get("pending_handoff")
        if pending_handoff:
            if not pending_handoff.get("response"):
                raise ValueError("pending handoff requires a user response before resuming")
            await self._apply_handoff_response(state, pending_handoff)

        if any(approval.get("status") == "pending" for approval in state.get("approvals", [])):
            await self._set_run_status(state, "awaiting_approval")
            return {"request_id": request_id, "status": "awaiting_approval", "resumed": False}

        if request_id in self.run_tasks and not self.run_tasks[request_id].done():
            return {"request_id": request_id, "status": state["status"], "resumed": False}

        self.run_cancel_events.setdefault(request_id, asyncio.Event())
        self.run_tasks[request_id] = asyncio.create_task(self._run_pipeline(request_id, resume=True))
        return {"request_id": request_id, "status": "resuming", "resumed": True}

    async def list_runs(self) -> List[Dict[str, Any]]:
        states = {state["request_id"]: state for state in await self.persistence.list_states()}
        for request_id, state in self.active_runs.items():
            states[request_id] = _jsonable(state)
        return sorted(states.values(), key=lambda item: item.get("last_updated") or "", reverse=True)

    async def list_approvals(self, request_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return await self.persistence.list_approvals(request_id=request_id)

    async def delete_run(self, request_id: str) -> Dict[str, Any]:
        state = self.active_runs.get(request_id)
        task = self.run_tasks.get(request_id)
        if task is not None and not task.done():
            await self.cancel_run(request_id)
            try:
                await self.wait_for_run(request_id, timeout=10)
            except Exception:
                logger.exception("Run %s did not finish cleanly before deletion", request_id)

        persisted = await self.persistence.get_kv(f"state:{request_id}")
        if state is None and persisted is None:
            raise KeyError(request_id)

        self.active_runs.pop(request_id, None)
        self.run_tasks.pop(request_id, None)
        self.run_cancel_events.pop(request_id, None)

        for approval_id, run_id in list(self.approval_run_map.items()):
            if run_id == request_id:
                self.approval_run_map.pop(approval_id, None)
                waiter = self.approval_waiters.pop(approval_id, None)
                if waiter is not None and not waiter.done():
                    waiter.cancel()

        await self.persistence.delete_state(request_id)
        await self.persistence.delete_approvals(request_id)
        await self._emit("run_deleted", {"request_id": request_id})
        return {"request_id": request_id, "deleted": True}

    async def wait_for_run(self, request_id: str, timeout: Optional[float] = None) -> Dict[str, Any]:
        task = self.run_tasks.get(request_id)
        if task is None:
            state = await self.get_state(request_id)
            if state is None:
                raise KeyError(request_id)
            return state
        deadline = None if timeout is None else asyncio.get_event_loop().time() + timeout
        try:
            if timeout is not None:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            else:
                await task
        except asyncio.CancelledError:
            pass
        except asyncio.TimeoutError:
            pass
        while True:
            state = await self.get_state(request_id)
            if state is None:
                raise KeyError(request_id)
            if task.done() and state.get("status") == "cancelling" and request_id in self.active_runs:
                await self._mark_run_cancelled(self.active_runs[request_id])
                state = await self.get_state(request_id)
            if state.get("status") in {"completed", "failed", "cancelled", "awaiting_input", "paused", "awaiting_approval"}:
                return state
            if deadline is not None and asyncio.get_event_loop().time() >= deadline:
                raise asyncio.TimeoutError()
            await asyncio.sleep(0.05)

    async def get_state(self, request_id: str) -> Optional[Dict[str, Any]]:
        if request_id in self.active_runs:
            return _jsonable(self.active_runs[request_id])
        return await self.persistence.get_kv(f"state:{request_id}")

    async def _ensure_active_state(self, request_id: str) -> Dict[str, Any]:
        state = self.active_runs.get(request_id)
        if state is not None:
            return state
        persisted = await self.persistence.get_kv(f"state:{request_id}")
        if persisted is None:
            raise KeyError(request_id)
        self.active_runs[request_id] = persisted
        self.run_cancel_events.setdefault(request_id, asyncio.Event())
        return persisted

    async def request_manual_approval(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        approval_id = str(uuid.uuid4())
        request_id = payload.get("request_id") or approval_id
        approval = {
            "approval_id": approval_id,
            "request_id": request_id,
            "task_id": payload.get("task_id"),
            "title": payload.get("title", "Approval request"),
            "reason": payload.get("reason", ""),
            "status": "pending",
            "payload": payload,
        }
        await self.persistence.create_approval(
            approval_id,
            request_id,
            payload.get("approver", ""),
            approval,
            task_id=payload.get("task_id"),
        )
        await self._emit("approval_requested", {"request_id": request_id, "approval": approval})
        return approval

    async def resolve_approval(self, approval_id: str, status: str) -> Dict[str, Any]:
        await self.persistence.set_approval(approval_id, status)
        approval = await self.persistence.get_approval(approval_id)
        if approval is None:
            raise KeyError(approval_id)
        request_id = approval.get("request_id")
        run_state = await self._ensure_active_state(request_id)
        if run_state is not None:
            for existing in run_state["approvals"]:
                if existing["approval_id"] == approval_id:
                    existing["status"] = status
            for task in run_state.get("tasks", []):
                if task.get("approval_id") == approval_id:
                    task["approval_granted"] = status == "approved"
                    if status == "approved":
                        task["status"] = "approved"
            await self._persist_state(run_state)
        waiter = self.approval_waiters.pop(approval_id, None)
        if waiter is not None and not waiter.done():
            waiter.set_result(status)
        payload = {
            "request_id": request_id,
            "approval_id": approval_id,
            "status": status,
            "task_id": approval.get("task_id"),
        }
        await self._emit("approval_resolved", payload)
        return payload

    def _new_state(
        self,
        request_id: str,
        inputs: Dict[str, Any],
        *,
        runtime_mode: str,
        provider: Optional[str],
        model: Optional[str],
    ) -> Dict[str, Any]:
        timestamp = _now()
        return {
            "request_id": request_id,
            "created_at": timestamp,
            "last_updated": timestamp,
            "status": "queued",
            "runtime_mode": runtime_mode,
            "provider": provider,
            "model": model,
            "inputs": _jsonable(inputs),
            "outputs": {},
            "current_node": None,
            "current_task_id": None,
            "tasks": [],
            "history": [],
            "approvals": [],
            "handoffs": [],
            "pending_handoff": None,
            "agent_session": {},
            "autonomy": {
                "pending_subgoals": [],
                "completed_subgoals": [],
                "observations": [],
                "hypotheses": [],
                "strategy_log": [],
                "goal_verifications": [],
            },
            "recovery": {},
            "error": None,
        }

    async def _run_pipeline(self, request_id: str, *, resume: bool = False) -> None:
        state = self.active_runs[request_id]
        try:
            if resume:
                await self._set_run_status(state, "resuming", current_node=state.get("current_node"))
                await self._emit(
                    "run_resumed",
                    {"request_id": request_id, "status": state["status"]},
                    state=state,
                )
            else:
                await self._set_run_status(state, "planning", current_node="planner")
                await self._emit(
                    "run_started",
                    {
                        "request_id": request_id,
                        "status": state["status"],
                        "inputs": state["inputs"],
                        "created_at": state["created_at"],
                        "runtime_mode": state["runtime_mode"],
                        "provider": state.get("provider"),
                        "model": state.get("model"),
                    },
                    state=state,
                )

            if state["inputs"].get("allow_local_execution") and (
                state["runtime_mode"] != "agentic" or not state.get("provider")
            ):
                await self._run_local_heuristic_pipeline(state)
                return

            use_manual_assist, reason = await self._should_use_manual_assist(state)
            if use_manual_assist:
                if state["inputs"].get("allow_local_execution"):
                    await self._run_local_heuristic_pipeline(state)
                else:
                    await self._run_manual_assist_pipeline(state, reason=reason)
            elif state["runtime_mode"] == "agentic":
                await self._run_agentic_pipeline(state)
            else:
                await self._run_local_heuristic_pipeline(state)
        except RunPausedError:
            return
        except asyncio.CancelledError:
            await self._mark_run_cancelled(state)
        except Exception as exc:
            logger.exception("Run %s failed unexpectedly", request_id)
            if state.get("status") != "failed":
                await self._fail_run(state, str(exc))
        finally:
            self.run_tasks.pop(request_id, None)
            if state.get("status") in {"completed", "failed", "cancelled"}:
                self.run_cancel_events.pop(request_id, None)

    async def _should_use_manual_assist(self, state: Dict[str, Any]) -> Any:
        if state["inputs"].get("allow_local_execution"):
            return False, None
        if state["runtime_mode"] != "agentic":
            return True, "runtime configured for manual assist fallback"
        provider = state.get("provider")
        if not provider:
            return True, "no provider configured for API-first mode"
        if not await self.credential_store.is_configured(provider):
            return True, f"provider '{provider}' is not configured"
        return False, None

    async def _run_manual_assist_pipeline(self, state: Dict[str, Any], *, reason: Optional[str] = None) -> None:
        plan_result = await self._plan_tasks(state)
        tasks = plan_result["tasks"]
        state["outputs"]["execution_mode"] = "manual_assist"
        if reason:
            state["outputs"]["manual_assist_reason"] = reason
        if not tasks:
            await self._complete_run(
                state,
                summary=plan_result["message"],
                details={"kind": plan_result["kind"], "execution_mode": "manual_assist", "reason": reason},
                current_node="planner",
            )
            return
        state["tasks"] = tasks
        for task in tasks:
            task["status"] = "manual_step"
            await self._emit("task_planned", {"request_id": state["request_id"], "task": task}, state=state)

        summary_lines = [f"- {task['title']}" for task in state["tasks"]]
        summary = "Modo manual assistido ativo."
        if reason:
            summary = f"{summary} Motivo: {reason}."
        summary = f"{summary}\nPassos sugeridos:\n" + "\n".join(summary_lines)
        state["outputs"]["manual_assist_plan"] = {
            "reason": reason,
            "tasks": state["tasks"],
        }
        await self._complete_run(
            state,
            summary=summary,
            details={"execution_mode": "manual_assist", "reason": reason, "task_ids": [task["id"] for task in state["tasks"]]},
            current_node="planner",
        )

    async def _run_local_heuristic_pipeline(self, state: Dict[str, Any]) -> None:
        plan_result = await self._plan_tasks(state)
        tasks = plan_result["tasks"]
        if not tasks:
            await self._complete_run(
                state,
                summary=plan_result["message"],
                details={"kind": plan_result["kind"], "execution_mode": "local_heuristic"},
                current_node="planner",
            )
            return
        if not state.get("tasks"):
            state["tasks"] = tasks
        for task in state["tasks"]:
            if task.get("status") == "completed":
                continue
            if task.get("status") == "manual_step":
                task["status"] = "pending"
            await self._emit("task_planned", {"request_id": state["request_id"], "task": task}, state=state)
            await self._execute_task_with_recovery(state, task)

        await self._complete_run(
            state,
            summary=f"Completed {len(state['tasks'])} task(s)",
            details={"completed_tasks": [task["id"] for task in state["tasks"]], "execution_mode": "local_heuristic"},
            current_node="reflection",
        )

    async def _run_agentic_pipeline(self, state: Dict[str, Any]) -> None:
        provider = state.get("provider")
        if not provider:
            await self._run_manual_assist_pipeline(state, reason="no provider configured for API-first mode")
            return
        provider_config = await self.credential_store.resolve_runtime_config(provider, model=state.get("model"))
        result = await self.agentic_loop.run(
            state=state,
            provider_config=provider_config,
            emit=lambda event_type, payload: self._emit(event_type, payload, state=state),
            task_factory=self._agentic_task_from_tool_call,
            execute_task=self._execute_task_with_recovery,
            cancel_event=self.run_cancel_events[state["request_id"]],
            session=state.get("agent_session") or None,
            checkpoint=lambda session_update: self._checkpoint_agent_session(state, session_update),
        )
        state["agent_session"] = result.get("session") or {}
        if result["status"] == "awaiting_input":
            await self._pause_for_handoff(state, result["handoff"])
            raise RunPausedError()
        state["outputs"]["agent_final_response"] = {
            "provider": provider_config["provider"],
            "model": provider_config["model"],
            "text": result["final_text"],
            "steps": result["steps"],
        }
        state["outputs"]["execution_mode"] = "agentic"
        await self._complete_run(
            state,
            summary=result["final_text"],
            details={
                "runtime_mode": "agentic",
                "provider": provider_config["provider"],
                "model": provider_config["model"],
                "steps": result["steps"],
                "execution_mode": "agentic",
            },
            current_node="reflection",
        )

    def _current_failure_task(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        current_task_id = state.get("current_task_id")
        if current_task_id:
            task = next((item for item in state.get("tasks", []) if item.get("id") == current_task_id), None)
            if task is not None:
                return task
        for task in reversed(state.get("tasks", [])):
            if task.get("status") in {"failed", "rejected", "retry_scheduled", "waiting_approval"}:
                return task
        return state.get("tasks", [])[-1] if state.get("tasks") else None

    def _user_facing_failure_reflection(self, state: Dict[str, Any], error: str) -> Dict[str, Any]:
        task = self._current_failure_task(state)
        recovery = (task or {}).get("recovery") or state.get("recovery") or {"action": "stop"}
        tool_result = (((task or {}).get("result") or {}).get("tool_result") or {})
        details = (tool_result.get("details") or {}) if isinstance(tool_result, dict) else {}
        params = (task or {}).get("params") or {}
        task_title = (task or {}).get("title") or "esta tarefa"
        raw_error = _first_non_empty(
            error,
            (task or {}).get("error"),
            details.get("error") if isinstance(details, dict) else None,
            details.get("stderr") if isinstance(details, dict) else None,
        ) or "erro inesperado"
        message = raw_error.lower()
        tool = (task or {}).get("tool")
        action = (task or {}).get("action")

        next_steps: List[str] = []
        manual_option: Optional[str] = None

        if "approval rejected" in message:
            summary = (
                "Nao continuei porque a acao que precisava da sua aprovacao foi rejeitada. "
                "Se quiser tentar de novo, inicie a tarefa novamente e aprove essa etapa."
            )
            manual_option = "Se preferir, faca essa etapa manualmente fora do agente."
        elif tool == "driver_manager" and action in {"printer_status", "printer_driver_info"}:
            target = params.get("printer_name") or params.get("query") or params.get("device_id")
            if target:
                summary = (
                    f"Nao consegui verificar a impressora '{target}'. "
                    "Confirme o nome exato, o IP ou o caminho compartilhado da impressora para eu tentar novamente."
                )
            else:
                summary = (
                    "Nao consegui continuar a configuracao da impressora porque ainda nao sei qual impressora de rede devo procurar. "
                    "Me informe o nome exibido da impressora, o IP ou o caminho compartilhado."
                )
            next_steps = [
                "Envie o nome da impressora, o endereco IP ou o caminho UNC no formato \\\\servidor\\impressora.",
                "Se souber o modelo, envie tambem o modelo para facilitar a busca do driver correto.",
            ]
            manual_option = (
                "Se preferir configurar manualmente, abra Configuracoes > Bluetooth e dispositivos > Impressoras e scanners > Adicionar dispositivo."
            )
        elif "path not allowed by sandbox" in message:
            target = params.get("path") or params.get("dest") or "o local solicitado"
            summary = (
                f"Nao consegui acessar {target} automaticamente porque esse local exige uma aprovacao explicita ou mais contexto. "
                "Se quiser, tente de novo e aprove a acao quando ela for solicitada."
            )
            manual_option = "Se preferir, abra esse local manualmente no Explorador de Arquivos e execute a etapa por conta propria."
        elif "validation:" in message:
            summary = (
                f"Nao consegui concluir '{task_title}' porque a tarefa foi bloqueada por uma validacao interna antes da execucao. "
                "Normalmente isso acontece quando falta um parametro importante ou quando a acao nao eh segura do jeito solicitado."
            )
            next_steps = ["Reenvie a instrucao com mais detalhes sobre o alvo, caminho, nome ou contexto necessario."]
        elif "timeout" in message:
            summary = (
                f"Nao consegui concluir '{task_title}' a tempo. "
                "A operacao demorou mais do que o esperado e foi interrompida."
            )
            next_steps = ["Tente novamente com mais contexto ou com um alvo mais especifico."]
        else:
            summary = (
                f"Nao consegui concluir '{task_title}'. "
                "A tarefa encontrou um problema interno antes de terminar."
            )
            next_steps = ["Se quiser tentar de novo, reenvie a instrucao com mais detalhes sobre o objetivo ou o alvo exato."]

        if manual_option:
            summary = f"{summary} {manual_option}"

        return {
            "verdict": "failed",
            "summary": summary,
            "details": {
                "task_id": (task or {}).get("id"),
                "task_title": task_title,
                "tool": tool,
                "action": action,
                "technical_error": raw_error,
                "next_steps": next_steps,
                "manual_option": manual_option,
                "recovery": recovery,
                "autonomy": state.get("autonomy") or {},
            },
            "recommended_action": recovery,
        }

    async def _fail_run(self, state: Dict[str, Any], error: str) -> None:
        state["error"] = error
        final_reflection = self._user_facing_failure_reflection(state, error)
        state["outputs"]["final_reflection"] = final_reflection
        await self._set_run_status(state, "failed")
        await self._emit(
            "run_failed",
            {
                "request_id": state["request_id"],
                "status": state["status"],
                "error": error,
                "user_message": final_reflection["summary"],
                "reflection": final_reflection,
            },
            state=state,
        )

    async def _mark_run_cancelled(self, state: Dict[str, Any]) -> None:
        current_task_id = state.get("current_task_id")
        task_to_cancel = None
        if current_task_id:
            task_to_cancel = next((task for task in state["tasks"] if task["id"] == current_task_id), None)
        if task_to_cancel is None:
            task_to_cancel = next(
                (
                    task
                    for task in state["tasks"]
                    if task.get("status") not in {"completed", "failed", "cancelled", "rejected"}
                ),
                None,
            )
        if task_to_cancel is not None and task_to_cancel.get("status") != "cancelled":
            task_to_cancel["status"] = "cancelled"
            if not task_to_cancel.get("error"):
                task_to_cancel["error"] = "cancelled"
            await self._emit(
                "task_cancelled",
                {"request_id": state["request_id"], "task_id": task_to_cancel["id"], "error": "cancelled"},
                state=state,
            )
        state["error"] = "cancelled"
        await self._set_run_status(state, "cancelled")
        await self._emit(
            "run_cancelled",
            {"request_id": state["request_id"], "status": "cancelled", "error": "cancelled"},
            state=state,
        )

    def _cancel_event_for(self, request_id: str) -> asyncio.Event:
        return self.run_cancel_events.setdefault(request_id, asyncio.Event())

    def _raise_if_cancelled(self, state: Dict[str, Any]) -> None:
        if self._cancel_event_for(state["request_id"]).is_set():
            raise asyncio.CancelledError()

    async def _complete_run(
        self,
        state: Dict[str, Any],
        *,
        summary: str,
        details: Dict[str, Any],
        current_node: str,
    ) -> None:
        await self._set_run_status(state, "completed", current_node=current_node)
        final_reflection = {
            "verdict": "success",
            "summary": summary,
            "details": details,
        }
        state["outputs"]["final_reflection"] = final_reflection
        await self._emit(
            "run_finished",
            {"request_id": state["request_id"], "status": state["status"], "reflection": final_reflection},
            state=state,
        )

    async def _set_run_status(
        self,
        state: Dict[str, Any],
        status: str,
        *,
        current_node: Optional[str] = None,
    ) -> None:
        state["status"] = status
        if current_node is not None:
            state["current_node"] = current_node
        state["last_updated"] = _now()
        await self._persist_state(state)

    async def _persist_state(self, state: Dict[str, Any]) -> None:
        state["last_updated"] = _now()
        await self.persistence.save_kv(f"state:{state['request_id']}", _jsonable(state))

    def _autonomy_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return state.setdefault(
            "autonomy",
            {
                "pending_subgoals": [],
                "completed_subgoals": [],
                "observations": [],
                "hypotheses": [],
                "strategy_log": [],
                "goal_verifications": [],
            },
        )

    def _append_unique(self, items: List[Any], value: Any, *, limit: int = 50) -> None:
        if value in items:
            return
        items.append(value)
        if len(items) > limit:
            del items[0 : len(items) - limit]

    async def _checkpoint_agent_session(self, state: Dict[str, Any], session_update: Dict[str, Any]) -> None:
        session = state.setdefault("agent_session", {})
        session.update({key: _jsonable(value) for key, value in session_update.items() if key not in {"observation", "hypothesis", "strategy", "pending_subgoal", "completed_subgoal", "goal_verification"}})

        autonomy = self._autonomy_state(state)
        observation = session_update.get("observation")
        if observation is not None:
            autonomy["observations"].append(_jsonable(observation))
            autonomy["observations"] = autonomy["observations"][-100:]
        hypothesis = session_update.get("hypothesis")
        if hypothesis:
            self._append_unique(autonomy["hypotheses"], _jsonable(hypothesis))
        strategy = session_update.get("strategy")
        if strategy:
            autonomy["strategy_log"].append(_jsonable(strategy))
            autonomy["strategy_log"] = autonomy["strategy_log"][-100:]
        pending_subgoal = session_update.get("pending_subgoal")
        if pending_subgoal:
            self._append_unique(autonomy["pending_subgoals"], str(pending_subgoal))
        completed_subgoal = session_update.get("completed_subgoal")
        if completed_subgoal:
            completed_subgoal = str(completed_subgoal)
            autonomy["pending_subgoals"] = [item for item in autonomy["pending_subgoals"] if item != completed_subgoal]
            self._append_unique(autonomy["completed_subgoals"], completed_subgoal)
        goal_verification = session_update.get("goal_verification")
        if goal_verification:
            autonomy["goal_verifications"].append(_jsonable(goal_verification))
            autonomy["goal_verifications"] = autonomy["goal_verifications"][-100:]
        await self._persist_state(state)

    async def _record_task_observation(
        self,
        state: Dict[str, Any],
        *,
        task: Dict[str, Any],
        result: Optional[Dict[str, Any]] = None,
        recovery: Optional[Dict[str, Any]] = None,
        goal_verification: Optional[Dict[str, Any]] = None,
    ) -> None:
        action_result = ((result or {}).get("action_result") or {}) if result else {}
        summary = action_result.get("summary") or (result or {}).get("tool_result", {}).get("message")
        observation = {
            "task_id": task.get("id"),
            "tool": task.get("tool"),
            "action": task.get("action"),
            "status": action_result.get("status") or task.get("status"),
            "summary": summary,
            "issue": action_result.get("issue"),
            "recovery": recovery,
            "goal_verification": goal_verification,
        }
        await self._checkpoint_agent_session(
            state,
            {
                "observation": observation,
                "completed_subgoal": task.get("title") if observation["status"] in {"succeeded", "completed"} else None,
                "goal_verification": goal_verification,
            },
        )

    def _verify_task_goal(self, task: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        action_result = (result.get("action_result") or {}) if isinstance(result, dict) else {}
        status = action_result.get("status")
        if status != "succeeded":
            return {
                "satisfied": False,
                "strategy": "defer_to_followup",
                "confidence": 0.0,
                "rationale": "The action itself did not succeed, so the goal cannot be considered satisfied yet.",
                "evidence": {"status": status, "summary": action_result.get("summary")},
                "recommended_followups": [],
            }

        tool = task.get("tool")
        action = task.get("action")
        evidence = {
            "target": action_result.get("target") or {},
            "data": action_result.get("data") or {},
        }
        if tool == "desktop" and action in {"open_app", "open_path", "open_file"}:
            return {
                "satisfied": False,
                "strategy": "verify_window_or_process",
                "confidence": 0.45,
                "rationale": "The launch request was accepted, but the runtime should still verify the resulting window, process or opened path.",
                "evidence": evidence,
                "recommended_followups": ["desktop.wait_for_window", "desktop.list_windows", "desktop.list_processes"],
            }
        return {
            "satisfied": True,
            "strategy": "tool_result",
            "confidence": 0.8,
            "rationale": "The tool returned a successful semantic result and no extra verification hook is required.",
            "evidence": evidence,
            "recommended_followups": [],
        }

    async def _emit(self, event_type: str, payload: Dict[str, Any], *, state: Optional[Dict[str, Any]] = None) -> None:
        payload = _jsonable(payload)
        event = {"type": event_type, "payload": payload}
        if state is not None:
            state["history"].append({"type": event_type, "timestamp": _now(), "payload": payload})
            await self._persist_state(state)
        await self.emitter(event)

    async def _execute_task_with_recovery(self, state: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
        return await self.action_executor.execute(state, task)

    async def _plan_tasks(self, state: Dict[str, Any]) -> Dict[str, Any]:
        inputs = state["inputs"]
        text = inputs.get("text") or inputs.get("prompt")
        if text:
            plan, validation = await self.planner.parse(text)
            if not validation.ok:
                return self._handle_non_actionable_input(text, validation)
            return {
                "tasks": [self._task_to_state(task) for task in plan.tasks],
                "kind": "tasks",
                "message": None,
            }

        if inputs.get("command"):
            return {
                "tasks": [
                    self._task_to_state(
                        Task(id=f"task-{uuid.uuid4().hex[:8]}", tool="shell", action="run", params={"command": inputs["command"]})
                    )
                ],
                "kind": "tasks",
                "message": None,
            }
        raise ValueError("inputs.text or inputs.command is required")

    def _handle_non_actionable_input(self, text: str, validation: Any) -> Dict[str, Any]:
        normalized = text.strip().lower()
        if re.fullmatch(r"(oi|ola|olá|hello|hi|bom dia|boa tarde|boa noite)[!. ]*", normalized):
            return {
                "tasks": [],
                "kind": "greeting",
                "message": (
                    "Ola! Eu executo acoes de shell, filesystem, memory, browser, package_manager, os e desktop. "
                    "Exemplos: 'run command Get-Date', "
                    "'list processes', 'open https://example.com' ou "
                    "'remember project is agente'."
                ),
            }
        return {
            "tasks": [],
            "kind": "unsupported_input",
            "message": (
                "Nao encontrei uma acao executavel nessa mensagem. "
                "Tente um pedido como: 'search web driver hp laserjet', "
                "'write hello to C:\\Temp\\agente.txt', "
                "'list windows' ou "
                "'remember project is agente'."
            ),
        }

    def _agentic_task_from_tool_call(self, tool_name: str, arguments: Dict[str, Any], step: int) -> Dict[str, Any]:
        task_id = f"agentic-{step}-{uuid.uuid4().hex[:6]}"
        return normalize_agentic_task(tool_name, arguments, task_id)

    def _task_to_state(self, task: Task) -> Dict[str, Any]:
        title = self._task_title(task)
        params = _jsonable(task.params)
        return {
            "id": task.id,
            "title": title,
            "tool": task.tool,
            "action": task.action,
            "params": params,
            "intent": {
                "tool": task.tool,
                "action": task.action,
                "params": params,
                "task_id": task.id,
                "title": title,
            },
            "policy_metadata": action_metadata(task.tool, task.action),
            "depends_on": list(task.depends_on),
            "status": "pending",
            "attempt": 0,
            "max_attempts": 2,
            "recovery": None,
            "requires_approval": False,
            "approval_granted": False,
            "approval_id": None,
            "result": None,
            "error": None,
            "reflection": None,
        }

    def _task_title(self, task: Task) -> str:
        return task_title(task.tool, task.action, task.params)

    def _describe_approval(self, task: Dict[str, Any], safety: Dict[str, Any]) -> Dict[str, Any]:
        tool = task.get("tool")
        action = task.get("action")
        params = task.get("params") or {}
        approval_meta = safety.get("approval") or {}
        title = f"Approve task: {task['title']}"
        reason = safety["reason"]
        details: Dict[str, Any] = {
            "tool": tool,
            "action": action,
            "predicted_effects": approval_meta.get("predicted_effects") or [],
            "reversibility": approval_meta.get("reversibility"),
            "confirmation_level": approval_meta.get("mode", "single"),
        }

        if tool == "filesystem":
            path = params.get("path")
            dest = params.get("dest")
            details.update({"path": path, "dest": dest})
            if action == "delete" and path:
                title = f"Approve delete: {path}"
                reason = f"Isto vai excluir: {path}"
            elif action == "move" and path and dest:
                title = f"Approve move: {path} -> {dest}"
                reason = f"Isto vai mover de {path} para {dest}"
            elif action == "copy" and path and dest:
                title = f"Approve copy: {path} -> {dest}"
                reason = f"Isto vai copiar de {path} para {dest}"
            elif action == "write" and path:
                title = f"Approve write: {path}"
                reason = f"Isto vai gravar/alterar: {path}"
            elif action in {"mkdir", "create_structure"} and path:
                title = f"Approve create: {path}"
                reason = f"Isto vai criar conteudo em: {path}"
            elif action in {"clean_temp", "organize_directory", "detect_duplicates", "find_files", "list", "stat", "read"} and path:
                title = f"Approve filesystem access: {path}"
                reason = f"{safety['reason']}. Alvo: {path}"
            elif path:
                reason = f"{safety['reason']}. Alvo: {path}"

        if tool == "shell":
            command = normalize_command(params.get("command", ""))
            command_explanation = explain_command(command, safety.get("risk"))
            title = "Permitir comando do sistema"
            reason = "Revise o comando abaixo e confirme se o agente pode executa-lo."
            details.update(
                {
                    "command": command,
                    "command_explanation": command_explanation,
                    "shell": params.get("shell", "powershell"),
                }
            )

        if approval_meta.get("mode") == "double":
            reason = f"{reason}. Esta acao exige dupla confirmacao porque pode ser destrutiva ou dificil de reverter."

        return {"title": title, "reason": reason, "details": details}

    async def _create_runtime_approval(self, state: Dict[str, Any], task: Dict[str, Any], safety: Dict[str, Any]) -> Dict[str, Any]:
        approval_id = str(uuid.uuid4())
        approval_copy = self._describe_approval(task, safety)
        approval = {
            "approval_id": approval_id,
            "request_id": state["request_id"],
            "task_id": task["id"],
            "title": approval_copy["title"],
            "reason": approval_copy["reason"],
            "status": "pending",
            "risk": safety.get("risk"),
            "details": approval_copy["details"],
            "approval_mode": (safety.get("approval") or {}).get("mode", "single"),
        }
        task["requires_approval"] = True
        state["approvals"].append(approval)
        self.approval_run_map[approval_id] = state["request_id"]
        self.approval_waiters[approval_id] = asyncio.get_event_loop().create_future()
        await self.persistence.create_approval(
            approval_id,
            state["request_id"],
            "",
            approval,
            task_id=task["id"],
        )
        await self._emit("approval_requested", {"request_id": state["request_id"], "approval": approval}, state=state)
        return approval

    async def _wait_for_approval(self, approval_id: str) -> str:
        future = self.approval_waiters.get(approval_id)
        if future is None:
            future = asyncio.get_event_loop().create_future()
            self.approval_waiters[approval_id] = future
        request_id = self.approval_run_map.get(approval_id)
        cancel_event = self.run_cancel_events.get(request_id) if request_id else None
        if cancel_event is None:
            return await future

        cancel_task = asyncio.create_task(cancel_event.wait())
        done, pending = await asyncio.wait({future, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
        for pending_task in pending:
            pending_task.cancel()
        if cancel_task in done:
            return "cancelled"
        return await future

    def _resolved_approval_status(self, state: Dict[str, Any], task_id: str) -> Optional[str]:
        for approval in state.get("approvals", []):
            if approval.get("task_id") == task_id:
                status = approval.get("status")
                if status in {"approved", "rejected"}:
                    return status
        return None

    async def _pause_for_handoff(self, state: Dict[str, Any], handoff: Dict[str, Any]) -> None:
        existing = next((item for item in state.get("handoffs", []) if item.get("handoff_id") == handoff["handoff_id"]), None)
        if existing is None:
            state.setdefault("handoffs", []).append(handoff)
        else:
            existing.update(handoff)
        state["pending_handoff"] = handoff
        await self._set_run_status(state, "awaiting_input", current_node="handoff")
        await self._emit(
            "user_input_requested",
            {"request_id": state["request_id"], "handoff": handoff},
            state=state,
        )
        await self._emit(
            "run_paused",
            {"request_id": state["request_id"], "status": "awaiting_input", "handoff_id": handoff["handoff_id"]},
            state=state,
        )

    def _handoff_for_recovery(self, state: Dict[str, Any], task: Dict[str, Any], error: str) -> Dict[str, Any]:
        payload = self.recovery_engine.question_for_failure(task, error)
        return {
            "handoff_id": str(uuid.uuid4()),
            "request_id": state["request_id"],
            "task_id": task["id"],
            "tool_call_id": None,
            "kind": payload["kind"],
            "title": payload["title"],
            "prompt": payload["prompt"],
            "reason": error,
            "status": "pending",
            "allow_free_text": payload["allow_free_text"],
            "options": payload["options"],
            "response": None,
        }

    async def _apply_handoff_response(self, state: Dict[str, Any], handoff: Dict[str, Any]) -> None:
        response = handoff.get("response")
        if response is None:
            return
        tool_call_id = handoff.get("tool_call_id")
        if tool_call_id:
            session = state.setdefault("agent_session", {})
            messages = list(session.get("messages") or [])
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps(response, default=str),
                }
            )
            session["messages"] = messages
            session["paused_reason"] = None
        handoff["status"] = "resolved"
        state["pending_handoff"] = None
        await self._persist_state(state)

