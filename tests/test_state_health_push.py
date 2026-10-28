from __future__ import annotations

import json
import logging
import threading
from datetime import timedelta

from tests.conftest import due_bounds, make_cfg

from meet_notes.calendar_sync import parse_event
from meet_notes.google_calendar import CALENDAR_SCOPES, _http_status
from meet_notes.health import inspect_health
from meet_notes.locking import SingleInstance
from meet_notes.logging_setup import configure_logging, log_event
from meet_notes.models import FlowResult, final_status
from meet_notes.present import format_history, format_status
from meet_notes.push import push_decision, should_renew
from meet_notes.state import StateStore
from meet_notes.timeutil import utc_now


def test_claim_release_and_restart_recovery(tmp_path):
    cfg = make_cfg(tmp_path)
    now, start, end = due_bounds()
    store = StateStore(cfg.state_db)
    event = parse_event(_event("evt-1", start, end), cfg.tzinfo)
    assert event is not None
    store.insert_scheduled(event, cfg.calendar_id, now)
    assert store.claim_join("evt-1", now, max_attempts=3) is True
    assert store.claim_join("evt-1", now, max_attempts=3) is False
    assert store.release_claim("evt-1", now) is True
    assert store.get("evt-1").attempt_count == 0  # type: ignore[union-attr]
    assert store.claim_join("evt-1", now, max_attempts=3) is True
    store.mark("evt-1", meet_opened_at=now, status="in_call", updated_at=now)
    recovered = dict(store.recover_abandoned(now))
    assert recovered["evt-1"] == "interrupted"
    assert store.claim_join("evt-1", now, max_attempts=3) is False
    assert store.requeue("missing", now) is False
    store.close()


def test_requeue_allows_another_manual_join(tmp_path):
    cfg = make_cfg(tmp_path)
    now, start, end = due_bounds()
    store = StateStore(cfg.state_db)
    event = parse_event(_event("evt-1", start, end), cfg.tzinfo)
    assert event is not None
    store.insert_scheduled(event, "primary", now)
    store.claim_join("evt-1", now, 3)
    store.save_flow_result(
        "evt-1",
        FlowResult(opened=True, joined=True, left=True, notes_verified=True, outcome="left"),
        now,
    )
    assert store.claim_join("evt-1", now, 3) is False
    assert store.requeue("evt-1", now) is True
    assert store.claim_join("evt-1", now, 3) is True
    store.close()


def test_claim_is_exclusive_across_connections(tmp_path):
    cfg = make_cfg(tmp_path)
    now, start, end = due_bounds()
    store = StateStore(cfg.state_db)
    event = parse_event(_event("evt-1", start, end), cfg.tzinfo)
    assert event is not None
    store.insert_scheduled(event, "primary", now)
    store.close()
    results: list[bool] = []

    def worker():
        local = StateStore(cfg.state_db)
        results.append(local.claim_join("evt-1", now, 3))
        local.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [False, True]


def test_final_status_mapping():
    assert final_status(FlowResult(outcome="failed", opened=False)) == "failed_before_join"
    assert final_status(FlowResult(outcome="failed", opened=True)) == "failed"
    assert final_status(FlowResult(outcome="auth_required")) == "auth_required"
    assert final_status(FlowResult(outcome="left", opened=True)) == "left"


def test_health_ready_and_missing_token(tmp_path):
    cfg = make_cfg(tmp_path)
    report = inspect_health(cfg)
    assert report.ready is False
    assert any("token" in item.lower() or "Token" in item or "Calendar token" in item for item in report.problems)

    cfg.credentials_file.write_text(json.dumps({"installed": {"client_id": "example"}}), encoding="utf-8")
    cfg.token_file.write_text(json.dumps({"refresh_token": "refresh", "scopes": [CALENDAR_SCOPES[0]]}), encoding="utf-8")
    cfg.profile_dir.mkdir()
    (cfg.profile_dir / "Local State").write_text("{}", encoding="utf-8")
    report = inspect_health(cfg)
    assert report.ready is True
    text = format_status(report, as_json=False)
    assert "Ready: yes" in text
    assert "Concurrent joins: 4" in text
    payload = json.loads(format_status(report, as_json=True))
    assert payload["ready"] is True


def test_history_format(tmp_path):
    cfg = make_cfg(tmp_path)
    now, start, end = due_bounds()
    store = StateStore(cfg.state_db)
    event = parse_event(_event("evt-1", start, end, summary="Design review"), cfg.tzinfo)
    assert event is not None
    store.insert_scheduled(event, "primary", now)
    store.save_flow_result(
        "evt-1",
        FlowResult(
            opened=True,
            joined=True,
            left=True,
            notes_verified=True,
            notes_clicked=True,
            outcome="left",
            verification_signal="stop-button",
        ),
        now,
    )
    text = format_history(store.history(10), cfg.tzinfo, as_json=False)
    assert "Design review" in text
    assert "notes=yes" in text
    store.close()


def test_push_decision_and_renew():
    headers = {
        "X-Goog-Channel-Token": "secret",
        "X-Goog-Resource-State": "exists",
        "X-Goog-Channel-ID": "abc",
    }
    assert push_decision(headers, "secret", "abc") == (200, True)
    assert push_decision({**headers, "X-Goog-Resource-State": "sync"}, "secret", "abc") == (200, False)
    assert push_decision(headers, "nope", "abc") == (403, False)
    assert push_decision({**headers, "X-Goog-Channel-ID": "other"}, "secret", "abc") == (200, False)
    now = utc_now()
    assert should_renew(None, now, 60) is True
    soon = int((now + timedelta(minutes=10)).timestamp() * 1000)
    later = int((now + timedelta(days=2)).timestamp() * 1000)
    assert should_renew(soon, now, 60) is True
    assert should_renew(later, now, 60) is False


def test_scope_is_calendar_readonly():
    assert CALENDAR_SCOPES == ("https://www.googleapis.com/auth/calendar.readonly",)


def test_http_410_maps():
    class FakeError(Exception):
        def __init__(self):
            self.resp = type("Resp", (), {"status": 410})()

    assert _http_status(FakeError()) == 410


def test_json_log_and_lock(tmp_path):
    configure_logging(tmp_path, "INFO", also_stderr=False)
    log_event(logging.getLogger("meet_notes.test"), logging.INFO, "calendar sync finished", inserted=1)
    lines = (tmp_path / "meet-notes.jsonl").read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[-1])
    assert payload["message"] == "calendar sync finished"
    assert payload["inserted"] == 1
    first = SingleInstance(tmp_path / "app.lock")
    second = SingleInstance(tmp_path / "app.lock")
    first.acquire()
    try:
        from meet_notes.errors import AlreadyRunning

        try:
            second.acquire()
            raised = False
        except AlreadyRunning:
            raised = True
        assert raised is True
    finally:
        first.release()


def test_cli_parser_and_requeue_guard(tmp_path, monkeypatch):
    from meet_notes.cli import main

    assert main(["version"]) == 0
    monkeypatch.chdir(tmp_path)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "timezone: Asia/Karachi\nstate_db: state.db\nlog_dir: logs\n"
        "credentials_file: credentials.json\ntoken_file: token.json\nprofile_dir: profile\n",
        encoding="utf-8",
    )
    assert main(["--config", str(cfg_path), "requeue", "evt-1"]) == 2


def _event(event_id, start, end, summary="Design review"):
    return {
        "id": event_id,
        "status": "confirmed",
        "summary": summary,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
        "hangoutLink": "https://meet.google.com/abc-defg-hij",
    }
