"""One email per finished meeting attempt, plus deduped hard-failure mail.

SMTP uses the standard library. The password is never read from YAML and
never written to the log. A send failure is a warning. It does not raise
into the joiner.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from meet_notes.config import AppConfig, default_config_dir
from meet_notes.logging_setup import log_event
from meet_notes.models import MeetingRow

logger = logging.getLogger("meet_notes.notify")

SIGNED_OUT_KEY = "hard:signed_out"
CALENDAR_AUTH_KEY = "hard:calendar_auth"


def smtp_password_path(environ: dict | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get("MEET_NOTES_SMTP_PASSWORD_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return default_config_dir() / "smtp_password"


def load_smtp_password(environ: dict | None = None) -> str | None:
    """Return the SMTP password, or None when it is not configured.

    The environment variable wins. The file is used only when its mode is
    owner-read (0600 or 0400) on POSIX. A world-readable file is ignored.
    """
    env = os.environ if environ is None else environ
    raw = env.get("MEET_NOTES_SMTP_PASSWORD", "")
    if raw.strip():
        return raw.strip()
    path = smtp_password_path(env)
    if not path.is_file():
        return None
    if os.name != "nt":
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            log_event(
                logger,
                logging.WARNING,
                "smtp password file is readable by others and will not be used",
                path=str(path),
            )
            return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def notify_attempt(cfg: AppConfig, store, row: MeetingRow, *, outcome: str, reason: str) -> None:
    if not cfg.notify_enabled:
        return
    try:
        subject = f"Meet notes: {outcome.replace('_', ' ')} — {row.summary}"
        body = _attempt_body(cfg, row, outcome=outcome, reason=reason)
        send_email(cfg, subject, body)
        if outcome == "signed_out":
            notify_hard_failure(
                cfg,
                store,
                SIGNED_OUT_KEY,
                "Meet notes: browser profile is signed out",
                reason,
            )
        elif outcome == "notes_started":
            store.forget_notification(SIGNED_OUT_KEY)
    except Exception as exc:
        _warn(exc)


def notify_hard_failure(cfg: AppConfig, store, key: str, subject: str, body: str) -> None:
    if not cfg.notify_enabled:
        return
    try:
        if not store.claim_notification(key):
            log_event(logger, logging.INFO, "hard-failure email already sent", key=key)
            return
        try:
            send_email(cfg, subject, body)
        except Exception:
            store.forget_notification(key)
            raise
    except Exception as exc:
        _warn(exc)


def notify_calendar_auth(cfg: AppConfig, store, error: str) -> None:
    if not _looks_like_auth_failure(error):
        return
    notify_hard_failure(
        cfg,
        store,
        CALENDAR_AUTH_KEY,
        "Meet notes: calendar sign-in failed",
        "The calendar poll could not authenticate. Run meet-notes auth again.\n\n" + error[:500],
    )


def send_test_email(cfg: AppConfig) -> tuple[bool, str]:
    """Send one test message. Returns (ok, message for the operator)."""
    problem = _config_problem(cfg, require_enabled=False)
    if problem:
        return False, problem
    try:
        send_email(
            cfg,
            "Meet notes: test email",
            "This is a test from Meet Notes Auto-Joiner. SMTP is configured.",
        )
    except Exception as exc:
        return False, f"Test email failed: {_public_error(exc)}"
    return True, f"Test email sent to {cfg.notify_to}"


def send_email(cfg: AppConfig, subject: str, body: str, *, environ: dict | None = None) -> None:
    problem = _config_problem(cfg, require_enabled=False)
    if problem:
        raise RuntimeError(problem)
    password = load_smtp_password(environ)
    if not password:
        raise RuntimeError(
            "SMTP password is missing. Set MEET_NOTES_SMTP_PASSWORD or create "
            f"{smtp_password_path(environ)} with mode 600."
        )
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = cfg.notify_from
    message["To"] = cfg.notify_to
    message.set_content(body)
    context = ssl.create_default_context()
    if cfg.notify_smtp_ssl:
        client = smtplib.SMTP_SSL(cfg.notify_smtp_host, cfg.notify_smtp_port, timeout=30, context=context)
    else:
        client = smtplib.SMTP(cfg.notify_smtp_host, cfg.notify_smtp_port, timeout=30)
    try:
        client.ehlo()
        if not cfg.notify_smtp_ssl:
            client.starttls(context=context)
            client.ehlo()
        if cfg.notify_smtp_username:
            client.login(cfg.notify_smtp_username, password)
        client.send_message(message)
    except Exception as exc:
        raise RuntimeError(_public_error(exc, password)) from None
    finally:
        try:
            client.quit()
        except Exception:
            pass
    log_event(logger, logging.INFO, "email sent", to=cfg.notify_to, subject=subject)


def _attempt_body(cfg: AppConfig, row: MeetingRow, *, outcome: str, reason: str) -> str:
    tz = cfg.tzinfo

    def clock(value) -> str:
        if value is None:
            return "-"
        return value.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")

    notes = "yes" if row.notes_verified or row.notes_already_on else "no"
    lines = [
        "Meet Notes Auto-Joiner",
        "",
        f"Meeting: {row.summary}",
        f"Scheduled start: {clock(row.start_utc)}",
        f"Joined: {clock(row.joined_at)}",
        f"Notes started: {notes}",
        f"Notes started at: {clock(row.notes_started_at)}",
        f"Left: {clock(row.left_at)}",
        f"Outcome: {outcome}",
        f"Reason: {reason}",
    ]
    return "\n".join(lines)


def _config_problem(cfg: AppConfig, *, require_enabled: bool) -> str | None:
    if require_enabled and not cfg.notify_enabled:
        return "Email is disabled. Set notify.enabled to true."
    if not cfg.notify_to or "@" not in cfg.notify_to:
        return "notify.to must be an email address."
    if not cfg.notify_from or "@" not in cfg.notify_from:
        return "notify.from must be an email address."
    if not cfg.notify_smtp_host:
        return "notify.smtp_host is empty."
    if cfg.notify_smtp_port < 1 or cfg.notify_smtp_port > 65535:
        return "notify.smtp_port is not a valid TCP port."
    return None


def _looks_like_auth_failure(error: str) -> bool:
    text = (error or "").casefold()
    tokens = ("invalid_grant", "unauthorized", "invalid credentials", "login required", "auth", "401", "403")
    return any(token in text for token in tokens)


def _public_error(exc: BaseException, password: str | None = None) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    if password and password in text:
        text = text.replace(password, "[redacted]")
    return text[:500]


def _warn(exc: BaseException) -> None:
    log_event(logger, logging.WARNING, "email notification failed", error=_public_error(exc))
