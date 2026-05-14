# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Visual Run Replay — timeline of screenshots with action annotations.

Records a sequence of (screenshot, action, result) entries during a run
so the user can later replay what the agent did visually, with redaction
of sensitive regions before storage.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

REPLAY_DIR = Path(__file__).resolve().parent.parent / "agent_workspace" / "replays"


@dataclass
class ReplayEntry:
    """A single step in the visual replay timeline."""
    timestamp: float
    action: str
    tool: str
    task_id: str
    screenshot_path: Optional[str] = None
    screenshot_hash: Optional[str] = None
    result_status: Optional[str] = None
    result_summary: Optional[str] = None
    annotations: List[str] = field(default_factory=list)
    visual_verification: Optional[Dict[str, Any]] = None
    redacted: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "action": self.action,
            "tool": self.tool,
            "task_id": self.task_id,
            "screenshot_path": self.screenshot_path,
            "screenshot_hash": self.screenshot_hash,
            "result_status": self.result_status,
            "result_summary": self.result_summary,
            "annotations": self.annotations,
            "visual_verification": self.visual_verification,
            "redacted": self.redacted,
        }


class VisualReplayRecorder:
    """Records visual entries during a run for later replay."""

    def __init__(self, run_id: str, *, replay_dir: Optional[Path] = None):
        self.run_id = run_id
        self.replay_dir = replay_dir or REPLAY_DIR
        self.replay_dir.mkdir(parents=True, exist_ok=True)
        self.entries: List[ReplayEntry] = []
        self._start_time = time.time()

    def record(
        self,
        *,
        action: str,
        tool: str,
        task_id: str,
        screenshot_path: Optional[str] = None,
        screenshot_hash: Optional[str] = None,
        result_status: Optional[str] = None,
        result_summary: Optional[str] = None,
        annotations: Optional[List[str]] = None,
        visual_verification: Optional[Dict[str, Any]] = None,
        redacted: bool = False,
    ) -> ReplayEntry:
        entry = ReplayEntry(
            timestamp=time.time(),
            action=action,
            tool=tool,
            task_id=task_id,
            screenshot_path=screenshot_path,
            screenshot_hash=screenshot_hash,
            result_status=result_status,
            result_summary=result_summary,
            annotations=annotations or [],
            visual_verification=visual_verification,
            redacted=redacted,
        )
        self.entries.append(entry)
        return entry

    def save(self) -> str:
        """Persist the replay timeline to disk as JSON."""
        replay_file = self.replay_dir / f"replay_{self.run_id}.json"
        data = {
            "run_id": self.run_id,
            "start_time": self._start_time,
            "end_time": time.time(),
            "entry_count": len(self.entries),
            "entries": [e.to_dict() for e in self.entries],
        }
        replay_file.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        return str(replay_file)

    @staticmethod
    def load(replay_path: str) -> Dict[str, Any]:
        """Load a replay from disk."""
        return json.loads(Path(replay_path).read_text(encoding="utf-8"))

    @property
    def duration(self) -> float:
        if not self.entries:
            return 0.0
        return self.entries[-1].timestamp - self._start_time

    @property
    def summary(self) -> Dict[str, Any]:
        statuses = {}
        for e in self.entries:
            s = e.result_status or "unknown"
            statuses[s] = statuses.get(s, 0) + 1
        return {
            "run_id": self.run_id,
            "entry_count": len(self.entries),
            "duration_seconds": round(self.duration, 2),
            "status_counts": statuses,
            "has_screenshots": any(e.screenshot_path for e in self.entries),
            "has_errors": any(
                e.visual_verification and e.visual_verification.get("verdict") == "error_detected"
                for e in self.entries
            ),
        }
