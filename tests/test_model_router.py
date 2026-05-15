# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tests for model routing by request complexity."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from model_router import (
    ComplexityLevel,
    apply_model_escalation,
    classify_request_complexity,
    escalate_model,
    fallback_escalation_enabled,
    is_fast_tier_model,
    route_model_for_request,
    routing_enabled,
    should_escalate_after_failure,
)


class TestComplexityClassifier:
    def test_trivial_greeting(self):
        c = classify_request_complexity("hello")
        assert c.level == ComplexityLevel.TRIVIAL

    def test_simple_short_request(self):
        c = classify_request_complexity("list files in my downloads folder")
        assert c.level in (ComplexityLevel.SIMPLE, ComplexityLevel.TRIVIAL)

    def test_complex_install_driver(self):
        c = classify_request_complexity(
            "Install the network printer driver and configure sharing permissions for all users"
        )
        assert c.level in (ComplexityLevel.COMPLEX, ComplexityLevel.MULTI_STEP)

    def test_multi_step_numbered(self):
        c = classify_request_complexity(
            "1. Open Excel\n2. Read the sales sheet\n3. Export to PDF\n4. Email the file"
        )
        assert c.level == ComplexityLevel.MULTI_STEP

    def test_outlook_read_unread_is_complex(self):
        c = classify_request_complexity(
            "voce pode me retornar aqui meus ultimos emails do outlook que nao foram visualizados"
        )
        assert c.level in (ComplexityLevel.COMPLEX, ComplexityLevel.MULTI_STEP)
        assert "outlook_integration" in c.signals

    def test_outlook_export_file_is_complex(self):
        c = classify_request_complexity(
            "gostaria de um arquivo contendo meus ultimos 3 emails do outlook, coloque na pasta de downloads"
        )
        assert c.level in (ComplexityLevel.COMPLEX, ComplexityLevel.MULTI_STEP)
        assert "integration_deliverable" in c.signals


class TestModelPool:
    def test_routes_trivial_to_fast_anthropic(self):
        d = route_model_for_request(
            "anthropic",
            user_text="hi",
            requested_model=None,
            available_models=[
                "claude-haiku-4-5-20251001",
                "claude-sonnet-4-6",
            ],
            force_routing=True,
        )
        assert d.routing_applied
        assert "haiku" in d.selected_model.lower()

    def test_routes_complex_to_full_openai(self):
        d = route_model_for_request(
            "openai",
            user_text="Install drivers and troubleshoot network printer deployment across the office",
            requested_model=None,
            available_models=["gpt-4o-mini", "gpt-4.1"],
            force_routing=True,
        )
        assert d.routing_applied
        assert d.selected_model == "gpt-4.1"

    def test_respects_user_locked_sonnet(self):
        d = route_model_for_request(
            "anthropic",
            user_text="hello",
            requested_model="claude-sonnet-4-6",
            force_routing=True,
        )
        assert not d.routing_applied
        assert d.selected_model == "claude-sonnet-4-6"

    def test_routing_disabled_env(self, monkeypatch):
        monkeypatch.setenv("AGENTE_MODEL_ROUTING", "0")
        assert not routing_enabled()
        d = route_model_for_request(
            "anthropic",
            user_text="hello",
            requested_model="claude-sonnet-4-6",
        )
        assert not d.routing_applied


class TestModelFallback:
    def test_is_fast_tier(self):
        assert is_fast_tier_model("gpt-4o-mini")
        assert not is_fast_tier_model("gpt-4.1")

    def test_escalate_openai_fast_to_full(self):
        target = escalate_model(
            "openai",
            "gpt-4o-mini",
            available_models=["gpt-4o-mini", "gpt-4.1"],
        )
        assert target == "gpt-4.1"

    def test_should_escalate_fast_tier(self):
        cfg = {"model": "gpt-4o-mini", "model_routing": {"tier": "fast"}}
        assert should_escalate_after_failure(cfg)

    def test_no_double_escalation(self):
        cfg = {
            "model": "gpt-4o-mini",
            "model_routing": {"tier": "fast"},
            "escalation_used": True,
        }
        assert not should_escalate_after_failure(cfg)

    def test_apply_escalation_mutates_config(self):
        cfg = {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "model_routing": {"tier": "fast", "selected_model": "gpt-4o-mini"},
            "available_models": ["gpt-4o-mini", "gpt-4.1"],
        }
        meta = apply_model_escalation(cfg)
        assert meta["escalated"]
        assert cfg["model"] == "gpt-4.1"
        assert cfg["escalation_used"]
        assert cfg["model_routing"]["tier"] == "full"

    def test_groq_outlook_read_routes_to_full_70b(self):
        d = route_model_for_request(
            "groq",
            user_text="retornar meus emails do outlook nao visualizados",
            requested_model=None,
            available_models=["llama-3.1-8b-instant", "llama-3.3-70b-versatile"],
            force_routing=True,
        )
        assert d.routing_applied
        assert d.selected_model == "llama-3.3-70b-versatile"

    def test_groq_outlook_file_routes_to_full_70b(self):
        d = route_model_for_request(
            "groq",
            user_text=(
                "gostaria de um arquivo contendo meus ultimos 3 emails do outlook, "
                "coloque na pasta de downloads"
            ),
            requested_model=None,
            available_models=["llama-3.1-8b-instant", "llama-3.3-70b-versatile"],
            force_routing=True,
        )
        assert d.routing_applied
        assert d.selected_model == "llama-3.3-70b-versatile"
        assert d.tier == "full"

    def test_groq_trivial_routes_to_fast_8b_by_default(self):
        d = route_model_for_request(
            "groq",
            user_text="ola",
            requested_model=None,
            available_models=["llama-3.1-8b-instant", "llama-3.3-70b-versatile"],
            force_routing=True,
        )
        assert d.routing_applied
        assert d.selected_model == "llama-3.1-8b-instant"

    def test_groq_tpm_on_fast_tier_still_escalates(self):
        cfg = {
            "provider": "groq",
            "model": "llama-3.1-8b-instant",
            "model_routing": {"tier": "fast"},
        }
        assert should_escalate_after_failure(
            cfg,
            status_code=413,
            response_body='{"error":{"message":"tokens per minute"}}',
        )

    def test_groq_tpm_on_full_tier_does_not_escalate_again(self):
        cfg = {
            "provider": "groq",
            "model": "llama-3.3-70b-versatile",
            "model_routing": {"tier": "full"},
            "escalation_used": False,
        }
        assert not should_escalate_after_failure(
            cfg,
            status_code=429,
            response_body='{"error":{"message":"tokens per minute"}}',
        )

    def test_fallback_disabled_env(self, monkeypatch):
        monkeypatch.setenv("AGENTE_MODEL_FALLBACK", "0")
        assert not fallback_escalation_enabled()
        cfg = {"model": "gpt-4o-mini", "model_routing": {"tier": "fast"}}
        assert not should_escalate_after_failure(cfg)
