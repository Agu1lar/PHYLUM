# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Semantic verification layer.

Three complementary verification strategies applied after tool execution:

1. **GoalVerifier** — "was the objective actually achieved?"
   Uses task intent + action_result to judge whether the *user's goal*
   is satisfied, not just whether the tool returned success.

2. **SemanticValidator** — "does the result make sense in context?"
   Checks whether the result data is consistent with the action type
   (e.g. a write action should have a non-empty target path, a read
   action should return data).

3. **PostconditionChecker** — "did the expected side-effect happen?"
   For mutations with observable postconditions (file created, email
   sent, process running), performs a lightweight check to confirm.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable, Awaitable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Postcondition rule registry
# ---------------------------------------------------------------------------

PostconditionFn = Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], Awaitable[Dict[str, Any]]]

_POSTCONDITION_REGISTRY: Dict[str, PostconditionFn] = {}


def postcondition(effect_kind: str):
    """Decorator that registers an async postcondition checker for *effect_kind*."""
    def decorator(fn: PostconditionFn) -> PostconditionFn:
        _POSTCONDITION_REGISTRY[effect_kind] = fn
        return fn
    return decorator


# --- Built-in postcondition checkers ---

@postcondition("write_file")
async def _check_write_file(task: Dict[str, Any], result: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    path = (task.get("params") or {}).get("path") or ""
    if not path:
        return {"passed": False, "reason": "no target path specified", "check": "file_exists"}
    exists = await asyncio.to_thread(os.path.isfile, path)
    if not exists:
        return {"passed": False, "reason": f"file not found after write: {path}", "check": "file_exists"}
    try:
        size = await asyncio.to_thread(os.path.getsize, path)
    except OSError:
        size = -1
    return {"passed": True, "reason": f"file exists ({size} bytes)", "check": "file_exists", "size": size}


@postcondition("copy_file")
async def _check_copy_file(task: Dict[str, Any], result: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    dest = (task.get("params") or {}).get("dest") or ""
    if not dest:
        return {"passed": False, "reason": "no destination path specified", "check": "dest_exists"}
    exists = await asyncio.to_thread(os.path.exists, dest)
    return {
        "passed": exists,
        "reason": f"destination {'exists' if exists else 'not found'}: {dest}",
        "check": "dest_exists",
    }


@postcondition("move_file")
async def _check_move_file(task: Dict[str, Any], result: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    dest = (task.get("params") or {}).get("dest") or ""
    src = (task.get("params") or {}).get("path") or ""
    checks: List[Dict[str, Any]] = []
    if dest:
        dest_exists = await asyncio.to_thread(os.path.exists, dest)
        checks.append({"check": "dest_exists", "passed": dest_exists, "path": dest})
    if src:
        src_gone = not await asyncio.to_thread(os.path.exists, src)
        checks.append({"check": "source_removed", "passed": src_gone, "path": src})
    all_passed = all(c["passed"] for c in checks) if checks else False
    return {
        "passed": all_passed,
        "reason": "; ".join(f"{c['check']}: {'ok' if c['passed'] else 'failed'}" for c in checks),
        "check": "move_verified",
        "details": checks,
    }


@postcondition("delete_file")
async def _check_delete_file(task: Dict[str, Any], result: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    path = (task.get("params") or {}).get("path") or ""
    if not path:
        return {"passed": False, "reason": "no target path specified", "check": "file_removed"}
    exists = await asyncio.to_thread(os.path.exists, path)
    return {
        "passed": not exists,
        "reason": f"path {'still exists' if exists else 'removed'}: {path}",
        "check": "file_removed",
    }


@postcondition("create_directory")
async def _check_mkdir(task: Dict[str, Any], result: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    path = (task.get("params") or {}).get("path") or ""
    if not path:
        return {"passed": False, "reason": "no target path specified", "check": "dir_exists"}
    exists = await asyncio.to_thread(os.path.isdir, path)
    return {
        "passed": exists,
        "reason": f"directory {'exists' if exists else 'not found'}: {path}",
        "check": "dir_exists",
    }


# ---------------------------------------------------------------------------
# Semantic Validator
# ---------------------------------------------------------------------------

class SemanticValidator:
    """Checks whether a result is internally consistent with the action type.

    A "succeeded" status from a mutation should have evidence of change.
    A "succeeded" read should have returned data. And so on.
    """

    def validate(self, task: Dict[str, Any], action_result: Dict[str, Any]) -> Dict[str, Any]:
        status = action_result.get("status")
        if status != "succeeded":
            return {"valid": True, "reason": "non-success status — semantic validation deferred"}

        semantic_type = action_result.get("semantic_type", "inspection")
        checks: List[Dict[str, Any]] = []

        if semantic_type == "mutation":
            checks.append(self._check_mutation_evidence(action_result))
        elif semantic_type == "inspection":
            checks.append(self._check_inspection_data(action_result))

        checks.append(self._check_summary_present(action_result))

        failed = [c for c in checks if not c.get("valid", True)]
        return {
            "valid": len(failed) == 0,
            "checks": checks,
            "reason": failed[0]["reason"] if failed else "all semantic checks passed",
        }

    @staticmethod
    def _check_mutation_evidence(action_result: Dict[str, Any]) -> Dict[str, Any]:
        effects = action_result.get("effects") or {}
        if isinstance(effects, dict):
            changed = effects.get("changed", False)
            has_artifacts = bool(effects.get("artifacts"))
            has_after = effects.get("after") is not None
            if changed or has_artifacts or has_after:
                return {"check": "mutation_evidence", "valid": True, "reason": "mutation has evidence of change"}
        target = action_result.get("target") or {}
        if target:
            return {"check": "mutation_evidence", "valid": True, "reason": "mutation has target info"}
        return {
            "check": "mutation_evidence",
            "valid": False,
            "reason": "mutation succeeded but has no evidence of change (no effects.changed, no artifacts, no target)",
        }

    @staticmethod
    def _check_inspection_data(action_result: Dict[str, Any]) -> Dict[str, Any]:
        data = action_result.get("data") or {}
        target = action_result.get("target") or {}
        if data or target:
            return {"check": "inspection_data", "valid": True, "reason": "inspection returned data"}
        summary = action_result.get("summary") or ""
        if len(summary) > 20:
            return {"check": "inspection_data", "valid": True, "reason": "inspection has substantive summary"}
        return {
            "check": "inspection_data",
            "valid": False,
            "reason": "inspection succeeded but returned no data, no target and a short summary",
        }

    @staticmethod
    def _check_summary_present(action_result: Dict[str, Any]) -> Dict[str, Any]:
        summary = (action_result.get("summary") or "").strip()
        if summary:
            return {"check": "summary_present", "valid": True, "reason": "summary is present"}
        return {
            "check": "summary_present",
            "valid": False,
            "reason": "action result has no summary text",
        }


# ---------------------------------------------------------------------------
# Goal Verifier
# ---------------------------------------------------------------------------

class GoalVerifier:
    """Determines whether the user's *actual objective* was satisfied.

    Goes beyond tool success status by examining intent, evidence and
    known patterns where "tool succeeded" != "goal achieved".
    """

    DEFERRED_ACTIONS: Dict[str, Dict[str, Any]] = {
        ("desktop", "open_app"): {
            "strategy": "verify_window_or_process",
            "confidence": 0.45,
            "rationale": "Launch request accepted but the resulting window/process should still be verified.",
            "recommended_followups": ["desktop.wait_for_window", "desktop.list_windows", "desktop.list_processes"],
        },
        ("desktop", "open_path"): {
            "strategy": "verify_window_or_process",
            "confidence": 0.45,
            "rationale": "Open-path request accepted but should verify the resulting window or Explorer.",
            "recommended_followups": ["desktop.list_windows"],
        },
        ("desktop", "open_file"): {
            "strategy": "verify_window_or_process",
            "confidence": 0.45,
            "rationale": "Open-file request accepted but should verify the file is actually open.",
            "recommended_followups": ["desktop.list_windows", "desktop.list_processes"],
        },
        ("office", "outlook_create_draft"): {
            "strategy": "verify_draft_created",
            "confidence": 0.60,
            "rationale": "Draft creation reported success but draft visibility should be confirmed.",
            "recommended_followups": ["office.outlook_search_messages"],
        },
        ("office", "word_create_document"): {
            "strategy": "verify_file_created",
            "confidence": 0.70,
            "rationale": "Document creation reported success; file existence should be confirmed.",
            "recommended_followups": ["filesystem.stat"],
        },
        ("office", "word_export_pdf"): {
            "strategy": "verify_file_created",
            "confidence": 0.70,
            "rationale": "PDF export reported success; output file should be confirmed.",
            "recommended_followups": ["filesystem.stat"],
        },
        ("sandbox", "run_python"): {
            "strategy": "verify_script_output",
            "confidence": 0.55,
            "rationale": "Script execution succeeded but output/artifacts should be validated.",
            "recommended_followups": [],
        },
        ("sandbox", "run_powershell"): {
            "strategy": "verify_script_output",
            "confidence": 0.55,
            "rationale": "Script execution succeeded but output/artifacts should be validated.",
            "recommended_followups": [],
        },
    }

    def verify(self, task: Dict[str, Any], action_result: Dict[str, Any]) -> Dict[str, Any]:
        status = action_result.get("status")
        if status != "succeeded":
            return {
                "satisfied": False,
                "strategy": "defer_to_followup",
                "confidence": 0.0,
                "rationale": "The action itself did not succeed, so the goal cannot be considered satisfied yet.",
                "evidence": {"status": status, "summary": action_result.get("summary")},
                "recommended_followups": [],
            }

        tool = task.get("tool", "")
        action = task.get("action", "")
        evidence = {
            "target": action_result.get("target") or {},
            "data": action_result.get("data") or {},
        }

        deferred = self.DEFERRED_ACTIONS.get((tool, action))
        if deferred:
            return {
                "satisfied": False,
                **deferred,
                "evidence": evidence,
            }

        issue = action_result.get("issue")
        if issue and isinstance(issue, dict) and issue.get("kind"):
            return {
                "satisfied": False,
                "strategy": "issue_present",
                "confidence": 0.30,
                "rationale": f"Tool succeeded but reported an issue: {issue.get('message', issue.get('kind'))}",
                "evidence": evidence,
                "recommended_followups": [],
            }

        effects = action_result.get("effects") or {}
        if isinstance(effects, dict) and effects.get("changed"):
            return {
                "satisfied": True,
                "strategy": "mutation_confirmed",
                "confidence": 0.90,
                "rationale": "Tool succeeded and effects confirm the mutation was applied.",
                "evidence": evidence,
                "recommended_followups": [],
            }

        semantic_type = action_result.get("semantic_type", "inspection")
        if semantic_type == "inspection":
            data = action_result.get("data") or {}
            confidence = 0.85 if data else 0.70
            return {
                "satisfied": True,
                "strategy": "inspection_data",
                "confidence": confidence,
                "rationale": "Inspection completed and returned data." if data else "Inspection completed but returned minimal data.",
                "evidence": evidence,
                "recommended_followups": [],
            }

        return {
            "satisfied": True,
            "strategy": "tool_result",
            "confidence": 0.80,
            "rationale": "The tool returned a successful result and no extra verification hook is required.",
            "evidence": evidence,
            "recommended_followups": [],
        }


# ---------------------------------------------------------------------------
# Postcondition Checker
# ---------------------------------------------------------------------------

class PostconditionChecker:
    """Runs registered postcondition checks for the task's effect_kind.

    Postconditions are lightweight, async-safe probes (file existence,
    process running, etc.) that confirm a mutation's side-effect actually
    materialized.
    """

    def __init__(self, registry: Optional[Dict[str, PostconditionFn]] = None):
        self._registry = registry if registry is not None else _POSTCONDITION_REGISTRY

    async def check(
        self,
        task: Dict[str, Any],
        result: Dict[str, Any],
        *,
        effect_kind: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        ek = effect_kind or (task.get("policy_metadata") or {}).get("effect_kind")
        if not ek:
            from canonical_tools import action_metadata
            meta = action_metadata(task.get("tool", ""), task.get("action", ""))
            ek = meta.get("effect_kind")

        if not ek or ek not in self._registry:
            return {
                "checked": False,
                "effect_kind": ek,
                "reason": "no postcondition rule registered for this effect_kind",
            }

        action_result = result.get("action_result") or {}
        if action_result.get("status") != "succeeded":
            return {
                "checked": False,
                "effect_kind": ek,
                "reason": "postcondition skipped — action did not succeed",
            }

        try:
            check_result = await self._registry[ek](task, result, context or {})
            return {
                "checked": True,
                "effect_kind": ek,
                **check_result,
            }
        except Exception as exc:
            logger.warning("Postcondition check for %s failed: %s", ek, exc)
            return {
                "checked": True,
                "effect_kind": ek,
                "passed": False,
                "reason": f"postcondition probe error: {exc}",
                "check": "exception",
            }

    @property
    def registered_effects(self) -> List[str]:
        return list(self._registry.keys())


# ---------------------------------------------------------------------------
# Unified SemanticVerifier facade
# ---------------------------------------------------------------------------

class SemanticVerifier:
    """Combines goal verification, semantic validation and postcondition
    checking into a single verification pass.

    Returns a unified ``GoalVerification`` dict enriched with semantic
    and postcondition results.
    """

    def __init__(self):
        self.goal_verifier = GoalVerifier()
        self.semantic_validator = SemanticValidator()
        self.postcondition_checker = PostconditionChecker()

    async def verify(
        self,
        task: Dict[str, Any],
        result: Dict[str, Any],
        *,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        action_result = (result.get("action_result") or {}) if isinstance(result, dict) else {}

        goal = self.goal_verifier.verify(task, action_result)
        semantic = self.semantic_validator.validate(task, action_result)
        postcondition = await self.postcondition_checker.check(task, result, context=context)

        if not semantic.get("valid", True) and goal.get("satisfied"):
            goal["satisfied"] = False
            goal["confidence"] = min(goal.get("confidence", 0), 0.40)
            goal["rationale"] = f"Semantic validation failed: {semantic.get('reason')}"
            goal["strategy"] = "semantic_validation_failed"

        if postcondition.get("checked") and not postcondition.get("passed", True) and goal.get("satisfied"):
            goal["satisfied"] = False
            goal["confidence"] = min(goal.get("confidence", 0), 0.35)
            goal["rationale"] = f"Postcondition failed: {postcondition.get('reason')}"
            goal["strategy"] = "postcondition_failed"

        if postcondition.get("checked") and postcondition.get("passed") and goal.get("satisfied"):
            goal["confidence"] = max(goal.get("confidence", 0), 0.95)
            goal["strategy"] = "postcondition_confirmed"
            goal["rationale"] = f"Goal confirmed by postcondition: {postcondition.get('reason')}"

        goal["semantic_validation"] = semantic
        goal["postcondition"] = postcondition

        return goal
