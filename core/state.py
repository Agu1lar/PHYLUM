from typing import TypedDict, Optional, Dict, Any, List
from pydantic import BaseModel
from datetime import datetime
import json


class TaskState(TypedDict, total=False):
    id: str
    title: str
    tool: str
    action: str
    params: Dict[str, Any]
    status: str
    attempt: int
    max_attempts: int
    recovery: Dict[str, Any]
    requires_approval: bool
    approval_id: Optional[str]
    result: Optional[Dict[str, Any]]
    error: Optional[str]
    reflection: Optional[Dict[str, Any]]


class ApprovalState(TypedDict, total=False):
    approval_id: str
    request_id: str
    task_id: str
    title: str
    reason: str
    status: str


class HandoffOption(TypedDict, total=False):
    id: str
    label: str
    value: Any


class HandoffState(TypedDict, total=False):
    handoff_id: str
    request_id: str
    task_id: Optional[str]
    kind: str
    title: str
    prompt: str
    reason: Optional[str]
    status: str
    allow_free_text: bool
    options: List[HandoffOption]
    response: Optional[Dict[str, Any]]


class AgentSessionState(TypedDict, total=False):
    step: int
    messages: List[Dict[str, Any]]
    paused_reason: Optional[str]
    last_tool_call_id: Optional[str]


class HistoryEvent(TypedDict, total=False):
    type: str
    timestamp: str
    payload: Dict[str, Any]


class AgentState(TypedDict):
    request_id: str
    created_at: str
    last_updated: str
    status: str
    runtime_mode: str
    provider: Optional[str]
    model: Optional[str]
    inputs: Dict[str, Any]
    outputs: Dict[str, Any]
    current_node: Optional[str]
    current_task_id: Optional[str]
    tasks: List[TaskState]
    history: List[HistoryEvent]
    approvals: List[ApprovalState]
    handoffs: List[HandoffState]
    pending_handoff: Optional[HandoffState]
    agent_session: Dict[str, Any]
    recovery: Dict[str, Any]
    error: Optional[str]


class StateModel(BaseModel):
    request_id: str
    created_at: str
    last_updated: str
    status: str = "pending"
    runtime_mode: str = "heuristic"
    provider: Optional[str] = None
    model: Optional[str] = None
    inputs: Dict[str, Any] = {}
    outputs: Dict[str, Any] = {}
    current_node: Optional[str] = None
    current_task_id: Optional[str] = None
    tasks: List[Dict[str, Any]] = []
    history: List[Dict[str, Any]] = []
    approvals: List[Dict[str, Any]] = []
    handoffs: List[Dict[str, Any]] = []
    pending_handoff: Optional[Dict[str, Any]] = None
    agent_session: Dict[str, Any] = {}
    recovery: Dict[str, Any] = {}
    error: Optional[str] = None

    def to_typed(self) -> AgentState:
        return AgentState(**json.loads(self.json()))
