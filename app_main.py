import asyncio
import json
import logging
from typing import Any, Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse
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

# Allow localhost origins for development (adjust in production)
allow_origins = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

persistence = Persistence.get()
credential_store = CredentialStore(persistence)
provider_client = MultiProviderClient()
runtime: Optional[RuntimeManager] = None

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


# WebSocket broadcaster state
@app.on_event("startup")
async def startup():
    app.state.connections = set()
    app.state.broadcast_queue = asyncio.Queue()
    app.state.broadcaster = asyncio.create_task(broadcaster_task())
    global runtime
    runtime = RuntimeManager(emit_event, credential_store=credential_store, provider_client=provider_client)
    await runtime.rehydrate_runs()


@app.on_event("shutdown")
async def shutdown():
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


@app.post('/approval/{approval_id}')
async def set_approval(approval_id: str, payload: dict):
    status = payload.get('status', 'approved')
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime not ready")
    try:
        result = await runtime.resolve_approval(approval_id, status)
    except KeyError:
        raise HTTPException(status_code=404, detail="approval not found")
    return {"ok": True, "approval": result}


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


@app.get('/tools')
async def list_tools():
    return {"tools": tool_definitions()}


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
