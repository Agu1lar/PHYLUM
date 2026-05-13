import sys
from pathlib import Path

_root = Path(__file__).resolve().parent
for _sub in ("core", "tools", "agents", "nodes", "models", "providers",
             "safety", "memory", "execution", "persistence"):
    _p = str(_root / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import shutil
import pytest


def _has_pwsh() -> bool:
    return shutil.which('pwsh') is not None or shutil.which('powershell.exe') is not None


def _has_playwright() -> bool:
    try:
        import playwright  # type: ignore
        return True
    except Exception:
        return False


def pytest_runtest_setup(item):
    # Skip Windows-only tests on non-Windows platforms
    if item.get_closest_marker('windows') and not sys.platform.startswith('win'):
        pytest.skip('Windows-only test')

    # Skip tests that require pwsh if not available
    if item.get_closest_marker('requires_pwsh') and not _has_pwsh():
        pytest.skip('PowerShell (pwsh) not available')

    # Skip tests that require Playwright if package not installed
    if item.get_closest_marker('requires_playwright') and not _has_playwright():
        pytest.skip('Playwright not installed')
