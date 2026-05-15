# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Architectural invariants — documented rules enforced by CI and local checks.

Run locally::

    python core/architecture_invariants.py

Exit code 0 = all invariants satisfied, 1 = one or more violations.

Invariant catalogue (also tested in ``tests/test_architecture_invariants.py``):

INV-01  RuntimeManager layers implement layer Protocols (structural typing).
INV-02  ``runtime_layers.py`` must not import orchestration (runtime_manager, app_main).
INV-03  ``layer_contracts.py`` must not import runtime implementations.
INV-04  Layer dependency matrix is acyclic (State has no outbound layer deps).
INV-05  ``tools/`` must not import ``runtime_manager`` (tools are leaf executors).
INV-06  ``nodes/`` must not import ``runtime_manager`` (nodes are leaf handlers).
INV-07  ``action_executor`` must not import cognitive/planner modules directly.
INV-08  RuntimeManager annotates layers with Protocol types (typed boundaries).
INV-09  Core layer modules exist and expose required public methods.
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, FrozenSet, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Invariant registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Invariant:
    id: str
    name: str
    description: str


ARCHITECTURAL_INVARIANTS: Sequence[Invariant] = (
    Invariant("INV-01", "layer_protocol_conformance", "RuntimeManager layers satisfy layer Protocols"),
    Invariant("INV-02", "runtime_layers_isolation", "runtime_layers.py does not import orchestration modules"),
    Invariant("INV-03", "contracts_purity", "layer_contracts.py imports only typing/stdlib + documents boundaries"),
    Invariant("INV-04", "acyclic_layer_deps", "Layer dependency matrix has no upward edges"),
    Invariant("INV-05", "tools_leaf_boundary", "tools/ must not import runtime_manager"),
    Invariant("INV-06", "nodes_leaf_boundary", "nodes/ must not import runtime_manager"),
    Invariant("INV-07", "executor_boundary", "action_executor does not bypass layers for cognitive imports"),
    Invariant("INV-08", "typed_layer_fields", "RuntimeManager declares Protocol-typed layer attributes"),
    Invariant("INV-09", "layer_surface_methods", "Concrete layer dataclasses expose contract methods"),
)


@dataclass
class InvariantResult:
    invariant: Invariant
    passed: bool
    detail: str = ""

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        suffix = f" — {self.detail}" if self.detail else ""
        return f"[{status}] {self.invariant.id} {self.invariant.name}{suffix}"


@dataclass
class InvariantReport:
    results: List[InvariantResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> List[InvariantResult]:
        return [r for r in self.results if not r.passed]


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def module_top_level_imports(path: Path) -> FrozenSet[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return frozenset(found)


def module_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def class_has_methods(path: Path, class_name: str, methods: Sequence[str]) -> List[str]:
    tree = ast.parse(module_source(path))
    missing: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            defined = {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            for method in methods:
                if method not in defined:
                    missing.append(method)
            return missing
    return list(methods)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_inv01_layer_protocols(root: Path) -> InvariantResult:
    inv = ARCHITECTURAL_INVARIANTS[0]
    sys.path.insert(0, str(root / "core"))
    sys.path.insert(0, str(root / "tools"))
    sys.path.insert(0, str(root / "agents"))
    sys.path.insert(0, str(root / "nodes"))
    sys.path.insert(0, str(root / "models"))
    sys.path.insert(0, str(root / "providers"))
    sys.path.insert(0, str(root / "safety"))
    sys.path.insert(0, str(root / "memory"))
    sys.path.insert(0, str(root / "execution"))
    sys.path.insert(0, str(root / "persistence"))
    try:
        from layer_contracts import (
            CognitiveLayerProtocol,
            ExecutionLayerProtocol,
            OperationalLayerProtocol,
            StateLayerProtocol,
        )
        from runtime_manager import RuntimeManager

        manager = RuntimeManager(lambda _e: None)
        checks = [
            ("cognitive_layer", CognitiveLayerProtocol, manager.cognitive_layer),
            ("operational_layer", OperationalLayerProtocol, manager.operational_layer),
            ("execution_layer", ExecutionLayerProtocol, manager.execution_layer),
            ("state_layer", StateLayerProtocol, manager.state_layer),
        ]
        failures = [name for name, proto, obj in checks if not isinstance(obj, proto)]
        if failures:
            return InvariantResult(inv, False, f"not a Protocol: {', '.join(failures)}")
        return InvariantResult(inv, True)
    except Exception as exc:
        return InvariantResult(inv, False, str(exc))


def _check_forbidden_imports(path: Path, forbidden: FrozenSet[str]) -> Optional[str]:
    imports = module_top_level_imports(path)
    hits = forbidden & imports
    if hits:
        return f"{path.name} imports forbidden: {sorted(hits)}"
    return None


def _check_inv02_runtime_layers(root: Path) -> InvariantResult:
    inv = ARCHITECTURAL_INVARIANTS[1]
    path = root / "core" / "runtime_layers.py"
    detail = _check_forbidden_imports(path, frozenset({"runtime_manager", "app_main"}))
    return InvariantResult(inv, detail is None, detail or "")


def _check_inv03_contracts_purity(root: Path) -> InvariantResult:
    inv = ARCHITECTURAL_INVARIANTS[2]
    path = root / "core" / "layer_contracts.py"
    forbidden = frozenset({
        "runtime_manager", "runtime_layers", "action_executor",
        "agentic_loop", "tool_registry", "planner_agent",
    })
    detail = _check_forbidden_imports(path, forbidden)
    return InvariantResult(inv, detail is None, detail or "")


def _check_inv04_acyclic_deps(root: Path) -> InvariantResult:
    inv = ARCHITECTURAL_INVARIANTS[3]
    sys.path.insert(0, str(root / "core"))
    try:
        from layer_contracts import ALLOWED_LAYER_DEPENDENCIES

        if ALLOWED_LAYER_DEPENDENCIES.get("state"):
            return InvariantResult(inv, False, "state layer must not depend on other layers")
        for layer, targets in ALLOWED_LAYER_DEPENDENCIES.items():
            if layer in targets:
                return InvariantResult(inv, False, f"{layer} cannot depend on itself")
        return InvariantResult(inv, True)
    except Exception as exc:
        return InvariantResult(inv, False, str(exc))


def _check_glob_forbidden(root: Path, pattern: str, forbidden: FrozenSet[str]) -> List[str]:
    violations: List[str] = []
    for path in sorted(root.glob(pattern)):
        if path.name.startswith("__"):
            continue
        detail = _check_forbidden_imports(path, forbidden)
        if detail:
            violations.append(f"{path.relative_to(root)}: {detail}")
    return violations


def _check_inv05_tools(root: Path) -> InvariantResult:
    inv = ARCHITECTURAL_INVARIANTS[4]
    violations = _check_glob_forbidden(root, "tools/tool_*.py", frozenset({"runtime_manager"}))
    if violations:
        return InvariantResult(inv, False, "; ".join(violations[:5]))
    return InvariantResult(inv, True)


def _check_inv06_nodes(root: Path) -> InvariantResult:
    inv = ARCHITECTURAL_INVARIANTS[5]
    violations = _check_glob_forbidden(root, "nodes/nodes_*.py", frozenset({"runtime_manager"}))
    if violations:
        return InvariantResult(inv, False, "; ".join(violations[:5]))
    return InvariantResult(inv, True)


def _check_inv07_executor(root: Path) -> InvariantResult:
    inv = ARCHITECTURAL_INVARIANTS[6]
    path = root / "core" / "action_executor.py"
    source = module_source(path)
    bad = []
    if "from planner_agent" in source or "import planner_agent" in source:
        bad.append("planner_agent")
    if "from agentic_loop" in source or "import agentic_loop" in source:
        bad.append("agentic_loop")
    if "cognitive_layer" in source and "runtime.cognitive_layer" not in source:
        bad.append("direct cognitive_layer access")
    if bad:
        return InvariantResult(inv, False, f"forbidden imports/references: {bad}")
    return InvariantResult(inv, True)


def _check_inv08_typed_layers(root: Path) -> InvariantResult:
    inv = ARCHITECTURAL_INVARIANTS[7]
    path = root / "core" / "runtime_manager.py"
    source = module_source(path)
    required = [
        "state_layer: StateLayerProtocol",
        "execution_layer: ExecutionLayerProtocol",
        "cognitive_layer: CognitiveLayerProtocol",
        "operational_layer: OperationalLayerProtocol",
    ]
    missing = [ann for ann in required if ann not in source]
    if missing:
        return InvariantResult(inv, False, f"missing annotations: {missing}")
    return InvariantResult(inv, True)


def _check_inv09_layer_surfaces(root: Path) -> InvariantResult:
    inv = ARCHITECTURAL_INVARIANTS[8]
    path = root / "core" / "runtime_layers.py"
    required = {
        "CognitiveLayer": ["parse_plan", "decide_execution_mode"],
        "OperationalLayer": ["graph_executor_for", "task_scheduler"],
        "ExecutionLayer": ["execute_tool", "wire_world_model"],
        "StateLayer": ["save_run_state", "get_run_state", "list_run_states"],
    }
    missing_all: List[str] = []
    for cls, methods in required.items():
        missing = class_has_methods(path, cls, methods)
        if missing:
            missing_all.append(f"{cls}.{','.join(missing)}")
    if missing_all:
        return InvariantResult(inv, False, f"missing methods: {missing_all}")
    return InvariantResult(inv, True)


_CHECKERS: List[Callable[[Path], InvariantResult]] = [
    _check_inv01_layer_protocols,
    _check_inv02_runtime_layers,
    _check_inv03_contracts_purity,
    _check_inv04_acyclic_deps,
    _check_inv05_tools,
    _check_inv06_nodes,
    _check_inv07_executor,
    _check_inv08_typed_layers,
    _check_inv09_layer_surfaces,
]


def check_all(root: Optional[Path] = None) -> InvariantReport:
    """Run every architectural invariant and return a structured report."""
    root = root or Path(__file__).resolve().parent.parent
    results = [checker(root) for checker in _CHECKERS]
    return InvariantReport(results=results)


def main(argv: Optional[Sequence[str]] = None) -> int:
    report = check_all()
    for result in report.results:
        print(result)
    if not report.passed:
        print(f"\n{len(report.failures)} invariant(s) failed.", file=sys.stderr)
        return 1
    print(f"\nAll {len(report.results)} architectural invariants satisfied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
