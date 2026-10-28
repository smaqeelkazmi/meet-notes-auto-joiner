from __future__ import annotations

from datetime import timedelta

from tests.conftest import FakeCalendar, due_bounds, gcal_event, make_cfg

from meet_notes.calendar_sync import apply_one, parse_event, sync_calendar
from meet_notes.models import FlowResult
from meet_notes.runner import sync_and_join
from meet_notes.state import StateStore


def test_parse_recurring_declined_and_all_day():
    cfg = make_cfg_stub()
    now, start, end = due_bounds()
    recurring = gcal_event(
        "base_20261028T040000Z",
        start,
        end,
        recurringEventId="base",
        attendees=[{"self": True, "responseStatus": "declined"}],
    )
    parsed = parse_event(recurring, cfg.tzinfo)
    assert parsed is not None
    assert parsed.recurring_event_id == "base"
    assert parsed.declined is True
    assert parsed.meet_url == "https://meet.google.com/abc-defg-hij"
    all_day = {
        "id": "all-day",
        "status": "confirmed",
        "summary": "Offsite",
        "start": {"date": "2026-10-28"},
        "end": {"date": "2026-10-29"},
        "hangoutLink": "https://meet.google.com/abc-defg-hij",
    }
    parsed_day = parse_event(all_day, cfg.tzinfo)
    assert parsed_day is not None and parsed_day.all_day is True


def test_sync_inserts_reschedules_and_cancels(tmp_path):
    cfg = make_cfg(tmp_path)
    now, start, end = due_bounds()
    later = start + timedelta(hours=3)
    store = StateStore(cfg.state_db)
    client = FakeCalendar(
        [
            {"items": [gcal_event("evt-1", later, later + timedelta(minutes=30))]},
            {
                "items": [
                    gcal_event("evt-1", start, end, summary="Moved review"),
                ]
            },
            {"items": [gcal_event("evt-1", start, end, status="cancelled")]},
        ]
    )
    stats = sync_calendar(store, client, cfg, now)
    assert stats["inserted"] == 1
    row = store.get("evt-1")
    assert row is not None and row.status == "scheduled"
    assert row.start_utc == later

    sync_calendar(store, client, cfg, now)
    moved = store.get("evt-1")
    assert moved is not None
    assert moved.status == "scheduled"
    assert moved.start_utc == start
    assert moved.summary == "Moved review"

    sync_calendar(store, client, cfg, now)
    cancelled = store.get("evt-1")
    assert cancelled is not None and cancelled.status == "cancelled"
    store.close()


def test_absence_cancels_only_after_two_polls(tmp_path):
    cfg = make_cfg(tmp_path, missing_polls_before_cancel=2)
    now, start, end = due_bounds()
    store = StateStore(cfg.state_db)
    client = FakeCalendar(
        [
            {"items": [gcal_event("evt-1", start, end)]},
            {"items": []},
            {"items": []},
        ]
    )
    sync_calendar(store, client, cfg, now)
    sync_calendar(store, client, cfg, now)
    once = store.get("evt-1")
    assert once is not None and once.status == "scheduled" and once.absent_polls == 1
    sync_calendar(store, client, cfg, now)
    gone = store.get("evt-1")
    assert gone is not None and gone.status == "cancelled"
    store.close()


def test_seen_again_clears_absence(tmp_path):
    cfg = make_cfg(tmp_path, missing_polls_before_cancel=2)
    now, start, end = due_bounds()
    store = StateStore(cfg.state_db)
    client = FakeCalendar(
        [
            {"items": [gcal_event("evt-1", start, end)]},
            {"items": []},
            {"items": [gcal_event("evt-1", start, end)]},
        ]
    )
    sync_calendar(store, client, cfg, now)
    sync_calendar(store, client, cfg, now)
    sync_calendar(store, client, cfg, now)
    row = store.get("evt-1")
    assert row is not None and row.status == "scheduled" and row.absent_polls == 0
    store.close()


def test_skip_all_day_and_declined(tmp_path):
    cfg = make_cfg(tmp_path, skip_declined=True)
    now, start, end = due_bounds()
    store = StateStore(cfg.state_db)
    declined = parse_event(
        gcal_event("dec", start, end, attendees=[{"self": True, "responseStatus": "declined"}]),
        cfg.tzinfo,
    )
    assert declined is not None
    assert apply_one(store, declined, cfg, now) == "skipped_declined"
    assert store.get("dec") is None
    store.close()


def test_runner_joins_due_meeting_once(tmp_path):
    cfg = make_cfg(tmp_path)
    now, start, end = due_bounds()
    store = StateStore(cfg.state_db)
    calls: list[str] = []

    def joiner(row):
        calls.append(row.instance_key)
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

    client = FakeCalendar(
        [
            {"items": [gcal_event("evt-1", start, end), gcal_event("evt-2", start, end, summary="Second")]},
            {"items": [gcal_event("evt-1", start, end), gcal_event("evt-2", start, end, summary="Second")]},
        ]
    )
    first = sync_and_join(store, client, joiner, cfg, now, join=True)
    assert sorted(first) == ["evt-1", "evt-2"]
    assert sorted(calls) == ["evt-1", "evt-2"]
    saved = store.get("evt-1")
    assert saved is not None
    assert saved.status == "left"
    assert saved.joined_at is not None
    assert saved.notes_verified is True
    assert saved.left_at is not None
    second = sync_and_join(store, client, joiner, cfg, now, join=True)
    assert second == []
    assert sorted(calls) == ["evt-1", "evt-2"]
    store.close()


def test_runner_does_not_join_before_lead_or_after_open(tmp_path):
    cfg = make_cfg(tmp_path, join_lead_minutes=3)
    now, start, _end = due_bounds()
    early_start = now + timedelta(hours=3)
    early_end = early_start + timedelta(minutes=30)
    store = StateStore(cfg.state_db)
    calls: list[str] = []

    def joiner(row):
        calls.append(row.instance_key)
        return FlowResult(opened=True, joined=True, left=True, notes_verified=True, outcome="left")

    client = FakeCalendar(
        [
            {"items": [gcal_event("evt-1", early_start, early_end)]},
            {"items": [gcal_event("evt-1", start, start + timedelta(minutes=30))]},
        ]
    )
    assert sync_and_join(store, client, joiner, cfg, now, join=True) == []
    assert calls == []
    assert sync_and_join(store, client, joiner, cfg, now, join=True) == ["evt-1"]
    assert store.get("evt-1").meet_opened_at is not None  # type: ignore[union-attr]
    store.close()


def test_prejoin_failure_retries_until_max_then_stops(tmp_path):
    cfg = make_cfg(tmp_path, max_prejoin_attempts=2)
    now, start, end = due_bounds()
    store = StateStore(cfg.state_db)
    calls: list[str] = []

    def joiner(row):
        calls.append(row.instance_key)
        return FlowResult(opened=False, outcome="failed", error="network down")

    pages = [{"items": [gcal_event("evt-1", start, end)]} for _ in range(4)]
    client = FakeCalendar(pages)
    sync_and_join(store, client, joiner, cfg, now, join=True)
    sync_and_join(store, client, joiner, cfg, now, join=True)
    sync_and_join(store, client, joiner, cfg, now, join=True)
    assert calls == ["evt-1", "evt-1"]
    row = store.get("evt-1")
    assert row is not None and row.status == "failed_before_join"
    assert row.meet_opened_at is None
    store.close()


def test_opened_failure_is_not_retried(tmp_path):
    cfg = make_cfg(tmp_path)
    now, start, end = due_bounds()
    store = StateStore(cfg.state_db)
    calls: list[str] = []

    def joiner(row):
        calls.append(row.instance_key)
        return FlowResult(opened=True, outcome="failed", error="ui changed")

    client = FakeCalendar(
        [
            {"items": [gcal_event("evt-1", start, end)]},
            {"items": [gcal_event("evt-1", start, end)]},
        ]
    )
    sync_and_join(store, client, joiner, cfg, now, join=True)
    sync_and_join(store, client, joiner, cfg, now, join=True)
    assert calls == ["evt-1"]
    assert store.get("evt-1").status == "failed"  # type: ignore[union-attr]
    store.close()


def test_dry_run_does_not_claim(tmp_path):
    cfg = make_cfg(tmp_path, dry_run=True)
    now, start, end = due_bounds()
    store = StateStore(cfg.state_db)

    def joiner(row):
        raise AssertionError("dry run must not join")

    client = FakeCalendar([{"items": [gcal_event("evt-1", start, end)]}])
    assert sync_and_join(store, client, joiner, cfg, now, join=False) == []
    row = store.get("evt-1")
    assert row is not None and row.status == "scheduled" and row.attempt_count == 0
    store.close()


def test_in_progress_time_change_does_not_open_a_second_join(tmp_path):
    cfg = make_cfg(tmp_path)
    now, start, end = due_bounds()
    later = start + timedelta(minutes=20)
    store = StateStore(cfg.state_db)
    client = FakeCalendar(
        [
            {"items": [gcal_event("evt-1", start, end)]},
            {"items": [gcal_event("evt-1", later, later + timedelta(minutes=30), summary="Moved")]},
        ]
    )
    sync_calendar(store, client, cfg, now)
    assert store.claim_join("evt-1", now, 3) is True
    sync_calendar(store, client, cfg, now)
    row = store.get("evt-1")
    assert row is not None
    assert row.status == "joining"
    assert row.attempt_count == 1
    assert row.start_utc == start
    assert row.summary == "Moved"
    calls: list[str] = []

    def joiner(item):
        calls.append(item.instance_key)
        return FlowResult(opened=True, joined=True, left=True, notes_verified=True, outcome="left")

    again = FakeCalendar([{"items": [gcal_event("evt-1", later, later + timedelta(minutes=30), summary="Moved")]}])
    assert sync_and_join(store, again, joiner, cfg, now, join=True) == []
    assert calls == []
    store.close()


def test_pagination(tmp_path):
    cfg = make_cfg(tmp_path)
    now, start, end = due_bounds()
    store = StateStore(cfg.state_db)
    client = FakeCalendar(
        [
            {
                "items": [gcal_event("evt-1", start, end)],
                "nextPageToken": "page-2",
            },
            {"items": [gcal_event("evt-2", start, end, summary="Page two")]},
        ]
    )
    stats = sync_calendar(store, client, cfg, now)
    assert stats["inserted"] == 2
    assert client.calls[1]["page_token"] == "page-2"
    store.close()


def make_cfg_stub():
    from pathlib import Path

    return make_cfg(Path("/tmp"))
