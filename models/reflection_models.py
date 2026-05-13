# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from pydantic import BaseModel
from typing import Dict, Any, Optional


class ReflectionReport(BaseModel):
    verdict: str  # success | retry | failed | partial
    details: Dict[str, Any] = {}
    checks: Dict[str, Any] = {}
    recommended_action: Optional[Dict[str, Any]] = None
