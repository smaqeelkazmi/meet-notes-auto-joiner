from __future__ import annotations

import csv
import smtplib

from tests.conftest import FakeCalendar, due_bounds, gcal_event, make_cfg

from meet_notes.models import FlowResult
from meet_notes.present import format_history, format_preview
from meet_notes.runner import preview_rows, sync_and_join
from meet_notes.state import StateStore


class _RecordingSMTP:
    bodies: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    def ehlo(self):
        return None

    def starttls(self, context=None):
        return None

    def login(self, user, password):
        return None

    def send_message(self, message):
        _RecordingSMTP.bodies.append(message.get_content())

    def quit(self):
        return None


def test_skips_events_this_account_does_not_organize(tmp_path, monkeypatch):
    _RecordingSMTP.bodies = []
    monkeypatch.setattr(smtplib, "SMTP", _RecordingSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _RecordingSMTP)
    monkeypatch.setenv("MEET_NOTES_SMTP_PASSWORD", "s3cret-password")
    cfg = make_cfg(
        tmp_path,
        notify_enabled=True,
        notify_to="aqeel@example.com",
        notify_from="notes@example.com",
        notify_smtp_host="smtp.example.com",
        notify_smtp_port=465,
        notify_smtp_ssl=True,
        notify_smtp_username="notes@example.com",
    )
    now, start, end = due_bounds()
    guest = gcal_event(
        "guest",
        start,
        end,
        summary="Someone else's sync",
        organizer={"email": "sam@example.com", "self": False},
    )
    mine = gcal_event("mine", start, end, summary="My sync")
    client = FakeCalendar([{"items": [guest, mine]}])
    store = StateStore(cfg.state_db)
    calls: list[str] = []

    def joiner(row):
        calls.append(row.instance_key)
        return FlowResult(opened=True, joined=True, left=True, notes_verified=True, outcome="left")

    started = sync_and_join(store, client, joiner, cfg, now, join=True)
    assert started == ["mine"]
    assert calls == ["mine"]
    row = store.get("guest")
    assert row is not None
    assert row.status == "skipped_not_organizer"
    assert row.outcome == "skipped"
    assert row.reason == "not organizer (organizer: sam@example.com)"

    text = format_history([row], cfg.tzinfo, as_json=False)
    assert "skipped" in text
    assert "not organizer (organizer: sam@example.com)" in text
    preview = format_preview(preview_rows(store, cfg, now), cfg.tzinfo)
    assert "not organizer (organizer: sam@example.com)" in preview

    with (tmp_path / "meeting-log.csv").open(encoding="utf-8", newline="") as handle:
        logged = list(csv.DictReader(handle))
    guest_rows = [item for item in logged if item["title"] == "Someone else's sync"]
    assert len(guest_rows) == 1
    assert guest_rows[0]["outcome"] == "skipped"
    assert guest_rows[0]["reason"] == "not organizer (organizer: sam@example.com)"
    assert not any("Someone else's sync" in body for body in _RecordingSMTP.bodies)
    assert any("My sync" in body for body in _RecordingSMTP.bodies)

    sync_and_join(store, FakeCalendar([{"items": [guest, mine]}]), joiner, cfg, now, join=False)
    with (tmp_path / "meeting-log.csv").open(encoding="utf-8", newline="") as handle:
        again = list(csv.DictReader(handle))
    assert len([item for item in again if item["title"] == "Someone else's sync"]) == 1
    store.close()


def test_missing_self_falls_back_to_the_account_email(tmp_path):
    cfg = make_cfg(tmp_path)
    now, start, end = due_bounds()
    owned = gcal_event(
        "owned",
        start,
        end,
        summary="Alias",
        organizer={"email": "aqeel@example.com"},
    )
    other = gcal_event(
        "other",
        start,
        end,
        summary="Not mine",
        organizer={"email": "sam@example.com"},
    )
    client = FakeCalendar([{"items": [owned, other]}])
    client.account = "Aqeel@Example.com"
    store = StateStore(cfg.state_db)

    def joiner(row):
        return FlowResult(opened=True, joined=True, left=True, notes_verified=True, outcome="left")

    started = sync_and_join(store, client, joiner, cfg, now, join=True)
    assert started == ["owned"]
    assert store.get("owned").status != "skipped_not_organizer"
    skipped = store.get("other")
    assert skipped.outcome == "skipped"
    assert skipped.reason == "not organizer (organizer: sam@example.com)"
    store.close()


def test_self_true_wins_and_the_filter_can_be_turned_off(tmp_path):
    now, start, end = due_bounds()
    odd = gcal_event(
        "odd",
        start,
        end,
        organizer={"email": "alias@example.com", "self": True},
    )
    cfg = make_cfg(tmp_path)
    store = StateStore(cfg.state_db)

    def joiner(row):
        return FlowResult(opened=True, joined=True, left=True, notes_verified=True, outcome="left")

    assert sync_and_join(store, FakeCalendar([{"items": [odd]}]), joiner, cfg, now, join=True) == ["odd"]
    store.close()

    cfg_off = make_cfg(tmp_path / "off", only_organizer_meetings=False)
    (tmp_path / "off").mkdir()
    guest = gcal_event(
        "guest",
        start,
        end,
        organizer={"email": "sam@example.com", "self": False},
    )
    store_off = StateStore(cfg_off.state_db)
    assert sync_and_join(
        store_off,
        FakeCalendar([{"items": [guest]}]),
        joiner,
        cfg_off,
        now,
        join=True,
    ) == ["guest"]
    store_off.close()
