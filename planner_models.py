from pydantic import BaseModel, Field, validator
from typing import Optional, Dict, Any, List


class Task(BaseModel):
    id: str
    tool: str
    action: str
    params: Dict[str, Any] = Field(default_factory=dict)
    depends_on: List[str] = Field(default_factory=list)
    priority: int = 50

    @validator('id')
    def id_must_not_be_empty(cls, v):
        if not v or not v.strip():
            raise ValueError('id required')
        return v


class Plan(BaseModel):
    original_text: str
    tasks: List[Task]


class ValidationResult(BaseModel):
    ok: bool
    errors: Optional[List[str]] = None
    warnings: Optional[List[str]] = None
