# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Target budgets per task class for LLM payload optimization (Fase 0.4)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_OUTLOOK_RE = re.compile(r"\b(?:outlook|e-?mail|emails|correio)\b", re.I)
_FILE_DELIVERABLE_RE = re.compile(
    r"\b(?:arquivo|file|ficheiro|pasta|folder|download|salvar|save|coloque|guardar|"
    r"escrever|write|export|exportar|gerar|create|criar)\b",
    re.I,
)
_GREETING_RE = re.compile(
    r"^(?:hi|hello|hey|thanks|thank you|ok|okay|yes|no|test|oi|olá|ola|bom dia|boa tarde|boa noite)\s*[!?.]*$",
    re.I,
)
_DRIVER_RE = re.compile(r"\b(?:driver|impressora|printer|instalar|install)\b", re.I)
_MULTI_STEP_RE = re.compile(
    r"(?:^|\n)\s*\d+[\.\)]\s+\S|"
    r"\b(?:first|primeiro|depois|then|em seguida|and then|e depois|por fim)\b",
    re.I,
)


@dataclass(frozen=True)
class TaskBudgetTargets:
    """Soft targets used to detect payload bloat (warnings, not hard stops)."""
    task_class: str
    description: str = ""
    max_prompt_tokens_turn_1: int = 12_000
    max_completion_tokens_turn_1: int = 2_000
    max_tools_json_chars_turn_1: int = 10_000
    max_system_prompt_chars_turn_1: int = 2_500
    max_tools_offered_turn_1: int = 12
    max_total_prompt_tokens_run: int = 40_000
    max_total_tokens_run: int = 50_000
    max_llm_turns: int = 6

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_class": self.task_class,
            "description": self.description,
            "max_prompt_tokens_turn_1": self.max_prompt_tokens_turn_1,
            "max_completion_tokens_turn_1": self.max_completion_tokens_turn_1,
            "max_tools_json_chars_turn_1": self.max_tools_json_chars_turn_1,
            "max_system_prompt_chars_turn_1": self.max_system_prompt_chars_turn_1,
            "max_tools_offered_turn_1": self.max_tools_offered_turn_1,
            "max_total_prompt_tokens_run": self.max_total_prompt_tokens_run,
            "max_total_tokens_run": self.max_total_tokens_run,
            "max_llm_turns": self.max_llm_turns,
        }


DEFAULT_TASK_BUDGETS: Dict[str, TaskBudgetTargets] = {
    "conversation": TaskBudgetTargets(
        task_class="conversation",
        description="Greeting or chat without machine actions.",
        max_prompt_tokens_turn_1=2_000,
        max_tools_json_chars_turn_1=0,
        max_system_prompt_chars_turn_1=1_200,
        max_tools_offered_turn_1=0,
        max_total_prompt_tokens_run=4_000,
        max_total_tokens_run=5_000,
        max_llm_turns=1,
    ),
    "outlook_read": TaskBudgetTargets(
        task_class="outlook_read",
        description="Read or list Outlook/email (no file export).",
        max_prompt_tokens_turn_1=8_000,
        max_tools_json_chars_turn_1=4_500,
        max_system_prompt_chars_turn_1=2_000,
        max_tools_offered_turn_1=4,
        max_total_prompt_tokens_run=18_000,
        max_total_tokens_run=22_000,
        max_llm_turns=3,
    ),
    "outlook_export": TaskBudgetTargets(
        task_class="outlook_export",
        description="Outlook/email content written to a file.",
        max_prompt_tokens_turn_1=10_000,
        max_tools_json_chars_turn_1=6_000,
        max_system_prompt_chars_turn_1=2_200,
        max_tools_offered_turn_1=6,
        max_total_prompt_tokens_run=28_000,
        max_total_tokens_run=35_000,
        max_llm_turns=4,
    ),
    "driver_install": TaskBudgetTargets(
        task_class="driver_install",
        description="Printer/driver install or network device setup.",
        max_prompt_tokens_turn_1=12_000,
        max_tools_json_chars_turn_1=8_000,
        max_tools_offered_turn_1=10,
        max_total_prompt_tokens_run=35_000,
        max_total_tokens_run=45_000,
        max_llm_turns=5,
    ),
    "simple_desktop": TaskBudgetTargets(
        task_class="simple_desktop",
        description="Single-step desktop or shell task.",
        max_prompt_tokens_turn_1=10_000,
        max_tools_json_chars_turn_1=7_000,
        max_tools_offered_turn_1=10,
        max_total_prompt_tokens_run=25_000,
        max_total_tokens_run=32_000,
        max_llm_turns=4,
    ),
    "complex_automation": TaskBudgetTargets(
        task_class="complex_automation",
        description="Multi-step or high-complexity automation.",
        max_prompt_tokens_turn_1=16_000,
        max_tools_json_chars_turn_1=12_000,
        max_tools_offered_turn_1=14,
        max_total_prompt_tokens_run=55_000,
        max_total_tokens_run=70_000,
        max_llm_turns=8,
    ),
}


def classify_task_class(
    user_text: str,
    *,
    complexity_level: Optional[str] = None,
) -> str:
    """Map user request to a budget profile (no per-word hardcoding beyond class patterns)."""
    raw = (user_text or "").strip()
    lowered = raw.lower()

    if not raw:
        return "conversation"
    if _GREETING_RE.match(raw):
        return "conversation"
    if complexity_level in {"multi_step"}:
        return "complex_automation"
    if complexity_level in {"complex"}:
        return "complex_automation"
    if _MULTI_STEP_RE.search(raw):
        return "complex_automation"

    outlook = bool(_OUTLOOK_RE.search(lowered))
    file_out = bool(_FILE_DELIVERABLE_RE.search(lowered))
    if outlook and file_out:
        return "outlook_export"
    if outlook:
        return "outlook_read"
    if _DRIVER_RE.search(lowered):
        return "driver_install"

    if complexity_level in {"trivial"}:
        return "conversation"
    if complexity_level in {"simple"}:
        return "simple_desktop"
    return "simple_desktop"


def get_task_budget_targets(task_class: str) -> TaskBudgetTargets:
    return DEFAULT_TASK_BUDGETS.get(task_class) or DEFAULT_TASK_BUDGETS["simple_desktop"]


def resolve_task_budget_profile(
    user_text: str,
    *,
    complexity: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    level = (complexity or {}).get("level")
    task_class = classify_task_class(user_text, complexity_level=level)
    targets = get_task_budget_targets(task_class)
    return {
        "task_class": task_class,
        "targets": targets.to_dict(),
    }


def evaluate_step_against_targets(
    step_metrics: Dict[str, Any],
    targets: TaskBudgetTargets,
) -> List[Dict[str, Any]]:
    """Return list of soft limit violations for one agent step."""
    violations: List[Dict[str, Any]] = []
    step = int(step_metrics.get("step") or 0)
    if step != 1:
        return violations

    checks = (
        ("prompt_tokens", int(step_metrics.get("prompt_tokens") or 0), targets.max_prompt_tokens_turn_1),
        ("completion_tokens", int(step_metrics.get("completion_tokens") or 0), targets.max_completion_tokens_turn_1),
        ("tools_json_chars", int(step_metrics.get("tools_json_chars") or 0), targets.max_tools_json_chars_turn_1),
        ("system_prompt_chars", int(step_metrics.get("system_prompt_chars") or 0), targets.max_system_prompt_chars_turn_1),
        ("tools_offered", int(step_metrics.get("tools_offered") or step_metrics.get("tools_count") or 0), targets.max_tools_offered_turn_1),
    )
    for metric, actual, limit in checks:
        if limit <= 0:
            continue
        if actual > limit:
            violations.append(
                {
                    "metric": metric,
                    "actual": actual,
                    "limit": limit,
                    "step": step,
                    "overage": actual - limit,
                    "overage_pct": round(100.0 * (actual - limit) / limit, 1),
                }
            )
    return violations


def evaluate_run_against_targets(
    cost_summary: Dict[str, Any],
    targets: TaskBudgetTargets,
) -> List[Dict[str, Any]]:
    violations: List[Dict[str, Any]] = []
    checks = (
        ("prompt_tokens", int(cost_summary.get("prompt_tokens") or 0), targets.max_total_prompt_tokens_run),
        ("total_tokens", int(cost_summary.get("total_tokens") or 0), targets.max_total_tokens_run),
        ("llm_turns", int(cost_summary.get("llm_turns") or 0), targets.max_llm_turns),
    )
    for metric, actual, limit in checks:
        if actual > limit:
            violations.append(
                {
                    "metric": metric,
                    "actual": actual,
                    "limit": limit,
                    "scope": "run",
                    "overage": actual - limit,
                    "overage_pct": round(100.0 * (actual - limit) / limit, 1),
                }
            )
    return violations


def build_budget_compliance_report(
    *,
    task_class: str,
    targets: TaskBudgetTargets,
    step_violations: List[Dict[str, Any]],
    run_violations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    all_violations = step_violations + run_violations
    return {
        "task_class": task_class,
        "targets": targets.to_dict(),
        "within_targets": not all_violations,
        "violation_count": len(all_violations),
        "violations": all_violations,
    }
