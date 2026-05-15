# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from task_budget_targets import (
    classify_task_class,
    evaluate_run_against_targets,
    evaluate_step_against_targets,
    get_task_budget_targets,
    resolve_task_budget_profile,
)


def test_classify_outlook_read():
    assert classify_task_class("meus emails do outlook nao lidos") == "outlook_read"


def test_classify_outlook_export():
    assert classify_task_class("emails do outlook para arquivo na pasta downloads") == "outlook_export"


def test_classify_conversation():
    assert classify_task_class("ola") == "conversation"


def test_outlook_read_turn1_limit_8k():
    targets = get_task_budget_targets("outlook_read")
    assert targets.max_prompt_tokens_turn_1 == 8000
    violations = evaluate_step_against_targets(
        {
            "step": 1,
            "prompt_tokens": 9000,
            "tools_json_chars": 3000,
            "tools_offered": 3,
        },
        targets,
    )
    assert len(violations) == 1
    assert violations[0]["metric"] == "prompt_tokens"


def test_resolve_profile_includes_targets():
    profile = resolve_task_budget_profile("listar emails outlook")
    assert profile["task_class"] == "outlook_read"
    assert profile["targets"]["max_prompt_tokens_turn_1"] == 8000


def test_run_level_violation():
    targets = get_task_budget_targets("conversation")
    violations = evaluate_run_against_targets(
        {"prompt_tokens": 5000, "total_tokens": 6000, "llm_turns": 3},
        targets,
    )
    assert any(v["metric"] == "llm_turns" for v in violations)
