"""Test suite for the EventBus.

Tests:
  - EventType enum exhaustiveness
  - Subscribe / unsubscribe by type and wildcard
  - Emission dispatches to correct handlers
  - Parallel handler execution
  - Failed handlers do not break other subscribers
  - emit_raw resolves known types and falls back to CUSTOM
  - History recording and cap
  - Singleton get_event_bus / reset_event_bus
  - Event envelope fields (type, payload, timestamp, source)
  - Introspection helpers (subscriber_count, subscribers_for)
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from event_bus import (
    Event,
    EventBus,
    EventType,
    get_event_bus,
    reset_event_bus,
)


# ─── Event envelope ─────────────────────────────────────────────────


class TestEvent:
    def test_event_fields(self):
        ev = Event(EventType.TASK_STARTED, {"task_id": "t1"}, source="test")
        assert ev.type == EventType.TASK_STARTED
        assert ev.payload == {"task_id": "t1"}
        assert ev.source == "test"
        assert ev.timestamp  # ISO string

    def test_event_to_dict(self):
        ev = Event(EventType.RUN_STARTED, {"rid": "r1"})
        d = ev.to_dict()
        assert d["type"] == "run_started"
        assert d["payload"] == {"rid": "r1"}
        assert "timestamp" in d

    def test_event_repr(self):
        ev = Event(EventType.GOAL_ENQUEUED, {}, source="src")
        assert "goal_enqueued" in repr(ev)


# ─── EventType enum ─────────────────────────────────────────────────


class TestEventType:
    def test_all_values_are_snake_case_strings(self):
        for et in EventType:
            assert et.value == et.value.lower()
            assert "_" in et.value or et.value.isalpha()

    def test_known_event_types_exist(self):
        required = [
            "run_started", "run_finished", "run_failed", "run_cancelled",
            "task_planned", "task_started", "task_finished", "task_failed",
            "approval_requested", "approval_resolved",
            "goal_enqueued", "goal_cancelled",
            "session_created", "session_closed",
            "agent_step", "agent_thinking",
            "script_recovery_started", "script_recovery_succeeded",
            "user_input_requested", "user_input_received",
            "custom",
        ]
        values = {et.value for et in EventType}
        for r in required:
            assert r in values, f"Missing EventType: {r}"


# ─── Subscribe / emit ───────────────────────────────────────────────


class TestSubscribeAndEmit:
    @pytest.mark.asyncio
    async def test_typed_subscriber_receives_event(self):
        bus = EventBus()
        received = []

        async def handler(event: Event):
            received.append(event)

        bus.subscribe(EventType.TASK_STARTED, handler)
        await bus.emit(EventType.TASK_STARTED, {"task_id": "t1"})
        assert len(received) == 1
        assert received[0].type == EventType.TASK_STARTED

    @pytest.mark.asyncio
    async def test_typed_subscriber_ignores_other_types(self):
        bus = EventBus()
        received = []

        async def handler(event: Event):
            received.append(event)

        bus.subscribe(EventType.TASK_STARTED, handler)
        await bus.emit(EventType.TASK_FAILED, {"task_id": "t1"})
        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_wildcard_subscriber_receives_all(self):
        bus = EventBus()
        received = []

        async def handler(event: Event):
            received.append(event)

        bus.subscribe_all(handler)
        await bus.emit(EventType.TASK_STARTED, {"a": 1})
        await bus.emit(EventType.RUN_FAILED, {"b": 2})
        await bus.emit(EventType.GOAL_ENQUEUED, {"c": 3})
        assert len(received) == 3

    @pytest.mark.asyncio
    async def test_multiple_subscribers_same_type(self):
        bus = EventBus()
        a_received = []
        b_received = []

        async def handler_a(event: Event):
            a_received.append(event)

        async def handler_b(event: Event):
            b_received.append(event)

        bus.subscribe(EventType.RUN_STARTED, handler_a)
        bus.subscribe(EventType.RUN_STARTED, handler_b)
        await bus.emit(EventType.RUN_STARTED, {"rid": "r1"})
        assert len(a_received) == 1
        assert len(b_received) == 1

    @pytest.mark.asyncio
    async def test_duplicate_subscribe_ignored(self):
        bus = EventBus()

        async def handler(event: Event):
            pass

        bus.subscribe(EventType.RUN_STARTED, handler)
        bus.subscribe(EventType.RUN_STARTED, handler)
        assert bus.subscribers_for(EventType.RUN_STARTED) == 1


# ─── Unsubscribe ─────────────────────────────────────────────────────


class TestUnsubscribe:
    @pytest.mark.asyncio
    async def test_unsubscribe_typed(self):
        bus = EventBus()
        received = []

        async def handler(event: Event):
            received.append(event)

        bus.subscribe(EventType.TASK_FINISHED, handler)
        await bus.emit(EventType.TASK_FINISHED, {})
        assert len(received) == 1

        bus.unsubscribe(EventType.TASK_FINISHED, handler)
        await bus.emit(EventType.TASK_FINISHED, {})
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_unsubscribe_all(self):
        bus = EventBus()
        received = []

        async def handler(event: Event):
            received.append(event)

        bus.subscribe_all(handler)
        bus.subscribe(EventType.TASK_STARTED, handler)
        bus.unsubscribe_all(handler)
        await bus.emit(EventType.TASK_STARTED, {})
        assert len(received) == 0

    def test_unsubscribe_nonexistent_handler_no_error(self):
        bus = EventBus()

        async def handler(event: Event):
            pass

        bus.unsubscribe(EventType.RUN_STARTED, handler)


# ─── Parallel execution ─────────────────────────────────────────────


class TestParallelExecution:
    @pytest.mark.asyncio
    async def test_handlers_run_concurrently(self):
        bus = EventBus()
        order = []

        async def slow_handler(event: Event):
            await asyncio.sleep(0.05)
            order.append("slow")

        async def fast_handler(event: Event):
            order.append("fast")

        bus.subscribe(EventType.AGENT_STEP, slow_handler)
        bus.subscribe(EventType.AGENT_STEP, fast_handler)
        await bus.emit(EventType.AGENT_STEP, {})
        assert "fast" in order
        assert "slow" in order


# ─── Error isolation ─────────────────────────────────────────────────


class TestErrorIsolation:
    @pytest.mark.asyncio
    async def test_failing_handler_does_not_break_others(self):
        bus = EventBus()
        received = []

        async def bad_handler(event: Event):
            raise ValueError("boom")

        async def good_handler(event: Event):
            received.append(event)

        bus.subscribe(EventType.RUN_STARTED, bad_handler)
        bus.subscribe(EventType.RUN_STARTED, good_handler)
        await bus.emit(EventType.RUN_STARTED, {"rid": "r1"})
        assert len(received) == 1


# ─── emit_raw ────────────────────────────────────────────────────────


class TestEmitRaw:
    @pytest.mark.asyncio
    async def test_raw_resolves_known_type(self):
        bus = EventBus()
        received = []

        async def handler(event: Event):
            received.append(event)

        bus.subscribe(EventType.TASK_PLANNED, handler)
        await bus.emit_raw("task_planned", {"task": "t1"})
        assert len(received) == 1
        assert received[0].type == EventType.TASK_PLANNED

    @pytest.mark.asyncio
    async def test_raw_unknown_falls_to_custom(self):
        bus = EventBus()
        received = []

        async def handler(event: Event):
            received.append(event)

        bus.subscribe(EventType.CUSTOM, handler)
        await bus.emit_raw("some_unknown_event", {"data": 42})
        assert len(received) == 1
        assert received[0].type == EventType.CUSTOM
        assert received[0].payload["_original_type"] == "some_unknown_event"

    @pytest.mark.asyncio
    async def test_raw_source_propagated(self):
        bus = EventBus()
        event = await bus.emit_raw("run_started", {}, source="test_src")
        assert event.source == "test_src"


# ─── History ─────────────────────────────────────────────────────────


class TestHistory:
    @pytest.mark.asyncio
    async def test_history_records_events(self):
        bus = EventBus()
        await bus.emit(EventType.RUN_STARTED, {"a": 1})
        await bus.emit(EventType.RUN_FINISHED, {"b": 2})
        assert len(bus.history) == 2
        assert bus.history[0].type == EventType.RUN_STARTED
        assert bus.history[1].type == EventType.RUN_FINISHED

    @pytest.mark.asyncio
    async def test_history_capped(self):
        bus = EventBus()
        bus._max_history = 5
        for i in range(10):
            await bus.emit(EventType.AGENT_STEP, {"i": i})
        assert len(bus.history) == 5
        assert bus.history[0].payload["i"] == 5

    @pytest.mark.asyncio
    async def test_clear_removes_subscribers_and_history(self):
        bus = EventBus()

        async def handler(event: Event):
            pass

        bus.subscribe(EventType.RUN_STARTED, handler)
        await bus.emit(EventType.RUN_STARTED, {})
        assert bus.subscriber_count > 0
        assert len(bus.history) > 0

        bus.clear()
        assert bus.subscriber_count == 0
        assert len(bus.history) == 0


# ─── Introspection ───────────────────────────────────────────────────


class TestIntrospection:
    def test_subscriber_count(self):
        bus = EventBus()

        async def h1(e):
            pass

        async def h2(e):
            pass

        bus.subscribe(EventType.RUN_STARTED, h1)
        bus.subscribe(EventType.TASK_FAILED, h2)
        bus.subscribe_all(h1)
        assert bus.subscriber_count == 3

    def test_subscribers_for_includes_wildcard(self):
        bus = EventBus()

        async def h1(e):
            pass

        async def h2(e):
            pass

        bus.subscribe(EventType.RUN_STARTED, h1)
        bus.subscribe_all(h2)
        assert bus.subscribers_for(EventType.RUN_STARTED) == 2
        assert bus.subscribers_for(EventType.RUN_FAILED) == 1


# ─── Singleton ───────────────────────────────────────────────────────


class TestSingleton:
    def test_get_returns_same_instance(self):
        reset_event_bus()
        a = get_event_bus()
        b = get_event_bus()
        assert a is b

    def test_reset_creates_new_instance(self):
        a = get_event_bus()
        b = reset_event_bus()
        assert a is not b
        assert get_event_bus() is b


# ─── Emit returns Event ─────────────────────────────────────────────


class TestEmitReturn:
    @pytest.mark.asyncio
    async def test_emit_returns_event_object(self):
        bus = EventBus()
        ev = await bus.emit(EventType.GOAL_ENQUEUED, {"goal": "g1"}, source="test")
        assert isinstance(ev, Event)
        assert ev.type == EventType.GOAL_ENQUEUED
        assert ev.payload == {"goal": "g1"}
        assert ev.source == "test"


# ─── Mixed typed + wildcard ──────────────────────────────────────────


class TestMixedSubscribers:
    @pytest.mark.asyncio
    async def test_typed_and_wildcard_both_called(self):
        bus = EventBus()
        typed_hits = []
        wildcard_hits = []

        async def typed_handler(event: Event):
            typed_hits.append(event)

        async def wildcard_handler(event: Event):
            wildcard_hits.append(event)

        bus.subscribe(EventType.APPROVAL_REQUESTED, typed_handler)
        bus.subscribe_all(wildcard_handler)

        await bus.emit(EventType.APPROVAL_REQUESTED, {"a": 1})
        assert len(typed_hits) == 1
        assert len(wildcard_hits) == 1

        await bus.emit(EventType.RUN_FAILED, {"b": 2})
        assert len(typed_hits) == 1
        assert len(wildcard_hits) == 2


# ─── No subscribers ─────────────────────────────────────────────────


class TestNoSubscribers:
    @pytest.mark.asyncio
    async def test_emit_with_no_subscribers_succeeds(self):
        bus = EventBus()
        ev = await bus.emit(EventType.SESSION_CLOSED, {"sid": "s1"})
        assert ev.type == EventType.SESSION_CLOSED
        assert len(bus.history) == 1
