# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Intent routing: fast path vs full agentic loop (Fase 1.3–1.4)."""
from __future__ import annotations

import os
from typing import Any, Dict

from intent_classifier import IntentClassification


def intent_fast_path_enabled() -> bool:
    return os.getenv("AGENTE_INTENT_FAST_PATH", "1").strip().lower() not in ("0", "false", "no", "off")


def intent_learning_enabled() -> bool:
    return os.getenv("AGENTE_INTENT_LEARN", "1").strip().lower() not in ("0", "false", "no", "off")


def resolve_intent_routing(classification: IntentClassification) -> Dict[str, Any]:
    """Choose execution mode before the agentic loop runs."""
    if classification.accepted and classification.profile is not None:
        if intent_fast_path_enabled():
            return {
                "mode": "fast_path",
                "profile_id": classification.profile_id,
                "domain": classification.profile.domain,
                "confidence": classification.confidence,
                "threshold": classification.threshold,
            }
        return {
            "mode": "agentic",
            "profile_id": classification.profile_id,
            "fallback_reason": "fast_path_disabled",
            "confidence": classification.confidence,
        }

    return {
        "mode": "agentic",
        "profile_id": None,
        "fallback_reason": classification.reason or "no_matching_profile",
        "confidence": classification.confidence,
    }
