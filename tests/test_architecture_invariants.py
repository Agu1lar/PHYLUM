# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""CI gate for documented architectural invariants."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

from architecture_invariants import ARCHITECTURAL_INVARIANTS, check_all


@pytest.mark.architecture
class TestArchitectureInvariantCatalog:
    def test_invariants_are_documented_with_unique_ids(self):
        ids = [inv.id for inv in ARCHITECTURAL_INVARIANTS]
        assert len(ids) == len(set(ids))
        assert len(ids) >= 9

    def test_each_invariant_has_description(self):
        for inv in ARCHITECTURAL_INVARIANTS:
            assert inv.id.startswith("INV-")
            assert inv.name
            assert len(inv.description) > 10


@pytest.mark.architecture
class TestArchitectureInvariantChecks:
    def test_all_invariants_pass(self):
        report = check_all(ROOT)
        failures = report.failures
        assert not failures, "\n".join(str(f) for f in failures)

    def test_cli_entrypoint_returns_zero(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "core" / "architecture_invariants.py")],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
