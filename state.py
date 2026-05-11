from typing import TypedDict, Optional, Dict, Any
from pydantic import BaseModel
from datetime import datetime
import json


class AgentState(TypedDict):
    request_id: str
    created_at: str
    last_updated: str
    inputs: Dict[str, Any]
    outputs: Dict[str, Any]
    current_node: Optional[str]
    history: Dict[str, Any]
    approvals: Dict[str, str]


class StateModel(BaseModel):
    request_id: str
    created_at: str
    last_updated: str
    inputs: Dict[str, Any] = {}
    outputs: Dict[str, Any] = {}
    current_node: Optional[str] = None
    history: Dict[str, Any] = {}
    approvals: Dict[str, str] = {}

    def to_typed(self) -> AgentState:
        return AgentState(**json.loads(self.json()))
