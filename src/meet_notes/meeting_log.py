"""Append-only CSV of meeting attempts, next to the state database.

SQLite remains the restart-safety record. This file is the human-readable
history: one row per attempt, never rewritten.
"""

from __future__ import annotations

import csv
import logging
import threading
from datetime import datetime
from pathlib import Path

from meet_notes.config import AppConfig
from meet_notes.logging_setup import log_event
from meet_notes.models import MeetingRow
from meet_notes.state import StateStore

logger = logging.getLogger("meet_notes.meeting_log")

CSV_COLUMNS = (
    "date",
    "title",
    "scheduled_start",
    "joined_at",
    "notes_started",
    "notes_started_at",
    "left_at",
    "outcome",
    "reason",
)

_file_lock = threading.Lock()


def explain_row(row: MeetingRow) -> tuple[str, str, str]:
    """Return (public outcome, notes yes/no, plain-English reason)."""
    notes = "yes" if row.notes_verified or row.notes_already_on else "no"
    outcome = _public_outcome(row)
    if row.reason:
        return outcome, notes, row.reason
    return outcome, notes, _reason(row, outcome)


def publish_attempt(
    cfg: AppConfig,
    store: StateStore,
    instance_key: str,
    now: datetime,
    *,
    email: bool,
) -> None:
    """Record one attempt. Failures here are logged and swallowed."""
    try:
        row = store.get(instance_key)
        if row is None:
            return
        outcome, notes, reason = explain_row(row)
        if row.reason != reason:
            store.mark(instance_key, reason=reason, updated_at=now)
            row = store.get(instance_key)
            if row is None:
                return
        if not store.claim_publication(instance_key, row.attempt_count, outcome):
            return
        _append_csv(cfg, row, now, outcome=outcome, notes=notes, reason=reason)
        if email:
            from meet_notes.notify import notify_attempt

            notify_attempt(cfg, store, row, outcome=outcome, reason=reason)
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "meeting log update failed",
            instance_key=instance_key,
            error=str(exc)[:500],
        )


def _public_outcome(row: MeetingRow) -> str:
    raw = (row.outcome or row.status or "").strip()
    if raw in {"auth_required"} or row.status == "auth_required":
        return "signed_out"
    if raw == "blocked_by_host":
        return "blocked_by_host"
    if raw in {"join_failed", "failed", "failed_before_join"} or row.status in {
        "failed",
        "failed_before_join",
    }:
        return "join_failed"
    if raw in {"notes_unavailable", "left_unverified"}:
        return "notes_failed"
    if raw in {
        "missed",
        "cancelled",
        "skipped",
        "skipped_all_day",
        "skipped_declined",
        "skipped_no_meet",
        "skipped_not_organizer",
        "stopped",
    }:
        return "skipped"
    if row.status in {
        "missed",
        "cancelled",
        "skipped_all_day",
        "skipped_declined",
        "skipped_no_meet",
        "skipped_not_organizer",
        "stopped",
    }:
        return "skipped"
    if row.notes_verified or row.notes_already_on:
        return "notes_started"
    if raw in {"left", "left_alone", "left_after_end"}:
        return "notes_failed"
    if raw in {"nobody_joined"}:
        return "skipped"
    return "join_failed"


def _reason(row: MeetingRow, outcome: str) -> str:
    if row.error:
        return row.error
    if outcome == "notes_started" and row.notes_already_on:
        return "Notes were already on when the bot joined. It confirmed them and left."
    if outcome == "notes_started":
        return "The bot started Gemini notes, confirmed they were active, and left."
    if outcome == "notes_failed":
        return (
            "Notes did not start. The Gemini control was missing or never became active. "
            "The organizer may lack the license, the host may have disabled notes, "
            "or the Meet button label changed. The bot left the call."
        )
    if outcome == "signed_out":
        return "The browser profile is signed out. Run meet-notes login. The bot does not type a password."
    if outcome == "blocked_by_host":
        return "The host did not admit this account, so notes were not started."
    if outcome == "join_failed":
        return "The bot could not enter the call."
    if row.status == "cancelled" or row.outcome == "cancelled":
        return "The event was cancelled before the bot joined."
    if row.status == "missed" or row.outcome == "missed":
        return "The join window passed before the bot opened the meeting."
    if row.status == "skipped_declined":
        return "The event was declined, and skip_declined is on."
    if row.status == "skipped_all_day":
        return "All-day events are not joined."
    if row.status == "skipped_no_meet":
        return "The event no longer has a Meet link."
    if row.status == "skipped_not_organizer":
        email = "unknown"
        return f"not organizer (organizer: {email})"
    if row.outcome == "nobody_joined":
        return "No other participant joined before the timeout, so notes were not started."
    if row.status == "stopped" or row.outcome == "stopped":
        return "The process was stopped before the Meet page opened."
    return "The meeting was not joined."


def _log_path(cfg: AppConfig) -> Path | None:
    if cfg.meeting_log is not None:
        return cfg.meeting_log
    if cfg.state_db is not None:
        return cfg.state_db.parent / "meeting-log.csv"
    return None


def _append_csv(
    cfg: AppConfig,
    row: MeetingRow,
    now: datetime,
    *,
    outcome: str,
    notes: str,
    reason: str,
) -> None:
    path = _log_path(cfg)
    if path is None:
        return
    tz = cfg.tzinfo

    def stamp(value: datetime | None) -> str:
        if value is None:
            return ""
        return value.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")

    record = {
        "date": now.astimezone(tz).strftime("%Y-%m-%d"),
        "title": row.summary,
        "scheduled_start": stamp(row.start_utc),
        "joined_at": stamp(row.joined_at),
        "notes_started": notes,
        "notes_started_at": stamp(row.notes_started_at),
        "left_at": stamp(row.left_at),
        "outcome": outcome,
        "reason": reason,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock:
        new_file = not path.exists() or path.stat().st_size == 0
        with path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
            if new_file:
                writer.writeheader()
            writer.writerow(record)
    log_event(
        logger,
        logging.INFO,
        "meeting attempt logged",
        instance_key=row.instance_key,
        outcome=outcome,
        notes_started=notes,
        path=str(path),
    )
