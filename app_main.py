import asyncio
import ctypes
import importlib.util
import json
import logging
import os
import platform
import socket
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder

from pydantic import BaseModel

from agent_persistence import Persistence
from canonical_tools import tool_definitions
from credential_store import CredentialPayload, CredentialStore
from multi_provider_client import MultiProviderClient
from runtime_manager import RuntimeManager

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI()

def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _split_csv_env(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _backend_bind_host() -> str:
    return os.getenv("AGENTE_RUNTIME_HOST") or os.getenv("AGENTE_BACKEND_HOST") or "127.0.0.1"


def _backend_port() -> int:
    return int(os.getenv("AGENTE_RUNTIME_PORT") or os.getenv("AGENTE_BACKEND_PORT") or "8000")


def _lan_origin_regex() -> str:
    return r"^https?://([a-zA-Z0-9.-]+|\d{1,3}(?:\.\d{1,3}){3})(:\d+)?$"


def _private_ipv4_addresses() -> list[str]:
    values: set[str] = set()
    try:
        hostname = socket.gethostname()
        for entry in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            ip = entry[4][0]
            if ip and not ip.startswith("127."):
                values.add(ip)
    except Exception:
        logger.debug("Unable to inspect local IPv4 addresses", exc_info=True)
    return sorted(values)


LAN_ENABLED = _env_flag("AGENTE_ALLOW_LAN", default=_backend_bind_host() not in {"127.0.0.1", "localhost"})
PUBLIC_BASE_URL = (os.getenv("AGENTE_PUBLIC_BASE_URL") or "").strip() or None
allow_origins = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    *_split_csv_env("AGENTE_CORS_ALLOW_ORIGINS"),
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_origin_regex=_lan_origin_regex() if LAN_ENABLED else None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

persistence = Persistence.get()
credential_store = CredentialStore(persistence)
provider_client = MultiProviderClient()
runtime: Optional[RuntimeManager] = None
PROJECT_ROOT = Path(__file__).resolve().parent
INSTALLER_DIR = PROJECT_ROOT / "frontend" / "src-tauri" / "target" / "release" / "bundle" / "nsis"

class RunRequest(BaseModel):
    inputs: Dict[str, Any]
    await_human: bool = False
    runtime_mode: str = "agentic"
    provider: Optional[str] = None
    model: Optional[str] = None


class ProviderTestRequest(BaseModel):
    model: Optional[str] = None


class RunReplyRequest(BaseModel):
    response: Dict[str, Any]


class ApprovalResolutionRequest(BaseModel):
    status: Literal["approved", "rejected", "approve", "reject", "accepted", "denied", "deny"] = "approved"
    scope: Literal["single", "run_scope"] = "single"


class GoalRequest(BaseModel):
    inputs: Dict[str, Any]
    workspace: str = "default"
    priority: int = 50
    runtime_mode: str = "agentic"
    provider: Optional[str] = None
    model: Optional[str] = None
    parent_goal_id: Optional[str] = None
    max_retries: int = 2
    retry_delay_seconds: int = 30
    scheduled_at: Optional[str] = None
    session_id: Optional[str] = None


class SessionRequest(BaseModel):
    workspace: str = "default"
    objective: Optional[str] = None
    context: Optional[Dict[str, Any]] = None
    phases: Optional[list] = None
    ttl_hours: int = 168


class SessionRunRequest(BaseModel):
    inputs: Dict[str, Any]
    workspace: str = "default"
    objective: Optional[str] = None
    runtime_mode: str = "agentic"
    provider: Optional[str] = None
    model: Optional[str] = None


# WebSocket broadcaster state
@app.on_event("startup")
async def startup():
    app.state.connections = set()
    app.state.broadcast_queue = asyncio.Queue()
    app.state.broadcaster = asyncio.create_task(broadcaster_task())
    global runtime
    runtime = RuntimeManager(emit_event, credential_store=credential_store, provider_client=provider_client)
    await runtime.rehydrate_runs()
    await runtime.start_daemon()


@app.on_event("shutdown")
async def shutdown():
    if runtime is not None:
        await runtime.stop_daemon()
    task = getattr(app.state, 'broadcaster', None)
    if task:
        task.cancel()


async def broadcaster_task():
    while True:
        msg = await app.state.broadcast_queue.get()
        to_remove = []
        for ws in list(app.state.connections):
            try:
                await ws.send_text(json.dumps(msg, default=str))
            except Exception:
                to_remove.append(ws)
        for r in to_remove:
            app.state.connections.discard(r)


async def emit_event(message: Dict[str, Any]):
    await app.state.broadcast_queue.put(jsonable_encoder(message))


@app.websocket('/ws')
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    app.state.connections.add(ws)
    try:
        while True:
            data = await ws.receive_text()
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                payload = {"type": "raw", "payload": data}
            if payload.get("type") == "ping":
                await ws.send_text(json.dumps({"type": "pong", "payload": {}}))
    except WebSocketDisconnect:
        app.state.connections.discard(ws)


@app.post('/run')
async def run(req: RunRequest):
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    inputs = dict(req.inputs)
    if req.await_human:
        inputs["force_approval"] = True
    request_id = await runtime.submit_run(
        inputs,
        runtime_mode=req.runtime_mode,
        provider=req.provider,
        model=req.model,
    )
    state = await runtime.get_state(request_id)
    return JSONResponse({"request_id": request_id, "state": jsonable_encoder(state)})


@app.get('/state/{request_id}')
async def get_state(request_id: str):
    if runtime is not None:
        s = await runtime.get_state(request_id)
    else:
        s = await persistence.get_kv(f"state:{request_id}")
    if s is None:
        raise HTTPException(404, "Not found")
    return s


@app.get('/runs')
async def list_runs():
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    return {"runs": await runtime.list_runs()}


@app.post('/run/{request_id}/cancel')
async def cancel_run(request_id: str):
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    try:
        result = await runtime.cancel_run(request_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="run not found")
    state = await runtime.get_state(request_id)
    return {"ok": True, "result": result, "state": jsonable_encoder(state)}


@app.delete('/run/{request_id}')
async def delete_run(request_id: str):
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    try:
        result = await runtime.delete_run(request_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="run not found")
    return {"ok": True, "result": result}


@app.post('/run/{request_id}/reply')
async def reply_run(request_id: str, payload: RunReplyRequest):
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    try:
        result = await runtime.reply_to_run(request_id, payload.response)
    except KeyError:
        raise HTTPException(status_code=404, detail="run not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    state = await runtime.get_state(request_id)
    return {"ok": True, "result": result, "state": jsonable_encoder(state)}


@app.post('/run/{request_id}/resume')
async def resume_run(request_id: str):
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    try:
        result = await runtime.resume_run(request_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="run not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    state = await runtime.get_state(request_id)
    return {"ok": True, "result": result, "state": jsonable_encoder(state)}


@app.post('/goals')
async def enqueue_goal(req: GoalRequest):
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    goal = await runtime.enqueue_goal(
        req.inputs,
        workspace=req.workspace,
        priority=req.priority,
        runtime_mode=req.runtime_mode,
        provider=req.provider,
        model=req.model,
        parent_goal_id=req.parent_goal_id,
        max_retries=req.max_retries,
        retry_delay_seconds=req.retry_delay_seconds,
        scheduled_at=req.scheduled_at,
        session_id=req.session_id,
    )
    return JSONResponse({"goal": jsonable_encoder(goal)})


@app.get('/goals')
async def list_goals(workspace: Optional[str] = None, status: Optional[str] = None, limit: int = 50):
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    goals = await runtime.list_goals(workspace=workspace, status=status, limit=limit)
    return {"goals": jsonable_encoder(goals)}


@app.get('/goals/{goal_id}')
async def get_goal(goal_id: str):
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    goal = await runtime.goal_queue.get_goal(goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="goal not found")
    return jsonable_encoder(goal)


@app.post('/goals/{goal_id}/cancel')
async def cancel_goal(goal_id: str):
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    try:
        result = await runtime.cancel_goal(goal_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="goal not found")
    return {"ok": True, "result": jsonable_encoder(result)}


@app.post('/sessions')
async def create_session(req: SessionRequest):
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    session = await runtime.create_session(
        workspace=req.workspace,
        objective=req.objective,
        context=req.context,
        phases=req.phases,
        ttl_hours=req.ttl_hours,
    )
    return JSONResponse({"session": jsonable_encoder(session)})


@app.get('/sessions')
async def list_sessions(workspace: Optional[str] = None, status: Optional[str] = None, limit: int = 50):
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    sessions = await runtime.list_sessions(workspace=workspace, status=status, limit=limit)
    return {"sessions": jsonable_encoder(sessions)}


@app.get('/sessions/{session_id}')
async def get_session(session_id: str):
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    session = await runtime.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return jsonable_encoder(session)


@app.post('/sessions/{session_id}/close')
async def close_session(session_id: str):
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    result = await runtime.close_session(session_id)
    if result is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {"ok": True, "session": jsonable_encoder(result)}


@app.post('/sessions/{session_id}/checkpoint')
async def session_checkpoint(session_id: str, payload: Dict[str, Any]):
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    result = await runtime.session_checkpoint(session_id, payload)
    if result is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {"ok": True, "session": jsonable_encoder(result)}


@app.post('/sessions/run')
async def submit_session_run(req: SessionRunRequest):
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    result = await runtime.submit_run_with_session(
        req.inputs,
        workspace=req.workspace,
        objective=req.objective,
        runtime_mode=req.runtime_mode,
        provider=req.provider,
        model=req.model,
    )
    return JSONResponse({"result": jsonable_encoder(result)})


@app.get('/daemon/status')
async def daemon_status():
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    pending = await runtime.goal_queue.pending_count()
    active_sessions = await runtime.list_sessions(status="active")
    return {
        "daemon_running": runtime._daemon_running,
        "pending_goals": pending,
        "active_sessions": len(active_sessions),
        "active_runs": len(runtime.active_runs),
    }


@app.post('/approval/{approval_id}')
async def set_approval(approval_id: str, payload: ApprovalResolutionRequest):
    aliases = {
        "approve": "approved",
        "accepted": "approved",
        "reject": "rejected",
        "deny": "rejected",
        "denied": "rejected",
    }
    status = aliases.get(payload.status, payload.status)
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    try:
        result = await runtime.resolve_approval(approval_id, status, scope=payload.scope)
    except KeyError:
        raise HTTPException(status_code=404, detail="approval not found")
    return {"ok": True, "approval": result}


@app.get('/health')
async def healthcheck():
    return {"ok": True, "service": "agente-desktop-backend"}


def _latest_windows_installer_path() -> Optional[Path]:
    if not INSTALLER_DIR.exists():
        return None
    candidates = sorted(INSTALLER_DIR.glob("*.exe"), key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _has_module(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _resolved_public_base_url() -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    host = _backend_bind_host()
    port = _backend_port()
    if host not in {"0.0.0.0", "::"}:
        return f"http://{host}:{port}"
    lan_ips = _private_ipv4_addresses()
    if lan_ips:
        return f"http://{lan_ips[0]}:{port}"
    return f"http://127.0.0.1:{port}"


def _network_payload() -> Dict[str, Any]:
    bind_host = _backend_bind_host()
    port = _backend_port()
    lan_ips = _private_ipv4_addresses()
    suggested_urls = [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]
    suggested_urls.extend(f"http://{ip}:{port}" for ip in lan_ips)
    public_base_url = _resolved_public_base_url()
    return {
        "bind_host": bind_host,
        "port": port,
        "lan_enabled": LAN_ENABLED,
        "public_base_url": public_base_url,
        "ws_url": public_base_url.replace("http", "ws", 1) + "/ws",
        "local_ipv4": lan_ips,
        "suggested_urls": suggested_urls,
        "cors": {
            "allow_origins": allow_origins,
            "allow_origin_regex": _lan_origin_regex() if LAN_ENABLED else None,
        },
    }


async def _doctor_payload() -> Dict[str, Any]:
    providers = await credential_store.list_provider_settings()
    installer = _latest_windows_installer_path()
    capabilities = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "is_admin": _is_admin(),
        "installer_available": installer is not None,
        "desktop_host": installer.name if installer else None,
        "providers_configured": len([item for item in providers if item.get("configured")]),
        "dependencies": {
            "playwright": _has_module("playwright"),
            "pywinauto": _has_module("pywinauto"),
            "win32com": _has_module("win32com"),
            "pypdf": _has_module("pypdf"),
            "extract_msg": _has_module("extract_msg"),
            "pillow": _has_module("PIL"),
            "pytesseract": _has_module("pytesseract"),
            "pymupdf": _has_module("fitz"),
        },
        "runtime": {
            "rehydrated_runs": len(await persistence.list_states()),
            "active_connections": len(getattr(app.state, "connections", [])),
            "network": _network_payload(),
        },
    }
    warnings = []
    if not capabilities["providers_configured"]:
        warnings.append("Nenhum provedor de IA esta configurado; o modo agentic vai cair para assistencia manual.")
    if not capabilities["dependencies"]["pywinauto"]:
        warnings.append("UI Automation nativa ainda nao esta pronta porque pywinauto nao esta instalado.")
    if not capabilities["dependencies"]["win32com"]:
        warnings.append("Office COM nao esta disponivel; automacoes de Word/Excel/Outlook ficarao limitadas.")
    if not (capabilities["dependencies"]["pillow"] and capabilities["dependencies"]["pytesseract"]):
        warnings.append("OCR de imagens/PDFs escaneados ficara limitado sem Pillow e pytesseract.")
    return {"ok": len(warnings) == 0, "capabilities": capabilities, "warnings": warnings}


@app.get('/downloads/windows-installer/meta')
async def windows_installer_meta():
    installer = _latest_windows_installer_path()
    if installer is None:
        return {"available": False, "filename": None}
    return {"available": True, "filename": installer.name}


@app.get('/downloads/windows-installer')
async def download_windows_installer():
    installer = _latest_windows_installer_path()
    if installer is None:
        raise HTTPException(status_code=404, detail="windows installer not found")
    return FileResponse(path=installer, filename=installer.name, media_type="application/octet-stream")


@app.post('/request_approval')
async def request_approval(payload: Dict[str, Any]):
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    approval = await runtime.request_manual_approval(payload)
    return {"approval_id": approval["approval_id"]}


@app.get('/approvals')
async def list_approvals(request_id: Optional[str] = None):
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    return {"approvals": await runtime.list_approvals(request_id=request_id)}


@app.get('/run/{request_id}/approval-grants')
async def list_run_approval_grants(request_id: str):
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    try:
        grants = await runtime.list_approval_grants(request_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="run not found")
    return {"approval_grants": grants}


@app.delete('/run/{request_id}/approval-grants/{grant_id}')
async def revoke_run_approval_grant(request_id: str, grant_id: str):
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    try:
        result = await runtime.revoke_approval_grant(request_id, grant_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="approval grant not found")
    state = await runtime.get_state(request_id)
    return {"ok": True, "result": result, "state": jsonable_encoder(state)}


@app.get('/tools')
async def list_tools():
    return {"tools": tool_definitions()}


@app.get('/diagnostics/doctor')
async def diagnostics_doctor():
    return await _doctor_payload()


@app.get('/diagnostics/network')
async def diagnostics_network():
    return {"ok": True, "network": _network_payload()}


@app.get('/onboarding/capabilities')
async def onboarding_capabilities():
    payload = await _doctor_payload()
    capabilities = payload["capabilities"]
    return {
        "steps": [
            {
                "id": "provider",
                "label": "Configurar um provedor de IA",
                "ready": capabilities["providers_configured"] > 0,
            },
            {
                "id": "uia",
                "label": "Instalar dependencias de UI Automation nativa",
                "ready": capabilities["dependencies"]["pywinauto"],
            },
            {
                "id": "office",
                "label": "Habilitar automacao Office COM",
                "ready": capabilities["dependencies"]["win32com"],
            },
            {
                "id": "desktop_host",
                "label": "Instalar o app desktop",
                "ready": capabilities["installer_available"],
            },
        ],
        "doctor": payload,
    }


@app.get('/settings/providers')
async def list_settings_providers():
    providers = await credential_store.list_provider_settings()
    return {"providers": providers}


@app.post('/settings/providers/{provider}/credential')
async def save_provider_credential(provider: str, payload: CredentialPayload):
    try:
        settings = await credential_store.save_credential(provider, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"provider": settings}


@app.delete('/settings/providers/{provider}/credential')
async def delete_provider_credential(provider: str):
    try:
        await credential_store.delete_credential(provider)
        settings = await credential_store.get_provider_settings(provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True, "provider": settings}


@app.post('/settings/providers/{provider}/test')
async def test_provider_connection(provider: str, payload: ProviderTestRequest):
    try:
        config = await credential_store.resolve_runtime_config(provider, model=payload.model)
        result = await provider_client.test_connection(
            provider=config["provider"],
            api_key=config["api_key"],
            base_url=config.get("base_url"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return result
