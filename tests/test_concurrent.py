"""Concurrent joins. The calendar client and the Meet page are local fakes."""

from __future__ import annotations

import threading
from datetime import timedelta

from tests.conftest import FakeCalendar, due_bounds, flow_options, gcal_event, make_cfg, meet_url

from meet_notes.browser import ProfileSession
from meet_notes.calendar_sync import sync_calendar
from meet_notes.join_pool import JoinCoordinator
from meet_notes.models import FlowResult, ParsedEvent
from meet_notes.state import StateStore


def _ok() -> FlowResult:
    return FlowResult(
        opened=True,
        joined=True,
        left=True,
        notes_verified=True,
        notes_clicked=True,
        others_seen=1,
        outcome="left",
        verification_signal="test",
        mic_off=True,
        camera_off=True,
    )


def _seed(store, cfg, events, now) -> None:
    sync_calendar(store, FakeCalendar([{"items": events}]), cfg, now)


def _wait_until(predicate, timeout: float = 5) -> None:
    flag = threading.Event()
    steps = int(timeout / 0.02)
    for _ in range(steps):
        if predicate():
            return
        flag.wait(0.02)
    assert predicate(), "timed out waiting for concurrent joins"


def test_two_meetings_starting_at_the_same_minute_run_together(tmp_path):
    _assert_same_minute(tmp_path, 2)


def test_three_meetings_starting_at_the_same_minute_run_together(tmp_path):
    _assert_same_minute(tmp_path, 3)


def _assert_same_minute(tmp_path, count: int) -> None:
    cfg = make_cfg(tmp_path, max_concurrent_joins=4)
    now, start, end = due_bounds()
    store = StateStore(cfg.state_db)
    _seed(
        store,
        cfg,
        [gcal_event(f"evt-{index}", start, end, summary=f"Review {index}") for index in range(1, count + 1)],
        now,
    )
    active: set[str] = set()
    peak = 0
    lock = threading.Lock()
    entered = threading.Barrier(count)
    release = threading.Event()

    def joiner(row):
        nonlocal peak
        with lock:
            active.add(row.instance_key)
            peak = max(peak, len(active))
        entered.wait(timeout=5)
        assert release.wait(timeout=5)
        with lock:
            active.discard(row.instance_key)
        return _ok()

    coordinator = JoinCoordinator(store, joiner, cfg)
    try:
        started = coordinator.dispatch(now, wait=False)
        assert sorted(started) == [f"evt-{index}" for index in range(1, count + 1)]
        _wait_until(lambda: peak == count)
        release.set()
        coordinator.wait_idle()
        assert peak == count
        for index in range(1, count + 1):
            row = store.get(f"evt-{index}")
            assert row is not None and row.status == "left" and row.attempt_count == 1
        assert coordinator.dispatch(now, wait=True) == []
    finally:
        release.set()
        coordinator.close()
        store.close()


def test_overlapping_meeting_starts_while_the_first_is_still_open(tmp_path):
    cfg = make_cfg(tmp_path, max_concurrent_joins=4, join_lead_minutes=3, join_after_start_minutes=10)
    now, start_a, end_a = due_bounds()
    start_b = now + timedelta(minutes=20)
    end_b = start_b + timedelta(minutes=30)
    store = StateStore(cfg.state_db)
    _seed(
        store,
        cfg,
        [
            gcal_event("early", start_a, end_a, summary="Early"),
            gcal_event("later", start_b, end_b, summary="Later"),
        ],
        now,
    )
    release = threading.Event()
    seen: list[str] = []
    lock = threading.Lock()

    def joiner(row):
        with lock:
            seen.append(row.instance_key)
        assert release.wait(timeout=5)
        return _ok()

    coordinator = JoinCoordinator(store, joiner, cfg)
    try:
        assert coordinator.dispatch(now, wait=False) == ["early"]
        _wait_until(lambda: len(seen) == 1)
        later_now = start_b - timedelta(minutes=2)
        assert coordinator.dispatch(later_now, wait=False) == ["later"]
        _wait_until(lambda: len(seen) == 2)
        with lock:
            assert sorted(seen) == ["early", "later"]
        early = store.get("early")
        later = store.get("later")
        assert early is not None and early.status == "joining"
        assert later is not None and later.status == "joining"
    finally:
        release.set()
        coordinator.close()
        store.close()


def test_cap_starts_the_rest_when_a_slot_frees(tmp_path):
    cfg = make_cfg(tmp_path, max_concurrent_joins=2)
    now, start, end = due_bounds()
    store = StateStore(cfg.state_db)
    _seed(
        store,
        cfg,
        [gcal_event(f"evt-{index}", start, end) for index in range(1, 4)],
        now,
    )
    release = threading.Event()
    started: list[str] = []
    lock = threading.Lock()

    def joiner(row):
        with lock:
            started.append(row.instance_key)
        assert release.wait(timeout=5)
        return _ok()

    coordinator = JoinCoordinator(store, joiner, cfg, fill_on_finish=True, clock=lambda: now)
    try:
        assert coordinator.dispatch(now, wait=False) == ["evt-1", "evt-2"]
        waiting = store.get("evt-3")
        assert waiting is not None and waiting.status == "scheduled" and waiting.attempt_count == 0
        release.set()
        _wait_until(lambda: store.get("evt-3") is not None and store.get("evt-3").status == "left")  # type: ignore[union-attr]
        assert sorted(started) == ["evt-1", "evt-2", "evt-3"]
        third = store.get("evt-3")
        assert third is not None and third.meet_opened_at is not None and third.attempt_count == 1
    finally:
        release.set()
        coordinator.close()
        store.close()


def test_a_meeting_whose_window_closes_while_waiting_is_not_joined(tmp_path):
    cfg = make_cfg(tmp_path, max_concurrent_joins=1, join_lead_minutes=3, join_after_start_minutes=10)
    now, start, end = due_bounds()
    store = StateStore(cfg.state_db)
    _seed(
        store,
        cfg,
        [gcal_event("held", start, end), gcal_event("queued", start, end, summary="Queued")],
        now,
    )
    release = threading.Event()
    calls: list[str] = []

    def joiner(row):
        calls.append(row.instance_key)
        assert release.wait(timeout=5)
        return _ok()

    coordinator = JoinCoordinator(store, joiner, cfg)
    try:
        assert coordinator.dispatch(now, wait=False) == ["held"]
        missed_at = start + timedelta(minutes=11)
        assert missed_at < end
        assert coordinator.dispatch(missed_at, wait=False) == []
        queued = store.get("queued")
        assert queued is not None and queued.status == "missed" and queued.attempt_count == 0
        assert calls == ["held"]
    finally:
        release.set()
        coordinator.close()
        store.close()


def test_two_dispatchers_still_join_each_meeting_once(tmp_path):
    cfg = make_cfg(tmp_path, max_concurrent_joins=4)
    now, start, end = due_bounds()
    store = StateStore(cfg.state_db)
    _seed(
        store,
        cfg,
        [gcal_event("evt-1", start, end), gcal_event("evt-2", start, end, summary="Second")],
        now,
    )
    release = threading.Event()
    calls: list[str] = []
    lock = threading.Lock()
    entered = threading.Barrier(2)

    def joiner(row):
        with lock:
            calls.append(row.instance_key)
        entered.wait(timeout=5)
        assert release.wait(timeout=5)
        return _ok()

    coordinator = JoinCoordinator(store, joiner, cfg)
    batches: list[list[str]] = []

    def dispatch():
        batches.append(coordinator.dispatch(now, wait=False))

    threads = [threading.Thread(target=dispatch) for _ in range(2)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        release.set()
        coordinator.wait_idle()
        flat = [key for batch in batches for key in batch]
        assert sorted(flat) == ["evt-1", "evt-2"]
        assert sorted(calls) == ["evt-1", "evt-2"]
        for key in ("evt-1", "evt-2"):
            row = store.get(key)
            assert row is not None and row.status == "left" and row.attempt_count == 1
    finally:
        release.set()
        coordinator.close()
        store.close()


def test_shared_profile_runs_three_fixture_meetings(tmp_path, meet_server):
    cfg = make_cfg(
        tmp_path,
        max_concurrent_joins=4,
        no_sandbox=True,
        headless=True,
        screenshot_on_failure=False,
        navigation_timeout_seconds=10,
    )
    now, start, end = due_bounds()
    scenarios = {"m1": "happy", "m2": "already-on", "m3": "consent"}
    store = StateStore(cfg.state_db)
    for key, scenario in scenarios.items():
        store.insert_scheduled(
            ParsedEvent(
                instance_key=key,
                event_id=key,
                recurring_event_id=None,
                ical_uid=None,
                summary=scenario,
                meet_url=meet_url(meet_server, scenario),
                start_utc=start,
                end_utc=end,
                cancelled=False,
                declined=False,
                all_day=False,
            ),
            cfg.calendar_id,
            now,
        )
    session = ProfileSession(cfg, store)
    options = flow_options()

    def joiner(row):
        return session.join(row, threading.Event(), options)

    coordinator = JoinCoordinator(store, joiner, cfg, clock=lambda: now)
    try:
        started = coordinator.dispatch(now, wait=True)
        assert sorted(started) == ["m1", "m2", "m3"]
        assert coordinator.dispatch(now, wait=True) == []
        for key in scenarios:
            row = store.get(key)
            assert row is not None
            assert row.status == "left"
            assert row.meet_opened_at is not None
            assert row.notes_verified is True
            assert row.left_at is not None
            assert row.attempt_count == 1
    finally:
        coordinator.close()
        session.close()
        store.close()
