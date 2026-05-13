import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from tool_base import BaseTool

logger = logging.getLogger(__name__)

UNINSTALL_ROOTS = [
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", False),
    (r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", False),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", True),
]

APP_PATHS_ROOTS = [
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths", False),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths", True),
]


def _registry_apps() -> List[Dict[str, Any]]:
    apps: List[Dict[str, Any]] = []
    try:
        import winreg
    except Exception:
        return apps

    for subkey, current_user in UNINSTALL_ROOTS:
        root = winreg.HKEY_CURRENT_USER if current_user else winreg.HKEY_LOCAL_MACHINE
        try:
            with winreg.OpenKey(root, subkey) as parent:
                for index in range(winreg.QueryInfoKey(parent)[0]):
                    key_name = winreg.EnumKey(parent, index)
                    try:
                        with winreg.OpenKey(parent, key_name) as child:
                            def _read(name: str) -> Optional[str]:
                                try:
                                    value, _ = winreg.QueryValueEx(child, name)
                                    return str(value)
                                except OSError:
                                    return None

                            display_name = _read("DisplayName")
                            if not display_name:
                                continue
                            apps.append(
                                {
                                    "name": display_name,
                                    "version": _read("DisplayVersion"),
                                    "publisher": _read("Publisher"),
                                    "install_location": _read("InstallLocation"),
                                    "uninstall_string": _read("UninstallString"),
                                }
                            )
                    except OSError:
                        continue
        except OSError:
            continue
    return apps


def _common_executable_roots() -> List[Path]:
    roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))),
        Path.home() / "AppData" / "Roaming",
    ]
    return [root for root in roots if root.exists()]


def _app_paths_lookup(executable_name: str) -> List[str]:
    matches: List[str] = []
    try:
        import winreg
    except Exception:
        return matches

    for subkey, current_user in APP_PATHS_ROOTS:
        root = winreg.HKEY_CURRENT_USER if current_user else winreg.HKEY_LOCAL_MACHINE
        try:
            with winreg.OpenKey(root, f"{subkey}\\{executable_name}") as key:
                value, _ = winreg.QueryValueEx(key, "")
                if value:
                    matches.append(str(value))
        except OSError:
            continue
    return matches


def _candidate_executable_names(query: str) -> List[str]:
    normalized = (query or "").strip()
    if not normalized:
        return []
    candidates = [normalized]
    if not normalized.lower().endswith(".exe"):
        candidates.extend([f"{normalized}.exe", normalized.replace(" ", "") + ".exe"])
    seen = set()
    ordered: List[str] = []
    for item in candidates:
        lowered = item.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        ordered.append(item)
    return ordered


class SoftwareInput(BaseModel):
    action: str = Field(..., pattern='^(list_installed|search_installed|find_executable|resolve_command|find_install_location|find_uninstaller)$')
    query: Optional[str] = None
    command: Optional[str] = None


class SoftwareOutput(BaseModel):
    success: bool
    message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class SoftwareInventoryTool(BaseTool):
    InputModel = SoftwareInput
    OutputModel = SoftwareOutput

    async def validate(self, payload: SoftwareInput) -> None:
        if payload.action in {"search_installed", "find_executable", "find_install_location", "find_uninstaller"} and not payload.query:
            raise ValueError("query is required")
        if payload.action == "resolve_command" and not payload.command:
            raise ValueError("command is required")

    async def _run(self, payload: SoftwareInput) -> SoftwareOutput:
        apps = _registry_apps()

        if payload.action == "list_installed":
            return SoftwareOutput(success=True, message="list_installed", details={"apps": apps})

        if payload.action == "search_installed":
            query = (payload.query or "").lower()
            matches = [app for app in apps if query in (app.get("name") or "").lower()]
            return SoftwareOutput(success=True, message="search_installed", details={"matches": matches})

        if payload.action == "find_install_location":
            query = (payload.query or "").lower()
            matches = [
                {
                    "name": app.get("name"),
                    "install_location": app.get("install_location"),
                    "version": app.get("version"),
                }
                for app in apps
                if query in (app.get("name") or "").lower()
            ]
            return SoftwareOutput(success=bool(matches), message="find_install_location", details={"matches": matches})

        if payload.action == "find_uninstaller":
            query = (payload.query or "").lower()
            matches = [
                {
                    "name": app.get("name"),
                    "uninstall_string": app.get("uninstall_string"),
                    "version": app.get("version"),
                }
                for app in apps
                if query in (app.get("name") or "").lower()
            ]
            return SoftwareOutput(success=bool(matches), message="find_uninstaller", details={"matches": matches})

        if payload.action == "resolve_command":
            command = payload.command or ""
            resolved = shutil.which(command)
            if not resolved:
                for candidate in _candidate_executable_names(command):
                    registry_matches = _app_paths_lookup(candidate)
                    if registry_matches:
                        resolved = registry_matches[0]
                        break
            return SoftwareOutput(success=bool(resolved), message="resolve_command", details={"command": command, "path": resolved})

        if payload.action == "find_executable":
            query = (payload.query or "").lower()
            if shutil.which(payload.query or ""):
                path = shutil.which(payload.query or "")
                return SoftwareOutput(success=True, message="find_executable", details={"matches": [path]})

            matches: List[str] = []
            for candidate in _candidate_executable_names(payload.query or ""):
                for registry_match in _app_paths_lookup(candidate):
                    matches.append(registry_match)
                if matches:
                    return SoftwareOutput(success=True, message="find_executable", details={"matches": matches})

            for root in _common_executable_roots():
                try:
                    for candidate in root.rglob("*.exe"):
                        if query in candidate.name.lower():
                            matches.append(str(candidate))
                        if len(matches) >= 25:
                            break
                except Exception:
                    logger.exception("executable scan failed for %s", root)
                if len(matches) >= 25:
                    break

            if not matches:
                for app in apps:
                    install_location = app.get("install_location")
                    if not install_location or query not in (app.get("name") or "").lower():
                        continue
                    location = Path(install_location)
                    if not location.exists():
                        continue
                    try:
                        for candidate in location.rglob("*.exe"):
                            matches.append(str(candidate))
                            if len(matches) >= 25:
                                break
                    except Exception:
                        logger.exception("install_location scan failed for %s", install_location)
                    if len(matches) >= 25:
                        break
            return SoftwareOutput(success=bool(matches), message="find_executable", details={"matches": matches})

        raise ValueError(f"unsupported software_inventory action: {payload.action}")
