# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Profile-driven intent classification (Fase 1.2).

Maps user text to a declarative :class:`IntentProfile` by scoring ``signals`` in each
profile JSON — no per-scenario Python branches and no greeting blocklists. Outlook,
filesystem, drivers, shell, etc. are equal entries in ``core/intent_profiles/``.

Signal keys (all optional lists of substrings, accent-insensitive):

- ``require_any`` — at least one must appear when non-empty.
- ``require_all`` — every pattern must appear when non-empty.
- ``also_require_any`` — secondary gate; at least one must appear (alias: ``require_all_any``).
- ``prefer_any`` — increases score per hit (disambiguation).
- ``exclude_any`` — any hit disqualifies the profile.
- ``min_chars`` — minimum normalized text length to consider this profile.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from intent_profile_registry import IntentProfile, IntentProfileRegistry

_BASE_SCORE = 0.52
_PREFER_WEIGHT = 0.09
_PREFER_MAX = 0.36
_EXTRA_REQUIRE_ANY_WEIGHT = 0.04
_ALSO_REQUIRE_BONUS = 0.10
_TIE_MARGIN = 0.03
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ProfileScore:
    profile_id: str
    confidence: float
    matched_require_any: int = 0
    matched_prefer: int = 0
    disqualified: bool = False
    disqualify_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "confidence": round(self.confidence, 4),
            "matched_require_any": self.matched_require_any,
            "matched_prefer": self.matched_prefer,
            "disqualified": self.disqualified,
            "disqualify_reason": self.disqualify_reason,
        }


@dataclass(frozen=True)
class IntentClassification:
    """Result of classifying user text against all registered profiles."""

    profile_id: Optional[str]
    profile: Optional[IntentProfile]
    confidence: float
    accepted: bool
    threshold: float
    reason: str
    ranked: Tuple[ProfileScore, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "domain": self.profile.domain if self.profile else None,
            "confidence": round(self.confidence, 4),
            "accepted": self.accepted,
            "threshold": self.threshold,
            "reason": self.reason,
            "required_tools": list(self.profile.required_tools) if self.profile else [],
            "ranked": [item.to_dict() for item in self.ranked[:8]],
        }


def normalize_user_text(text: str) -> str:
    raw = (text or "").strip().lower()
    if not raw:
        return ""
    decomposed = unicodedata.normalize("NFKD", raw)
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _WS_RE.sub(" ", folded)


def _signal_list(signals: Dict[str, Any], key: str) -> List[str]:
    alt_keys = (key,)
    if key == "also_require_any":
        alt_keys = ("also_require_any", "require_all_any")
    for alt in alt_keys:
        raw = signals.get(alt)
        if isinstance(raw, list):
            return [normalize_user_text(str(item)) for item in raw if str(item).strip()]
    return []


def _count_matches(text: str, patterns: Sequence[str]) -> int:
    if not text or not patterns:
        return 0
    return sum(1 for pattern in patterns if pattern and pattern in text)


def _any_match(text: str, patterns: Sequence[str]) -> bool:
    return _count_matches(text, patterns) > 0


def score_profile(text: str, profile: IntentProfile) -> ProfileScore:
    normalized = normalize_user_text(text)
    signals = profile.signals or {}

    min_chars = int(signals.get("min_chars") or 0)
    if min_chars > 0 and len(normalized) < min_chars:
        return ProfileScore(profile.id, 0.0, disqualified=True, disqualify_reason="min_chars")

    exclude = _signal_list(signals, "exclude_any")
    if exclude and _any_match(normalized, exclude):
        return ProfileScore(profile.id, 0.0, disqualified=True, disqualify_reason="exclude_any")

    require_any = _signal_list(signals, "require_any")
    require_all = _signal_list(signals, "require_all")
    also_require = _signal_list(signals, "also_require_any")
    prefer = _signal_list(signals, "prefer_any")

    if require_any and not _any_match(normalized, require_any):
        return ProfileScore(profile.id, 0.0, disqualified=True, disqualify_reason="require_any")
    if require_all and _count_matches(normalized, require_all) < len(require_all):
        return ProfileScore(profile.id, 0.0, disqualified=True, disqualify_reason="require_all")
    if also_require and not _any_match(normalized, also_require):
        return ProfileScore(profile.id, 0.0, disqualified=True, disqualify_reason="also_require_any")

    req_hits = _count_matches(normalized, require_any) if require_any else 0
    pref_hits = _count_matches(normalized, prefer)

    confidence = _BASE_SCORE
    if req_hits > 1:
        confidence += min(0.12, (req_hits - 1) * _EXTRA_REQUIRE_ANY_WEIGHT)
    if also_require and _any_match(normalized, also_require):
        confidence += _ALSO_REQUIRE_BONUS
    confidence += min(_PREFER_MAX, pref_hits * _PREFER_WEIGHT)
    confidence = min(1.0, confidence)

    return ProfileScore(
        profile.id,
        confidence,
        matched_require_any=req_hits,
        matched_prefer=pref_hits,
    )


def rank_profiles(
    text: str,
    registry: IntentProfileRegistry,
) -> List[Tuple[IntentProfile, ProfileScore]]:
    scored: List[Tuple[IntentProfile, ProfileScore]] = []
    for profile in registry.list_profiles():
        item = score_profile(text, profile)
        if not item.disqualified and item.confidence > 0:
            scored.append((profile, item))
    scored.sort(
        key=lambda pair: (
            pair[1].confidence,
            pair[1].matched_prefer,
            pair[1].matched_require_any,
        ),
        reverse=True,
    )
    return scored


def _pick_winner(
    ranked: List[Tuple[IntentProfile, ProfileScore]],
) -> Optional[Tuple[IntentProfile, ProfileScore]]:
    if not ranked:
        return None
    best_profile, best_score = ranked[0]
    if len(ranked) == 1:
        return best_profile, best_score
    runner_profile, runner_score = ranked[1]
    if best_score.confidence - runner_score.confidence <= _TIE_MARGIN:
        if runner_score.matched_prefer > best_score.matched_prefer:
            return runner_profile, runner_score
        if (
            runner_score.matched_prefer == best_score.matched_prefer
            and runner_score.matched_require_any > best_score.matched_require_any
        ):
            return runner_profile, runner_score
    return best_profile, best_score


class IntentClassifier:
    """Scores every profile in the registry; accepts only if confidence ≥ profile threshold."""

    def __init__(self, registry: Optional[IntentProfileRegistry] = None):
        self._registry = registry or IntentProfileRegistry.default()

    @property
    def registry(self) -> IntentProfileRegistry:
        return self._registry

    def classify(self, user_text: str) -> IntentClassification:
        ranked_pairs = rank_profiles(user_text, self._registry)
        ranked_scores = tuple(score for _, score in ranked_pairs)

        if not ranked_pairs:
            return IntentClassification(
                profile_id=None,
                profile=None,
                confidence=0.0,
                accepted=False,
                threshold=0.0,
                reason="no_profile_signals_matched",
                ranked=ranked_scores,
            )

        winner_profile, winner_score = _pick_winner(ranked_pairs) or ranked_pairs[0]
        threshold = winner_profile.confidence_threshold
        accepted = winner_score.confidence >= threshold

        if not accepted:
            return IntentClassification(
                profile_id=None,
                profile=None,
                confidence=winner_score.confidence,
                accepted=False,
                threshold=threshold,
                reason="below_confidence_threshold",
                ranked=ranked_scores,
            )

        return IntentClassification(
            profile_id=winner_profile.id,
            profile=winner_profile,
            confidence=winner_score.confidence,
            accepted=True,
            threshold=threshold,
            reason="matched",
            ranked=ranked_scores,
        )


def classify_user_intent(
    user_text: str,
    *,
    registry: Optional[IntentProfileRegistry] = None,
) -> IntentClassification:
    return IntentClassifier(registry).classify(user_text)


def build_resolved_tool_arguments(profile: IntentProfile) -> Dict[str, Any]:
    """Merge param_defaults with default_action.params for fast-path execution (Fase 1.3)."""
    merged = {**profile.param_defaults, **profile.default_action.params}
    tool = profile.default_action.tool
    action = profile.default_action.action
    if tool == "shell":
        return {
            "command": merged.get("command", ""),
            "shell": merged.get("shell", "powershell"),
            **{k: v for k, v in merged.items() if k in {"timeout", "retries", "require_admin"}},
        }
    if tool == "filesystem":
        return {"action": action, **{k: v for k, v in merged.items() if k in {
            "path", "content", "dest", "pattern", "template", "request_id",
        }}}
    if tool == "office":
        return {"action": action, **{k: v for k, v in merged.items() if k in {
            "limit", "folder", "unread_only", "path", "query", "output_path",
        }}}
    if tool == "driver_manager":
        return {"action": action, **{k: v for k, v in merged.items() if k in {
            "query", "device_id", "printer_name", "path",
        }}}
    return {"action": action, **merged}
