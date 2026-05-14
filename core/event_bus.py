# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Explicit event bus for decoupling producers from consumers.

Producers emit typed events without knowing who listens. Consumers
subscribe by event type (or to all events) and receive payloads
asynchronously. The WebSocket emitter becomes just another subscriber.

Usage:
    bus = EventBus()
    bus.subscribe(EventType.TASK_STARTED, my_handler)
    bus.subscribe_all(logging_handler)
    await bus.emit(EventType.TASK_STARTED, {"task_id": "t1", ...})
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

EventHandler = Callable[["Event"], Awaitable[None]]


class EventType(str, Enum):
    # --- Run lifecycle ---
    RUN_STARTED = "run_started"
    RUN_RESUMED = "run_resumed"
    RUN_FINISHED = "run_finished"
    RUN_FAILED = "run_failed"
    RUN_PAUSED = "run_paused"
    RUN_CANCELLATION_REQUESTED = "run_cancellation_requested"
    RUN_CANCELLED = "run_cancelled"
    RUN_DELETED = "run_deleted"

    # --- Goal lifecycle ---
    GOAL_ENQUEUED = "goal_enqueued"
    GOAL_CANCELLED = "goal_cancelled"

    # --- Session lifecycle ---
    SESSION_CREATED = "session_created"
    SESSION_CLOSED = "session_closed"

    # --- Task lifecycle ---
    TASK_PLANNED = "task_planned"
    TASK_STARTED = "task_started"
    TASK_FINISHED = "task_finished"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"
    TASK_RETRY_SCHEDULED = "task_retry_scheduled"
    TASK_GRAPH_BUILT = "task_graph_built"
    TASK_BRANCH_BATCH_STARTED = "task_branch_batch_started"
    TASK_BRANCH_BATCH_FINISHED = "task_branch_batch_finished"

    # --- Approval flow ---
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    APPROVAL_GRANT_CREATED = "approval_grant_created"
    APPROVAL_GRANT_REVOKED = "approval_grant_revoked"

    # --- Recovery ---
    SCRIPT_RECOVERY_STARTED = "script_recovery_started"
    SCRIPT_RECOVERY_SUCCEEDED = "script_recovery_succeeded"

    # --- Agentic loop ---
    AGENT_STEP = "agent_step"
    AGENT_THINKING = "agent_thinking"
    TOOL_CALL_PROPOSED = "tool_call_proposed"

    # --- Handoff ---
    USER_INPUT_REQUESTED = "user_input_requested"
    USER_INPUT_RECEIVED = "user_input_received"

    # --- Catch-all for forward compatibility ---
    CUSTOM = "custom"


class Event:
    """Immutable event envelope."""

    __slots__ = ("type", "payload", "timestamp", "source")

    def __init__(
        self,
        event_type: EventType,
        payload: Dict[str, Any],
        *,
        source: Optional[str] = None,
    ):
        self.type = event_type
        self.payload = payload
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.source = source

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type.value,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "source": self.source,
        }

    def __repr__(self) -> str:
        return f"Event({self.type.value}, source={self.source})"


_WILDCARD = "__all__"


class EventBus:
    """Async pub/sub event bus.

    * Subscribe to a specific EventType or to *all* events.
    * Handlers run concurrently via ``asyncio.gather``.
    * Failed handlers are logged but never break other subscribers.
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, List[EventHandler]] = defaultdict(list)
        self._history: List[Event] = []
        self._max_history = 500

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Register *handler* for a specific event type."""
        key = event_type.value
        if handler not in self._handlers[key]:
            self._handlers[key].append(handler)

    def subscribe_all(self, handler: EventHandler) -> None:
        """Register *handler* that receives every event regardless of type."""
        if handler not in self._handlers[_WILDCARD]:
            self._handlers[_WILDCARD].append(handler)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        key = event_type.value
        handlers = self._handlers.get(key)
        if handlers and handler in handlers:
            handlers.remove(handler)

    def unsubscribe_all(self, handler: EventHandler) -> None:
        for key in list(self._handlers):
            if handler in self._handlers[key]:
                self._handlers[key].remove(handler)

    # ------------------------------------------------------------------
    # Emission
    # ------------------------------------------------------------------

    async def emit(
        self,
        event_type: EventType,
        payload: Dict[str, Any],
        *,
        source: Optional[str] = None,
    ) -> Event:
        """Create and dispatch an event to all matching subscribers."""
        event = Event(event_type, payload, source=source)
        self._record(event)

        handlers: List[EventHandler] = []
        handlers.extend(self._handlers.get(event_type.value, []))
        handlers.extend(self._handlers.get(_WILDCARD, []))

        if handlers:
            results = await asyncio.gather(
                *(self._safe_call(h, event) for h in handlers),
                return_exceptions=True,
            )
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(
                        "EventBus handler %s failed for %s: %s",
                        handlers[i],
                        event_type.value,
                        result,
                    )
        return event

    async def emit_raw(
        self,
        event_type_str: str,
        payload: Dict[str, Any],
        *,
        source: Optional[str] = None,
    ) -> Event:
        """Emit using a raw string type, resolving to EventType or CUSTOM."""
        try:
            et = EventType(event_type_str)
        except ValueError:
            et = EventType.CUSTOM
            payload = {**payload, "_original_type": event_type_str}
        return await self.emit(et, payload, source=source)

    # ------------------------------------------------------------------
    # History / introspection
    # ------------------------------------------------------------------

    @property
    def history(self) -> List[Event]:
        return list(self._history)

    @property
    def subscriber_count(self) -> int:
        return sum(len(v) for v in self._handlers.values())

    def subscribers_for(self, event_type: EventType) -> int:
        specific = len(self._handlers.get(event_type.value, []))
        wildcard = len(self._handlers.get(_WILDCARD, []))
        return specific + wildcard

    def clear(self) -> None:
        """Remove all subscribers and history."""
        self._handlers.clear()
        self._history.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    async def _safe_call(handler: EventHandler, event: Event) -> None:
        try:
            await handler(event)
        except Exception:
            raise

    def _record(self, event: Event) -> None:
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]


# Singleton for the process — importable from anywhere.
_default_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Return (and lazily create) the process-wide EventBus singleton."""
    global _default_bus
    if _default_bus is None:
        _default_bus = EventBus()
    return _default_bus


def reset_event_bus() -> EventBus:
    """Replace the singleton with a fresh bus (useful in tests)."""
    global _default_bus
    _default_bus = EventBus()
    return _default_bus
