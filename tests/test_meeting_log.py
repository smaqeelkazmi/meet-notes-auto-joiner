from __future__ import annotations

import csv

from tests.conftest import due_bounds, gcal_event, make_cfg

from meet_notes.calendar_sync import parse_event
from meet_notes.meeting_log import CSV_COLUMNS, publish_attempt
from meet_notes.models import FlowResult
from meet_notes.present import format_history
from meet_notes.state import StateStore


def test_meeting_log_is_append_only_and_history_shows_the_reason(tmp_path):
    cfg = make_cfg(tmp_path)
    now, start, end = due_bounds()
    store = StateStore(cfg.state_db)
    event = parse_event(gcal_event("evt-1", start, end, summary="Design review"), cfg.tzinfo)
    assert event is not None
    store.insert_scheduled(event, "primary", now)
    store.claim_join("evt-1", now, 3)
    store.save_flow_result(
        "evt-1",
        FlowResult(
            opened=True,
            joined=True,
            left=True,
            notes_verified=True,
            notes_clicked=True,
            mic_off=True,
            camera_off=True,
            outcome="left",
            verification_signal="stop-button",
        ),
        now,
    )
    publish_attempt(cfg, store, "evt-1", now, email=False)
    publish_attempt(cfg, store, "evt-1", now, email=False)

    path = tmp_path / "meeting-log.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == list(CSV_COLUMNS)
    assert len(rows) == 1
    assert rows[0]["title"] == "Design review"
    assert rows[0]["notes_started"] == "yes"
    assert rows[0]["outcome"] == "notes_started"
    assert "confirmed" in rows[0]["reason"]
    assert rows[0]["scheduled_start"]
    assert rows[0]["joined_at"]
    assert rows[0]["left_at"]

    text = format_history(store.history(5), cfg.tzinfo, as_json=False)
    assert "notes_started" in text
    assert "notes=yes" in text
    assert rows[0]["reason"] in text
    store.close()


def test_notes_failure_is_logged_with_a_plain_reason(tmp_path):
    cfg = make_cfg(tmp_path)
    now, start, end = due_bounds()
    store = StateStore(cfg.state_db)
    event = parse_event(gcal_event("evt-2", start, end, summary="Standup"), cfg.tzinfo)
    assert event is not None
    store.insert_scheduled(event, "primary", now)
    store.claim_join("evt-2", now, 3)
    store.save_flow_result(
        "evt-2",
        FlowResult(
            opened=True,
            joined=True,
            left=True,
            outcome="notes_unavailable",
            error="The notes control was not in the Meet UI. The bot is leaving the call.",
        ),
        now,
    )
    publish_attempt(cfg, store, "evt-2", now, email=False)
    with (tmp_path / "meeting-log.csv").open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["notes_started"] == "no"
    assert row["outcome"] == "notes_failed"
    assert "leaving the call" in row["reason"]
    text = format_history(store.history(5), cfg.tzinfo, as_json=False)
    assert "notes_failed" in text
    assert "leaving the call" in text
    store.close()
