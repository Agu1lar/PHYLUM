from pydantic import BaseModel
from typing import Dict, Any, Optional


class ReflectionReport(BaseModel):
    verdict: str  # success | retry | failed | partial
    details: Dict[str, Any] = {}
    checks: Dict[str, Any] = {}
    recommended_action: Optional[Dict[str, Any]] = None
