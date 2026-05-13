# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
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


class GoalPhase(BaseModel):
    """A phase within a multi-phase goal decomposition."""
    phase_id: str
    title: str
    description: str
    tasks: List[Task] = Field(default_factory=list)
    depends_on_phases: List[str] = Field(default_factory=list)
    priority: int = 50
    status: str = "pending"
    estimated_complexity: str = "medium"

    @validator('phase_id')
    def phase_id_must_not_be_empty(cls, v):
        if not v or not v.strip():
            raise ValueError('phase_id required')
        return v


class GoalDecomposition(BaseModel):
    """Result of decomposing a complex goal into phases."""
    original_text: str
    goal_type: str = "complex"
    phases: List[GoalPhase] = Field(default_factory=list)
    total_estimated_steps: int = 0
    requires_long_running: bool = False
    workspace: str = "default"
