# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Objective-based skill discovery — rank installed skills before creating new scripts."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from skill_manifest import SkillManifest

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)

_STOPWORDS = frozenset({
    "a", "an", "the", "to", "for", "of", "in", "on", "at", "by", "with",
    "and", "or", "is", "are", "me", "my", "do", "run", "use", "using",
    "please", "can", "you", "i", "want", "need", "help",
})


@dataclass
class SkillMatch:
    name: str
    version: str
    score: float
    display_name: str = ""
    description: str = ""
    tags: List[str] = field(default_factory=list)
    risk_level: str = "low"
    requires_approval: bool = False
    trust_status: str = "trusted"
    match_reasons: List[str] = field(default_factory=list)
    input_params: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "score": round(self.score, 4),
            "display_name": self.display_name,
            "description": self.description,
            "tags": self.tags,
            "risk_level": self.risk_level,
            "requires_approval": self.requires_approval,
            "trust_status": self.trust_status,
            "match_reasons": self.match_reasons,
            "input_params": self.input_params,
            "recommended": self.score >= 0.45,
        }


def _tokenize(text: str) -> Set[str]:
    tokens = {t.lower() for t in _TOKEN_RE.findall(text) if len(t) >= 2}
    return tokens - _STOPWORDS


def _overlap_score(query_tokens: Set[str], field_tokens: Set[str], weight: float) -> tuple[float, Optional[str]]:
    if not query_tokens or not field_tokens:
        return 0.0, None
    overlap = query_tokens & field_tokens
    if not overlap:
        return 0.0, None
    ratio = len(overlap) / max(len(query_tokens), 1)
    return weight * ratio, ", ".join(sorted(overlap)[:5])


def score_skill_for_objective(
    manifest: SkillManifest,
    objective: str,
    *,
    index_entry: Optional[Dict[str, Any]] = None,
) -> SkillMatch:
    """Score how well a skill matches a natural-language objective (0.0–1.0)."""
    query_tokens = _tokenize(objective)
    if not query_tokens:
        return SkillMatch(
            name=manifest.name,
            version=manifest.version,
            score=0.0,
            display_name=manifest.display_name,
            description=manifest.description,
            tags=manifest.tags,
            risk_level=manifest.effective_risk_level.value,
            requires_approval=manifest.requires_approval,
            trust_status=(index_entry or {}).get("trust_status", "trusted"),
        )

    score = 0.0
    reasons: List[str] = []

    fields = [
        (manifest.name.replace(".", " ").replace("_", " "), 0.35, "name"),
        (manifest.display_name, 0.20, "display_name"),
        (manifest.description, 0.30, "description"),
        (" ".join(manifest.tags), 0.25, "tags"),
        (manifest.inputs.description, 0.10, "inputs"),
        (" ".join(p.name for p in manifest.inputs.params), 0.10, "parameters"),
    ]

    objective_lower = objective.lower()
    if manifest.name.lower() in objective_lower or objective_lower in manifest.name.lower():
        score += 0.25
        reasons.append("exact name match")

    for text, weight, label in fields:
        if not text:
            continue
        partial, matched = _overlap_score(query_tokens, _tokenize(text), weight)
        if partial > 0:
            score += partial
            reasons.append(f"{label}: {matched}")

    for tag in manifest.tags:
        if tag.lower() in objective_lower:
            score += 0.15
            reasons.append(f"tag '{tag}'")

    score = min(score, 1.0)

    return SkillMatch(
        name=manifest.name,
        version=manifest.version,
        score=score,
        display_name=manifest.display_name,
        description=manifest.description,
        tags=manifest.tags,
        risk_level=manifest.effective_risk_level.value,
        requires_approval=manifest.requires_approval,
        trust_status=(index_entry or {}).get("trust_status", "trusted"),
        match_reasons=reasons,
        input_params=[p.name for p in manifest.inputs.params],
    )


def discover_skills_for_objective(
    manifests: List[tuple[SkillManifest, Optional[Dict[str, Any]]]],
    objective: str,
    *,
    limit: int = 5,
    min_score: float = 0.12,
) -> List[SkillMatch]:
    """Rank skills by relevance to an objective."""
    scored = [
        score_skill_for_objective(m, objective, index_entry=entry)
        for m, entry in manifests
    ]
    scored = [m for m in scored if m.score >= min_score]
    scored.sort(key=lambda m: (-m.score, m.name))
    return scored[:limit]
