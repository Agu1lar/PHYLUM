# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Task dependency graph scheduling for local runtime sub-tasks.

This module is intentionally small and data-oriented: tasks remain plain dicts,
but scheduling decisions are made from an explicit dependency graph instead of
the previous list order.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


SUCCESS_STATUSES = {"completed", "partial"}
TERMINAL_STATUSES = {"completed", "partial", "failed", "blocked", "cancelled", "rejected", "skipped"}
RUNNABLE_STATUSES = {"pending", "manual_step", "approved", "retry_scheduled"}


@dataclass
class TaskGraphSnapshot:
    dependencies: Dict[str, List[str]] = field(default_factory=dict)
    dependents: Dict[str, List[str]] = field(default_factory=dict)
    ready: List[str] = field(default_factory=list)
    pending: List[str] = field(default_factory=list)
    running: List[str] = field(default_factory=list)
    completed: List[str] = field(default_factory=list)
    partial: List[str] = field(default_factory=list)
    blocked: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dependencies": self.dependencies,
            "dependents": self.dependents,
            "ready": self.ready,
            "pending": self.pending,
            "running": self.running,
            "completed": self.completed,
            "partial": self.partial,
            "blocked": self.blocked,
            "failed": self.failed,
            "skipped": self.skipped,
        }


class TaskGraphScheduler:
    """Schedules task dicts using dependency, branch and speculation metadata.

    Supported task metadata:
    - depends_on: list of task ids that must reach completed or partial.
    - branch_group / alternative_group: alternatives where the first successful
      branch cancels remaining pending alternatives.
    - speculative: allows a read-only task to run before dependencies finish.
    - optional: a failed task does not block overall partial completion.
    """

    def __init__(self, tasks: List[Dict[str, Any]], *, max_parallel: int = 4):
        self.tasks = tasks
        self.max_parallel = max(1, int(max_parallel or 1))
        self.by_id = {task["id"]: task for task in tasks}
        self.dependencies: Dict[str, Set[str]] = {
            task["id"]: set(task.get("depends_on") or [])
            for task in tasks
        }
        self.dependents: Dict[str, Set[str]] = defaultdict(set)
        for task_id, deps in self.dependencies.items():
            for dep in deps:
                self.dependents[dep].add(task_id)
        self.validate()

    def validate(self) -> None:
        missing = {
            dep
            for deps in self.dependencies.values()
            for dep in deps
            if dep not in self.by_id
        }
        if missing:
            raise ValueError(f"Task graph has missing dependencies: {sorted(missing)}")

        indegree = {task_id: len(deps) for task_id, deps in self.dependencies.items()}
        queue = deque([task_id for task_id, degree in indegree.items() if degree == 0])
        visited = 0
        while queue:
            current = queue.popleft()
            visited += 1
            for child in self.dependents.get(current, set()):
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        if visited != len(self.by_id):
            raise ValueError("Task graph contains a dependency cycle")

    def prepare(self) -> None:
        for task in self.tasks:
            if task.get("status") == "manual_step":
                task["status"] = "pending"
            task.setdefault("depends_on", list(self.dependencies.get(task["id"], set())))
            task.setdefault("graph", {})
            task["graph"]["dependents"] = sorted(self.dependents.get(task["id"], set()))

    def next_batch(self) -> List[Dict[str, Any]]:
        self.resolve_alternative_branches()
        self.mark_blocked_by_failed_dependencies()
        ready = [task for task in self.tasks if self._is_ready(task)]
        ready.sort(key=lambda task: (self._is_speculative(task), int(task.get("priority") or 50), task.get("id", "")))
        if not ready:
            return []

        first = ready[0]
        if self._mutates_state(first) or first.get("requires_approval"):
            return [first]

        batch = []
        for task in ready:
            if len(batch) >= self.max_parallel:
                break
            if self._mutates_state(task) or task.get("requires_approval"):
                continue
            batch.append(task)
        return batch or [first]

    def all_done(self) -> bool:
        self.resolve_alternative_branches()
        self.mark_blocked_by_failed_dependencies()
        return all((task.get("status") in TERMINAL_STATUSES) for task in self.tasks)

    def has_partial_completion(self) -> bool:
        return any(task.get("status") in SUCCESS_STATUSES for task in self.tasks)

    def can_finish_partially(self) -> bool:
        if not self.all_done():
            return False
        return self.has_partial_completion() and any(task.get("status") != "completed" for task in self.tasks)

    def status_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for task in self.tasks:
            status = task.get("status") or "pending"
            counts[status] = counts.get(status, 0) + 1
        return counts

    def snapshot(self) -> Dict[str, Any]:
        ready_ids = [task["id"] for task in self.next_batch()]
        snap = TaskGraphSnapshot(
            dependencies={task_id: sorted(deps) for task_id, deps in self.dependencies.items()},
            dependents={task_id: sorted(deps) for task_id, deps in self.dependents.items()},
            ready=ready_ids,
            pending=[task["id"] for task in self.tasks if task.get("status") in RUNNABLE_STATUSES],
            running=[task["id"] for task in self.tasks if task.get("status") == "running"],
            completed=[task["id"] for task in self.tasks if task.get("status") == "completed"],
            partial=[task["id"] for task in self.tasks if task.get("status") == "partial"],
            blocked=[task["id"] for task in self.tasks if task.get("status") == "blocked"],
            failed=[task["id"] for task in self.tasks if task.get("status") in {"failed", "rejected"}],
            skipped=[task["id"] for task in self.tasks if task.get("status") == "skipped"],
        )
        return snap.to_dict()

    def resolve_alternative_branches(self) -> None:
        groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for task in self.tasks:
            group = task.get("branch_group") or task.get("alternative_group")
            if group:
                groups[str(group)].append(task)
        for group, tasks in groups.items():
            winner = next((task for task in tasks if task.get("status") in SUCCESS_STATUSES), None)
            if winner is None:
                continue
            for task in tasks:
                if task is winner:
                    continue
                if task.get("status") in RUNNABLE_STATUSES:
                    task["status"] = "skipped"
                    task["skip_reason"] = f"alternative branch '{group}' satisfied by {winner['id']}"

    def mark_blocked_by_failed_dependencies(self) -> None:
        for task in self.tasks:
            if task.get("status") not in RUNNABLE_STATUSES:
                continue
            failed_deps = [
                dep
                for dep in self.dependencies.get(task["id"], set())
                if self.by_id[dep].get("status") in {"failed", "blocked", "cancelled", "rejected"}
            ]
            if failed_deps and not task.get("speculative"):
                task["status"] = "blocked"
                task["error"] = f"blocked by failed dependencies: {', '.join(sorted(failed_deps))}"

    def _is_ready(self, task: Dict[str, Any]) -> bool:
        if task.get("status") not in RUNNABLE_STATUSES:
            return False
        deps = self.dependencies.get(task["id"], set())
        if not deps:
            return True
        if self._is_speculative(task) and not self._mutates_state(task):
            return True
        return all(self.by_id[dep].get("status") in SUCCESS_STATUSES for dep in deps)

    @staticmethod
    def _is_speculative(task: Dict[str, Any]) -> bool:
        return bool(task.get("speculative") or (task.get("execution_strategy") or {}).get("speculative"))

    @staticmethod
    def _mutates_state(task: Dict[str, Any]) -> bool:
        metadata = task.get("policy_metadata") or {}
        return bool(metadata.get("mutates_state")) or metadata.get("semantic_type") in {"mutation", "transfer", "execution"}
