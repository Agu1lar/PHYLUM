import asyncio
import json
import logging
import uuid
from typing import Any, Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel

from builder import GraphBuilder
from agent_persistence import Persistence
from agent_executor import ExecutionEngine
from nodes_planner import PlannerNode
from nodes_safety import SafetyNode
from nodes_shell import ShellNode
from nodes_filesystem import FileSystemNode
from nodes_memory import MemoryNode
from nodes_reflection import ReflectionNode
from nodes_browser import BrowserNode
from nodes_os import OSNode

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

# build graph
builder = GraphBuilder()
builder.add_node('planner', PlannerNode('planner'))
builder.add_node('safety', SafetyNode('safety'))
builder.add_node('shell', ShellNode('shell'))
builder.add_node('filesystem', FileSystemNode('filesystem'))
builder.add_node('memory', MemoryNode('memory'))
builder.add_node('reflection', ReflectionNode('reflection'))
builder.add_node('browser', BrowserNode('browser'))
builder.add_node('os', OSNode('os'))
# edges
builder.add_edge('planner', 'safety')
builder.add_edge('safety', 'shell', meta={"retries": 2})
builder.add_edge('shell', 'filesystem', parallel=True)
builder.add_edge('shell', 'memory', parallel=True)
builder.add_edge('filesystem', 'reflection')

graph = builder.build()
engine = ExecutionEngine(graph)

persistence = Persistence.get()

class RunRequest(BaseModel):
    inputs: dict
    await_human: bool = False


# WebSocket broadcaster state
@app.on_event("startup")
async def startup():
    app.state.connections = set()
    app.state.broadcast_queue = asyncio.Queue()
    app.state.broadcaster = asyncio.create_task(broadcaster_task())


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


@app.websocket('/ws')
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    app.state.connections.add(ws)
    try:
        while True:
            data = await ws.receive_text()
            # echo back or ignore
            await ws.send_text(json.dumps({"received": data}))
    except WebSocketDisconnect:
        app.state.connections.discard(ws)


@app.post('/run')
async def run(req: RunRequest):
    request_id = str(uuid.uuid4())
    state = {"request_id": request_id, "created_at": None, "last_updated": None, "inputs": req.inputs, "outputs": {}, "history": {}}
    await persistence.save_kv(f"state:{request_id}", state)

    # notify websocket listeners
    await app.state.broadcast_queue.put({"event": "run_started", "request_id": request_id, "inputs": req.inputs})

    # schedule execution
    loop = asyncio.get_event_loop()
    loop.create_task(engine.execute('planner', state))
    return JSONResponse({"request_id": request_id})


@app.get('/state/{request_id}')
async def get_state(request_id: str):
    s = await persistence.get_kv(f"state:{request_id}")
    if s is None:
        raise HTTPException(404, "Not found")
    return s


@app.post('/approval/{approval_id}')
async def set_approval(approval_id: str, payload: dict):
    await persistence.set_approval(approval_id, payload.get('status', 'approved'))
    return {"ok": True}


# Simple tool run endpoint (for manual testing)
TOOLS = {
    "filesystem": FileSystemNode('filesystem'),
    "shell": ShellNode('shell'),
}


@app.post('/run/tool/{tool_name}')
async def run_tool(tool_name: str, payload: Dict[str, Any]):
    tool = TOOLS.get(tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail="tool not found")
    # broadcast start
    await app.state.broadcast_queue.put({"event": "tool_started", "tool": tool_name, "payload": payload})
    try:
        # tool.run expects payload dict via the tool implementation
        result = await tool.execute({'inputs': payload})
        out = result
        await app.state.broadcast_queue.put({"event": "tool_finished", "tool": tool_name, "result": out})
        return JSONResponse(content=out)
    except Exception as exc:
        logger.exception("Tool run failed")
        await app.state.broadcast_queue.put({"event": "tool_failed", "tool": tool_name, "error": str(exc)})
        raise HTTPException(status_code=500, detail=str(exc))
