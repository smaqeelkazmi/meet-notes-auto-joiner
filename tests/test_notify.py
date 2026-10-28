from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

import pytest

from tests.conftest import due_bounds, gcal_event, make_cfg

from meet_notes.calendar_sync import parse_event
from meet_notes.errors import ConfigError
from meet_notes.meeting_log import publish_attempt
from meet_notes.models import FlowResult
from meet_notes.notify import (
    SIGNED_OUT_KEY,
    load_smtp_password,
    notify_attempt,
    notify_calendar_auth,
    notify_hard_failure,
    send_email,
    send_test_email,
)
from meet_notes.state import StateStore


class FakeSMTP:
    sent: list[EmailMessage] = []
    init_args: list[tuple] = []

    def __init__(self, host, port, timeout=30, context=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.password = None
        FakeSMTP.init_args.append((host, port))

    def ehlo(self):
        return None

    def starttls(self, context=None):
        return None

    def login(self, user, password):
        self.password = password

    def send_message(self, message):
        FakeSMTP.sent.append(message)

    def quit(self):
        return None


def _notify_cfg(tmp_path, **extra):
    values = dict(
        notify_enabled=True,
        notify_to="aqeel@example.com",
        notify_from="notes@example.com",
        notify_smtp_host="smtp.example.com",
        notify_smtp_port=465,
        notify_smtp_ssl=True,
        notify_smtp_username="notes@example.com",
    )
    values.update(extra)
    return make_cfg(tmp_path, **values)


def _finished_row(cfg, store, now, *, outcome="left", error=None, verified=True):
    start = now
    end = now
    from datetime import timedelta

    event = parse_event(
        gcal_event("evt-1", start, start + timedelta(minutes=30), summary="Design review"),
        cfg.tzinfo,
    )
    assert event is not None
    store.insert_scheduled(event, "primary", now)
    store.claim_join("evt-1", now, 3)
    store.save_flow_result(
        "evt-1",
        FlowResult(
            opened=True,
            joined=True,
            left=True,
            notes_verified=verified,
            notes_clicked=verified,
            outcome=outcome,
            error=error,
        ),
        now,
    )
    return store.get("evt-1")


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.sent = []
    FakeSMTP.init_args = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setenv("MEET_NOTES_SMTP_PASSWORD", "s3cret-password")
    return FakeSMTP


def test_disabled_notify_does_not_send(tmp_path, smtp):
    cfg = _notify_cfg(tmp_path, notify_enabled=False)
    now, _start, _end = due_bounds()
    store = StateStore(cfg.state_db)
    row = _finished_row(cfg, store, now)
    notify_attempt(cfg, store, row, outcome="notes_started", reason="ok")
    assert smtp.sent == []
    store.close()


def test_attempt_email_includes_title_times_and_failure_reason(tmp_path, smtp):
    cfg = _notify_cfg(tmp_path)
    now, _start, _end = due_bounds()
    store = StateStore(cfg.state_db)
    row = _finished_row(
        cfg,
        store,
        now,
        outcome="notes_unavailable",
        error="The notes control was missing. The bot is leaving the call.",
        verified=False,
    )
    notify_attempt(cfg, store, row, outcome="notes_failed", reason=row.error)
    assert len(smtp.sent) == 1
    message = smtp.sent[0]
    body = message.get_content()
    assert "Design review" in body
    assert "Notes started: no" in body
    assert "notes_failed" in body
    assert "leaving the call" in body
    assert "Scheduled start:" in body
    assert message["To"] == "aqeel@example.com"
    store.close()


def test_signed_out_hard_failure_is_deduped(tmp_path, smtp):
    cfg = _notify_cfg(tmp_path)
    store = StateStore(cfg.state_db)
    notify_hard_failure(cfg, store, SIGNED_OUT_KEY, "signed out", "Run meet-notes login.")
    notify_hard_failure(cfg, store, SIGNED_OUT_KEY, "signed out", "Run meet-notes login.")
    assert len(smtp.sent) == 1
    store.close()


def test_calendar_auth_email_is_deduped_and_ignores_other_errors(tmp_path, smtp):
    cfg = _notify_cfg(tmp_path)
    store = StateStore(cfg.state_db)
    notify_calendar_auth(cfg, store, "connection timed out")
    assert smtp.sent == []
    notify_calendar_auth(cfg, store, "invalid_grant: token expired")
    notify_calendar_auth(cfg, store, "invalid_grant: token expired")
    assert len(smtp.sent) == 1
    assert "calendar sign-in" in smtp.sent[0]["Subject"]
    store.close()


def test_smtp_failure_does_not_escape_publish(tmp_path, monkeypatch):
    cfg = _notify_cfg(tmp_path)
    monkeypatch.setenv("MEET_NOTES_SMTP_PASSWORD", "s3cret-password")

    class Boom:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("smtp down s3cret-password")

    monkeypatch.setattr(smtplib, "SMTP_SSL", Boom)
    now, _start, _end = due_bounds()
    store = StateStore(cfg.state_db)
    _finished_row(cfg, store, now)
    publish_attempt(cfg, store, "evt-1", now, email=True)
    text = (tmp_path / "meeting-log.csv").read_text(encoding="utf-8")
    assert "notes_started" in text
    store.close()


def test_password_is_redacted_and_not_read_from_yaml(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    monkeypatch.setenv("MEET_NOTES_SMTP_PASSWORD", "s3cret-password")

    class Boom:
        def __init__(self, *args, **kwargs):
            pass

        def ehlo(self):
            return None

        def login(self, user, password):
            raise smtplib.SMTPException(f"rejected {password}")

        def quit(self):
            return None

    monkeypatch.setattr(smtplib, "SMTP_SSL", Boom)
    cfg = _notify_cfg(tmp_path)
    with pytest.raises(RuntimeError) as caught:
        send_email(cfg, "subject", "body")
    assert "s3cret-password" not in str(caught.value)
    assert "s3cret-password" not in caplog.text

    path = tmp_path / "config.yaml"
    path.write_text(
        "notify:\n  enabled: false\n  smtp_password: nope\n",
        encoding="utf-8",
    )
    from meet_notes.config import load_config

    with pytest.raises(ConfigError, match="smtp_password"):
        load_config(path, environ={}, cwd=tmp_path)


def test_password_file_must_be_owner_only(tmp_path, monkeypatch):
    monkeypatch.delenv("MEET_NOTES_SMTP_PASSWORD", raising=False)
    secret = tmp_path / "smtp_password"
    secret.write_text("file-secret\n", encoding="utf-8")
    os.chmod(secret, 0o644)
    env = {"MEET_NOTES_SMTP_PASSWORD_FILE": str(secret)}
    assert load_smtp_password(env) is None
    os.chmod(secret, 0o600)
    assert load_smtp_password(env) == "file-secret"
    env["MEET_NOTES_SMTP_PASSWORD"] = "from-env"
    assert load_smtp_password(env) == "from-env"


def test_starttls_when_ssl_is_off(tmp_path, smtp):
    cfg = _notify_cfg(tmp_path, notify_smtp_ssl=False, notify_smtp_port=587)
    send_email(cfg, "hello", "body")
    assert len(smtp.sent) == 1
    assert smtp.init_args == [("smtp.example.com", 587)]


def test_send_test_email_command(tmp_path, smtp, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(
        "\n".join(
            [
                "timezone: Asia/Karachi",
                "state_db: state.db",
                "log_dir: logs",
                "profile_dir: profile",
                "credentials_file: credentials.json",
                "token_file: token.json",
                "notify:",
                "  enabled: false",
                "  to: aqeel@example.com",
                "  from: notes@example.com",
                "  smtp_host: smtp.example.com",
                "  smtp_port: 465",
                "  smtp_ssl: true",
                "  smtp_username: notes@example.com",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    from meet_notes.cli import main

    assert main(["--config", str(path), "test-email"]) == 0
    assert smtp.sent[0]["Subject"] == "Meet notes: test email"
    assert send_test_email(make_cfg(tmp_path, notify_to=""))[0] is False
