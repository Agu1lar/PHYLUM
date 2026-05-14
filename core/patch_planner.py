# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Patch planner — decompose large changes by files/owners with risk and application order.

Given a set of intended file changes, the planner:

1. **Classifies risk** per file (based on language, change scope, config vs code,
   ownership, test coverage, and dependency fan-out).
2. **Resolves owners** using CODEOWNERS or the codebase map.
3. **Orders patches** topologically by dependency so downstream consumers are
   changed after their dependencies.
4. **Groups by owner** for review assignment.
5. **Generates a plan** with risk scores, application order, and review assignments.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class FileChange:
    path: str
    change_type: str  # add, modify, delete, rename
    description: str = ""
    lines_added: int = 0
    lines_removed: int = 0
    language: str = ""
    is_test: bool = False
    is_config: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path, "change_type": self.change_type,
            "description": self.description,
            "lines_added": self.lines_added, "lines_removed": self.lines_removed,
            "language": self.language,
            "is_test": self.is_test, "is_config": self.is_config,
        }


@dataclass
class FileRisk:
    path: str
    risk_level: str  # low, medium, high, critical
    risk_score: float = 0.0  # 0.0 - 1.0
    factors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "risk_level": self.risk_level,
            "risk_score": round(self.risk_score, 4),
            "factors": self.factors,
        }


@dataclass
class PatchStep:
    order: int
    path: str
    change: FileChange
    risk: FileRisk
    owner: str = ""
    dependencies: List[str] = field(default_factory=list)
    dependents: List[str] = field(default_factory=list)
    test_coverage: List[str] = field(default_factory=list)
    review_group: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order": self.order,
            "path": self.path,
            "change": self.change.to_dict(),
            "risk": self.risk.to_dict(),
            "owner": self.owner,
            "dependencies": self.dependencies,
            "dependents": self.dependents,
            "test_coverage": self.test_coverage,
            "review_group": self.review_group,
        }


@dataclass
class PatchPlan:
    plan_id: str
    total_files: int = 0
    total_lines_added: int = 0
    total_lines_removed: int = 0
    overall_risk: str = "low"
    overall_risk_score: float = 0.0
    steps: List[PatchStep] = field(default_factory=list)
    owner_groups: Dict[str, List[str]] = field(default_factory=dict)
    risk_summary: Dict[str, int] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "total_files": self.total_files,
            "total_lines_added": self.total_lines_added,
            "total_lines_removed": self.total_lines_removed,
            "overall_risk": self.overall_risk,
            "overall_risk_score": round(self.overall_risk_score, 4),
            "steps": [s.to_dict() for s in self.steps],
            "owner_groups": self.owner_groups,
            "risk_summary": self.risk_summary,
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# RiskAssessor
# ---------------------------------------------------------------------------

CONFIG_PATTERNS = {
    "pyproject.toml", "setup.cfg", "setup.py", "package.json", "tsconfig.json",
    ".eslintrc", ".prettierrc", "Dockerfile", "docker-compose.yml",
    "docker-compose.yaml", ".env", "requirements.txt", "Pipfile",
    "Cargo.toml", "go.mod", "Makefile", "pytest.ini", "tox.ini",
}

HIGH_RISK_PATHS = {
    "migrations", "alembic", "schema", "security", "auth", "crypto",
    "payment", "billing", "deploy", "infra", "ci", ".github",
}


class RiskAssessor:
    """Scores per-file risk for a change."""

    def assess(
        self,
        change: FileChange,
        *,
        codebase_map=None,
        dependents_count: int = 0,
        has_tests: bool = False,
    ) -> FileRisk:
        factors: List[str] = []
        score = 0.0

        # Change scope
        total_lines = change.lines_added + change.lines_removed
        if total_lines > 200:
            score += 0.20
            factors.append(f"large change ({total_lines} lines)")
        elif total_lines > 50:
            score += 0.10
            factors.append(f"moderate change ({total_lines} lines)")

        # File type
        if change.is_config:
            score += 0.25
            factors.append("config file — affects entire project")
        if change.change_type == "delete":
            score += 0.20
            factors.append("file deletion")
        elif change.change_type == "rename":
            score += 0.15
            factors.append("file rename — may break imports")

        # Path signals
        path_lower = change.path.lower()
        for risky in HIGH_RISK_PATHS:
            if risky in path_lower:
                score += 0.20
                factors.append(f"high-risk path component: {risky}")
                break

        # Dependency fan-out
        if dependents_count > 10:
            score += 0.20
            factors.append(f"high dependency fan-out ({dependents_count} dependents)")
        elif dependents_count > 3:
            score += 0.10
            factors.append(f"moderate fan-out ({dependents_count} dependents)")

        # Test coverage
        if not has_tests and not change.is_test:
            score += 0.10
            factors.append("no test coverage found")

        # Test files are lower risk
        if change.is_test:
            score *= 0.5
            factors.append("test file — lower risk")

        score = min(score, 1.0)
        level = self._score_to_level(score)

        return FileRisk(
            path=change.path,
            risk_level=level,
            risk_score=score,
            factors=factors,
        )

    @staticmethod
    def _score_to_level(score: float) -> str:
        if score >= 0.7:
            return "critical"
        if score >= 0.45:
            return "high"
        if score >= 0.2:
            return "medium"
        return "low"


# ---------------------------------------------------------------------------
# DependencyOrderer
# ---------------------------------------------------------------------------

class DependencyOrderer:
    """Topologically orders file changes so dependencies come before dependents."""

    def order(
        self,
        changes: List[FileChange],
        *,
        codebase_map=None,
    ) -> List[FileChange]:
        if not codebase_map:
            return self._heuristic_order(changes)

        path_set = {c.path for c in changes}
        adjacency: Dict[str, Set[str]] = {c.path: set() for c in changes}
        in_degree: Dict[str, int] = {c.path: 0 for c in changes}

        for change in changes:
            try:
                graph = codebase_map.dependency_graph(change.path)
                for imp in graph.get("imports", []):
                    mod = imp.get("module", "")
                    for other in path_set:
                        if other != change.path and Path(other).stem == mod:
                            adjacency.setdefault(other, set()).add(change.path)
                            in_degree[change.path] = in_degree.get(change.path, 0) + 1
            except Exception:
                pass

        # Kahn's algorithm
        ordered: List[str] = []
        queue = [p for p in in_degree if in_degree[p] == 0]
        queue.sort()

        while queue:
            node = queue.pop(0)
            ordered.append(node)
            for neighbor in sorted(adjacency.get(node, set())):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        for p in path_set:
            if p not in ordered:
                ordered.append(p)

        change_map = {c.path: c for c in changes}
        return [change_map[p] for p in ordered if p in change_map]

    @staticmethod
    def _heuristic_order(changes: List[FileChange]) -> List[FileChange]:
        """Order without a codebase map: configs first, then libs, then tests."""
        def sort_key(c: FileChange) -> tuple:
            if c.is_config:
                return (0, c.path)
            if c.is_test:
                return (2, c.path)
            return (1, c.path)
        return sorted(changes, key=sort_key)


# ---------------------------------------------------------------------------
# OwnerResolver
# ---------------------------------------------------------------------------

class OwnerResolver:
    """Resolves file owners from the codebase map or heuristics."""

    def resolve(
        self,
        path: str,
        *,
        codebase_map=None,
    ) -> str:
        if codebase_map:
            try:
                owners = codebase_map.find_owners()
                for entry in owners:
                    if entry.get("file") == path:
                        return entry.get("owner", "")
            except Exception:
                pass

        parts = Path(path).parts
        if len(parts) > 1:
            return parts[0]
        return "unowned"


# ---------------------------------------------------------------------------
# PatchPlanner — orchestrator
# ---------------------------------------------------------------------------

class PatchPlanner:
    """Decomposes a set of file changes into an ordered, risk-assessed plan."""

    def __init__(self, *, codebase_map=None):
        self.codebase_map = codebase_map
        self._risk_assessor = RiskAssessor()
        self._orderer = DependencyOrderer()
        self._owner_resolver = OwnerResolver()
        self._plan_counter = 0

    def plan(self, changes: List[FileChange]) -> PatchPlan:
        self._plan_counter += 1
        plan_id = f"patch-{self._plan_counter}"

        if not changes:
            return PatchPlan(plan_id=plan_id)

        ordered = self._orderer.order(changes, codebase_map=self.codebase_map)

        steps: List[PatchStep] = []
        risks: Dict[str, int] = {"low": 0, "medium": 0, "high": 0, "critical": 0}
        owner_groups: Dict[str, List[str]] = {}
        warnings: List[str] = []
        max_risk_score = 0.0

        for order_idx, change in enumerate(ordered):
            dependents_count = 0
            deps: List[str] = []
            dep_by: List[str] = []
            test_coverage: List[str] = []

            if self.codebase_map:
                try:
                    graph = self.codebase_map.dependency_graph(change.path)
                    dep_by = [e.get("file", "") for e in graph.get("imported_by", [])]
                    dependents_count = len(dep_by)
                    deps = [
                        i.get("module", "")
                        for i in graph.get("imports", [])
                    ]
                except Exception:
                    pass

                try:
                    stem = Path(change.path).stem
                    tests = self.codebase_map.find_tests_for(stem)
                    test_coverage = [t.get("file", "") for t in tests]
                except Exception:
                    pass

            risk = self._risk_assessor.assess(
                change,
                codebase_map=self.codebase_map,
                dependents_count=dependents_count,
                has_tests=bool(test_coverage),
            )
            risks[risk.risk_level] = risks.get(risk.risk_level, 0) + 1
            max_risk_score = max(max_risk_score, risk.risk_score)

            owner = self._owner_resolver.resolve(
                change.path, codebase_map=self.codebase_map,
            )
            owner_groups.setdefault(owner, []).append(change.path)

            step = PatchStep(
                order=order_idx,
                path=change.path,
                change=change,
                risk=risk,
                owner=owner,
                dependencies=deps[:10],
                dependents=[d for d in dep_by if d][:10],
                test_coverage=[t for t in test_coverage if t][:10],
                review_group=owner,
            )
            steps.append(step)

        # Warnings
        no_tests = [s for s in steps if not s.test_coverage and not s.change.is_test]
        if no_tests:
            warnings.append(
                f"{len(no_tests)} file(s) without test coverage: "
                + ", ".join(s.path for s in no_tests[:5])
            )

        high_risk = [s for s in steps if s.risk.risk_level in ("high", "critical")]
        if high_risk:
            warnings.append(
                f"{len(high_risk)} high-risk change(s): "
                + ", ".join(s.path for s in high_risk[:5])
            )

        deletes = [s for s in steps if s.change.change_type == "delete"]
        if deletes:
            total_deps = sum(len(s.dependents) for s in deletes)
            if total_deps > 0:
                warnings.append(
                    f"Deleting {len(deletes)} file(s) with {total_deps} dependent(s)"
                )

        overall_risk = RiskAssessor._score_to_level(max_risk_score)

        return PatchPlan(
            plan_id=plan_id,
            total_files=len(steps),
            total_lines_added=sum(c.lines_added for c in changes),
            total_lines_removed=sum(c.lines_removed for c in changes),
            overall_risk=overall_risk,
            overall_risk_score=max_risk_score,
            steps=steps,
            owner_groups=owner_groups,
            risk_summary=risks,
            warnings=warnings,
        )

    def plan_from_dicts(self, change_dicts: List[Dict[str, Any]]) -> PatchPlan:
        """Convenience: build FileChanges from dicts and plan."""
        changes = []
        for d in change_dicts:
            path = d.get("path", "")
            name = Path(path).name.lower()
            is_test = name.startswith("test_") or name.endswith("_test.py") or "test" in Path(path).parts
            is_config = name in CONFIG_PATTERNS or any(
                name.startswith(p.rstrip("*")) for p in CONFIG_PATTERNS if "*" in p
            )
            changes.append(FileChange(
                path=path,
                change_type=d.get("change_type", "modify"),
                description=d.get("description", ""),
                lines_added=d.get("lines_added", 0),
                lines_removed=d.get("lines_removed", 0),
                language=d.get("language", ""),
                is_test=d.get("is_test", is_test),
                is_config=d.get("is_config", is_config),
            ))
        return self.plan(changes)
