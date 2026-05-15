# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Workspace awareness — detect IDE, git branch, venv, task runners, dev ports and related processes."""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

IDE_PROCESS_NAMES = {
    "code.exe": "vscode",
    "cursor.exe": "cursor",
    "devenv.exe": "visual_studio",
    "pycharm64.exe": "pycharm",
    "pycharm.exe": "pycharm",
    "idea64.exe": "intellij",
    "windsurf.exe": "windsurf",
    "sublime_text.exe": "sublime",
}

DEV_SERVER_PORTS = (3000, 3001, 4200, 5000, 5173, 8000, 8080, 8888, 9229)
TASK_RUNNER_MARKERS = (
    ("package.json", "npm_scripts"),
    ("Makefile", "make"),
    ("pyproject.toml", "python_project"),
    ("tasks.json", "vscode_tasks"),
    ("nx.json", "nx"),
    ("turbo.json", "turbo"),
    ("justfile", "just"),
    ("Taskfile.yml", "taskfile"),
)


@dataclass
class WorkspaceSnapshot:
    workspace: str
    git_branch: str = ""
    git_dirty: bool = False
    git_remote: str = ""
    venv_path: str = ""
    venv_active: bool = False
    python_executable: str = ""
    ides: List[Dict[str, Any]] = field(default_factory=list)
    task_runners: List[Dict[str, Any]] = field(default_factory=list)
    dev_ports: List[Dict[str, Any]] = field(default_factory=list)
    related_processes: List[Dict[str, Any]] = field(default_factory=list)
    markers: Dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace": self.workspace,
            "git": {
                "branch": self.git_branch,
                "dirty": self.git_dirty,
                "remote": self.git_remote,
            },
            "venv": {
                "path": self.venv_path,
                "active": self.venv_active,
                "python": self.python_executable,
            },
            "ides": self.ides,
            "task_runners": self.task_runners,
            "dev_ports": self.dev_ports,
            "related_processes": self.related_processes,
            "markers": self.markers,
        }


def _run_git(workspace: Path, *args: str, timeout: float = 5.0) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            return None
        return (proc.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


def _detect_git(workspace: Path) -> tuple[str, bool, str]:
    if not (workspace / ".git").exists():
        return "", False, ""
    branch = _run_git(workspace, "rev-parse", "--abbrev-ref", "HEAD") or ""
    status = _run_git(workspace, "status", "--porcelain") or ""
    dirty = bool(status.strip())
    remote = _run_git(workspace, "remote", "get-url", "origin") or ""
    return branch, dirty, remote


def _detect_venv(workspace: Path) -> tuple[str, bool, str]:
    active = os.environ.get("VIRTUAL_ENV", "")
    if active:
        return active, True, os.environ.get("PYTHON", "") or ""

    for name in (".venv", "venv", ".env"):
        candidate = workspace / name
        if not candidate.is_dir():
            continue
        cfg = candidate / "pyvenv.cfg"
        if cfg.exists() or (candidate / "Scripts" / "python.exe").exists():
            py = candidate / "Scripts" / "python.exe"
            return str(candidate.resolve()), False, str(py) if py.exists() else ""
    return "", False, ""


def _list_processes() -> List[Dict[str, Any]]:
    """Best-effort process list on Windows via tasklist."""
    try:
        proc = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows: List[Dict[str, Any]] = []
    for line in (proc.stdout or "").splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 2:
            continue
        name, pid_s = parts[0], parts[1]
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        rows.append({"name": name, "pid": pid})
    return rows


def _detect_ides(processes: List[Dict[str, Any]], workspace: Path) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for proc in processes:
        key = proc["name"].lower()
        kind = IDE_PROCESS_NAMES.get(key)
        if kind and kind not in seen:
            seen.add(kind)
            found.append({"kind": kind, "process": proc["name"], "pid": proc["pid"]})
    if (workspace / ".vscode").is_dir() and "vscode" not in seen:
        found.append({"kind": "vscode", "process": None, "marker": ".vscode"})
    if (workspace / ".cursor").is_dir() and "cursor" not in seen:
        found.append({"kind": "cursor", "process": None, "marker": ".cursor"})
    return found


def _parse_listening_ports() -> Dict[int, int]:
    """Return port -> pid for LISTENING sockets (Windows netstat)."""
    port_pid: Dict[int, int] = {}
    try:
        proc = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return port_pid
    for line in (proc.stdout or "").splitlines():
        if "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        local = parts[1]
        pid_s = parts[-1]
        m = re.search(r":(\d+)$", local)
        if not m:
            continue
        try:
            port = int(m.group(1))
            pid = int(pid_s)
        except ValueError:
            continue
        port_pid[port] = pid
    return port_pid


def _detect_dev_ports(port_pid: Dict[int, int], processes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    pid_name = {p["pid"]: p["name"] for p in processes}
    hits: List[Dict[str, Any]] = []
    for port in DEV_SERVER_PORTS:
        pid = port_pid.get(port)
        if pid is None:
            continue
        hits.append({
            "port": port,
            "pid": pid,
            "process": pid_name.get(pid, ""),
        })
    return hits


def _detect_task_runners(workspace: Path) -> List[Dict[str, Any]]:
    runners: List[Dict[str, Any]] = []
    for filename, kind in TASK_RUNNER_MARKERS:
        path = workspace / filename
        if not path.exists():
            continue
        entry: Dict[str, Any] = {"kind": kind, "path": str(path.relative_to(workspace)).replace("\\", "/")}
        if filename == "package.json":
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                scripts = list((data.get("scripts") or {}).keys())[:20]
                entry["scripts"] = scripts
            except Exception:
                entry["scripts"] = []
        elif filename == "pyproject.toml":
            text = path.read_text(encoding="utf-8", errors="ignore")
            scripts = re.findall(r'^\s*(\w+)\s*=\s*".*"$', text, re.MULTILINE)
            entry["script_hints"] = scripts[:15]
        elif filename == "tasks.json":
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                labels = [t.get("label", "") for t in (data.get("tasks") or []) if isinstance(t, dict)]
                entry["tasks"] = [l for l in labels if l][:15]
            except Exception:
                entry["tasks"] = []
        runners.append(entry)
    return runners


def _related_dev_processes(
    workspace: Path,
    processes: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Heuristic: dev-related process names often used with local workspaces."""
    keywords = ("node", "python", "npm", "dotnet", "java", "rustc", "cargo", "uvicorn", "webpack")
    hits: List[Dict[str, Any]] = []
    for proc in processes:
        name = proc["name"].lower()
        if any(k in name for k in keywords):
            hits.append(proc)
    return hits[:25]


def detect_workspace_context(workspace: Optional[str] = None) -> WorkspaceSnapshot:
    """Build a snapshot of the development workspace environment."""
    ws = Path(workspace or os.getcwd()).resolve()
    processes = _list_processes()
    port_pid = _parse_listening_ports()
    branch, dirty, remote = _detect_git(ws)
    venv_path, venv_active, python_exe = _detect_venv(ws)

    markers = {
        "has_git": (ws / ".git").exists(),
        "has_package_json": (ws / "package.json").exists(),
        "has_pyproject": (ws / "pyproject.toml").exists(),
        "has_requirements": (ws / "requirements.txt").exists(),
    }

    return WorkspaceSnapshot(
        workspace=str(ws),
        git_branch=branch,
        git_dirty=dirty,
        git_remote=remote,
        venv_path=venv_path,
        venv_active=venv_active,
        python_executable=python_exe,
        ides=_detect_ides(processes, ws),
        task_runners=_detect_task_runners(ws),
        dev_ports=_detect_dev_ports(port_pid, processes),
        related_processes=_related_dev_processes(ws, processes),
        markers=markers,
    )
