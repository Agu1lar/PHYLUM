# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Backward-compatible re-export — selection logic lives in ``llm_payload_planner`` (Fase 2.2)."""
from __future__ import annotations

from llm_payload_planner import select_tools_for_request

__all__ = ["select_tools_for_request"]
