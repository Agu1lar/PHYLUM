# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Learn intent profiles from successful agentic runs (user overlay in ~/.agente/)."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from intent_classifier import normalize_user_text
from intent_profile_registry import (
    IntentProfileRegistry,
    profile_from_dict,
    user_intent_profiles_dir,
)
from intent_routing import intent_learning_enabled

logger = logging.getLogger(__name__)

_LEARN_INDEX = "_learn_index.json"
_MIN_USER_CHARS = 12
_MIN_SUCCESSES = 2
_SUCCESS_STATUSES = frozenset({"completed", "partial", "succeeded"})
_STOPWORDS = frozenset({
    "para", "como", "qual", "quero", "preciso", "pode", "fazer", "isso", "essa", "esse",
    "the", "and", "for", "with", "that", "this", "from", "your", "please", "meus", "minha",
    "uma", "uns", "das", "dos", "por", "mais", "muito", "sobre",
})
_WORD_RE = re.compile(r"[a-z0-9]{3,}")


def _learn_index_path() -> Path:
    return user_intent_profiles_dir() / _LEARN_INDEX


def _load_index() -> Dict[str, Any]:
    path = _learn_index_path()
    if not path.is_file():
        return {"patterns": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("patterns"), dict):
            return data
    except (json.JSONDecodeError, OSError):
        logger.warning("Corrupt intent learn index at %s — resetting", path)
    return {"patterns": {}}


def _save_index(data: Dict[str, Any]) -> None:
    root = user_intent_profiles_dir()
    root.mkdir(parents=True, exist_ok=True)
    _learn_index_path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _extract_terms(text: str, *, limit: int = 10) -> List[str]:
    normalized = normalize_user_text(text)
    terms: List[str] = []
    seen: Set[str] = set()

    for match in _WORD_RE.finditer(normalized):
        word = match.group(0)
        if word in _STOPWORDS or word in seen:
            continue
        seen.add(word)
        terms.append(word)
        if len(terms) >= limit:
            break

    for needle in ("nao lid", "não lid", "unread", "download", "outlook", "email", "driver", "process"):
        if needle in normalized and needle not in seen:
            seen.add(needle)
            terms.insert(0, needle)

    return terms[:limit]


def _dominant_completed_task(tasks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    completed = [
        task for task in tasks
        if str(task.get("status") or "").lower() in _SUCCESS_STATUSES
        and task.get("tool") not in {None, "", "request_user_input"}
    ]
    if not completed:
        return None
    return completed[-1]


def _pattern_key(user_text: str, task: Dict[str, Any]) -> str:
    tool = str(task.get("tool") or "")
    action = str(task.get("action") or "")
    normalized = normalize_user_text(user_text)[:200]
    raw = f"{tool}|{action}|{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _profile_id_for_key(pattern_key: str) -> str:
    return f"learned_{pattern_key}"


def build_learned_profile_draft(
    *,
    user_text: str,
    task: Dict[str, Any],
    pattern_key: str,
) -> Dict[str, Any]:
    tool = str(task["tool"])
    action = str(task["action"])
    params = dict(task.get("params") or {})
    terms = _extract_terms(user_text)
    profile_id = _profile_id_for_key(pattern_key)

    return {
        "id": profile_id,
        "domain": tool,
        "description": f"Learned from successful runs ({tool}.{action}).",
        "version": "1",
        "source": "learned",
        "required_tools": [tool],
        "default_action": {
            "tool": tool,
            "action": action,
            "params": {k: v for k, v in params.items() if v is not None},
        },
        "param_defaults": params,
        "confidence_threshold": 0.78,
        "signals": {
            "require_any": terms[:5] if terms else [tool],
            "prefer_any": terms[5:10],
            "min_chars": _MIN_USER_CHARS,
        },
    }


def propose_learned_profile(
    *,
    user_text: str,
    tasks: List[Dict[str, Any]],
    intent_accepted: bool,
) -> Optional[Dict[str, Any]]:
    """Build a profile draft if this run is worth learning from."""
    if not intent_learning_enabled():
        return None
    text = (user_text or "").strip()
    if len(text) < _MIN_USER_CHARS:
        return None
    if intent_accepted:
        return None

    task = _dominant_completed_task(tasks)
    if task is None:
        return None

    pattern_key = _pattern_key(text, task)
    return build_learned_profile_draft(user_text=text, task=task, pattern_key=pattern_key)


def maybe_promote_learned_profile(
    *,
    user_text: str,
    tasks: List[Dict[str, Any]],
    intent_accepted: bool,
    execution_mode: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Record successful agentic patterns; after MIN_SUCCESSES writes profile JSON under ~/.agente/.
    Returns promotion metadata when a file is written.
    """
    if execution_mode == "intent_fast_path":
        return None

    draft = propose_learned_profile(
        user_text=user_text,
        tasks=tasks,
        intent_accepted=intent_accepted,
    )
    if draft is None:
        return None

    task = _dominant_completed_task(tasks) or {}
    pattern_key = _pattern_key(user_text, task)
    index = _load_index()
    patterns: Dict[str, Any] = index.setdefault("patterns", {})
    entry = patterns.setdefault(pattern_key, {"count": 0, "profile_id": draft["id"]})
    entry["count"] = int(entry.get("count") or 0) + 1
    entry["last_user_text"] = user_text[:500]
    entry["tool"] = draft["default_action"]["tool"]
    entry["action"] = draft["default_action"]["action"]
    _save_index(index)

    if entry["count"] < _MIN_SUCCESSES:
        logger.debug(
            "Intent learn pattern %s at %d/%d successes",
            pattern_key,
            entry["count"],
            _MIN_SUCCESSES,
        )
        return {"status": "recorded", "pattern_key": pattern_key, "count": entry["count"]}

    profile_path = user_intent_profiles_dir() / f"{draft['id']}.json"
    if profile_path.is_file():
        return {"status": "already_exists", "profile_id": draft["id"], "path": str(profile_path)}

    profile_path.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        profile_from_dict(draft, source=profile_path)
    except ValueError as exc:
        profile_path.unlink(missing_ok=True)
        logger.warning("Learned profile validation failed: %s", exc)
        return None

    logger.info("Promoted learned intent profile %s -> %s", pattern_key, profile_path)
    IntentProfileRegistry.reload_default()
    return {
        "status": "promoted",
        "profile_id": draft["id"],
        "path": str(profile_path),
        "pattern_key": pattern_key,
        "count": entry["count"],
    }


def learn_from_run_state(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Entry point after a successful run."""
    user_text = str(state.get("inputs", {}).get("text") or state.get("inputs", {}).get("prompt") or "")
    intent = state.get("_intent_classification") or {}
    intent_accepted = bool(intent.get("accepted"))
    execution_mode = (
        state.get("outputs", {}).get("execution_mode")
        or state.get("agent_session", {}).get("execution_mode")
    )
    return maybe_promote_learned_profile(
        user_text=user_text,
        tasks=list(state.get("tasks") or []),
        intent_accepted=intent_accepted,
        execution_mode=execution_mode,
    )
