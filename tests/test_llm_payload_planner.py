# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from canonical_tools import agentic_tool_definitions
from llm_payload_planner import (
    DisclosureLevel,
    _TOOL_AFFINITY,
    can_expand_disclosure,
    disclosure_tool_bounds,
    max_tools_for_disclosure,
    next_disclosure_expansion,
    plan_llm_payload,
    resolve_disclosure_level,
    standard_disclosure_max,
)
from model_router import ComplexityClassification, ComplexityLevel


def _catalog():
    return agentic_tool_definitions()


def test_plan_returns_tool_payload_plan():
    plan = plan_llm_payload(
        _catalog(),
        "listar emails nao lidos do outlook",
        ComplexityLevel.SIMPLE,
        "anthropic",
    )
    assert plan.catalog_size >= 20
    assert len(plan.tools) <= plan.max_tools
    assert "office" in plan.tool_names
    assert plan.disclosure_level == DisclosureLevel.FOCUSED
    assert plan.intent_accepted is True
    assert plan.intent_profile_id == "outlook_read_unread"


def test_trivial_uses_minimal_disclosure():
    plan = plan_llm_payload(_catalog(), "ola", ComplexityLevel.TRIVIAL, "openai")
    assert plan.disclosure_level == DisclosureLevel.MINIMAL
    assert len(plan.tools) <= 3


def test_expansion_step_widens_disclosure():
    complexity = ComplexityClassification(level=ComplexityLevel.SIMPLE, score=0.0)
    assert resolve_disclosure_level(complexity, expansion_step=0) == DisclosureLevel.FOCUSED
    assert resolve_disclosure_level(complexity, expansion_step=1) == DisclosureLevel.STANDARD
    assert resolve_disclosure_level(complexity, expansion_step=2) == DisclosureLevel.FULL


def test_provider_cap_from_config_not_branch():
    assert max_tools_for_disclosure(DisclosureLevel.STANDARD, provider="groq") == 14
    assert max_tools_for_disclosure(DisclosureLevel.STANDARD, provider="anthropic") == 14


def test_large_catalog_never_full_on_turn_zero():
    catalog = _catalog()
    if len(catalog) < 20:
        return
    plan = plan_llm_payload(
        catalog,
        "instalar driver da impressora",
        ComplexityLevel.SIMPLE,
        "anthropic",
        expansion_step=0,
    )
    assert len(plan.tools) < len(catalog)
    assert plan.disclosure_level != DisclosureLevel.FULL


def test_force_full_sends_catalog():
    catalog = _catalog()
    plan = plan_llm_payload(
        catalog,
        "qualquer coisa",
        ComplexityLevel.SIMPLE,
        "groq",
        force_full=True,
    )
    assert len(plan.tools) == len(catalog)
    assert plan.disclosure_level == DisclosureLevel.FULL


def test_to_dict_includes_metrics():
    plan = plan_llm_payload(_catalog(), "listar processos", ComplexityLevel.SIMPLE, "openai")
    data = plan.to_dict()
    assert data["tools_offered"] == len(plan.tools)
    assert data["tools_json_chars"] > 0
    assert data["disclosure_level"] == "focused"


def test_disclosure_bounds_2_3():
    assert disclosure_tool_bounds(DisclosureLevel.MINIMAL) == (1, 3)
    assert disclosure_tool_bounds(DisclosureLevel.FOCUSED) == (4, 8)
    assert disclosure_tool_bounds(DisclosureLevel.STANDARD)[1] == standard_disclosure_max()


def test_turn_one_focused_for_complex_request():
    complexity = ComplexityClassification(level=ComplexityLevel.COMPLEX, score=1.0)
    assert resolve_disclosure_level(complexity, expansion_step=0) == DisclosureLevel.FOCUSED


def test_next_disclosure_expansion_sequence():
    step, level, expanded = next_disclosure_expansion(0, reason="test")
    assert expanded and step == 1 and level == DisclosureLevel.STANDARD
    step, level, expanded = next_disclosure_expansion(1, reason="test")
    assert expanded and step == 2 and level == DisclosureLevel.FULL
    step, level, expanded = next_disclosure_expansion(2, reason="test")
    assert not expanded and step == 2


def test_cannot_expand_past_max():
    assert not can_expand_disclosure(2)


def test_affinity_lives_in_planner_only():
    assert "office" in _TOOL_AFFINITY
    assert "filesystem" in _TOOL_AFFINITY["office"]


def test_standard_cap_override(monkeypatch):
    monkeypatch.setenv("AGENTE_DISCLOSURE_STANDARD_MAX", "10")
    assert standard_disclosure_max() == 10
    assert disclosure_tool_bounds(DisclosureLevel.STANDARD) == (4, 10)
