"""Text and JSON views for status and history."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from meet_notes.health import HealthReport
from meet_notes.models import MeetingRow


def format_status(report: HealthReport, *, as_json: bool) -> str:
    if as_json:
        payload = {
            "ready": report.ready,
            "problems": report.problems,
            "warnings": report.warnings,
            "details": report.details,
        }
        return json.dumps(payload, indent=2, default=str)
    lines = [
        "Meet Notes Auto-Joiner",
        f"Ready: {'yes' if report.ready else 'no'}",
        f"Calendar: {report.details.get('calendar_id')}",
        f"Timezone: {report.details.get('timezone')}",
        f"Stay mode: {report.details.get('stay_mode')}",
        f"Concurrent joins: {report.details.get('max_concurrent_joins')}",
        f"Wait for a participant: {'yes' if report.details.get('wait_for_participant') else 'no'}",
        f"Only meetings this account organizes: {'yes' if report.details.get('only_organizer_meetings') else 'no'}",
        f"Email: {'on' if report.details.get('notify_enabled') else 'off'}",
        f"Config: {report.details.get('config_path') or '(defaults)'}",
        f"Credentials: {report.details.get('credentials')}",
        f"Token: {report.details.get('token')}",
        f"Profile: {report.details.get('profile')}",
        f"Session cookie: {report.details.get('google_session_cookie')}",
        f"Database: {report.details.get('database')}",
        f"Last sync: {report.details.get('last_sync_at') or 'never'}",
    ]
    if report.problems:
        lines.append("Problems:")
        lines.extend(f"  - {item}" for item in report.problems)
    if report.warnings:
        lines.append("Warnings:")
        lines.extend(f"  - {item}" for item in report.warnings)
    return "\n".join(lines)


def format_history(rows: list[MeetingRow], tz: ZoneInfo, *, as_json: bool) -> str:
    if as_json:
        return json.dumps([_row_dict(row, tz) for row in rows], indent=2)
    if not rows:
        return "No meetings recorded yet."
    lines = []
    for row in rows:
        start = row.start_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")
        outcome, notes, reason = _explain(row)
        joined = _clock(row.joined_at, tz)
        notes_at = _clock(row.notes_started_at, tz)
        left = _clock(row.left_at, tz)
        lines.append(
            f"{start}  {outcome:<16}  notes={notes}  "
            f"joined={joined}  notes_at={notes_at}  left={left}  {row.summary}  [{row.instance_key}]"
        )
        if reason:
            lines.append(f"    {reason}")
    return "\n".join(lines)


def format_preview(rows: list[tuple[MeetingRow, str]], tz: ZoneInfo) -> str:
    if not rows:
        return "No meetings in the local history yet. If the calendar is empty, nothing will be joined."
    lines = ["Upcoming and recorded meetings:"]
    for row, window in rows:
        start = row.start_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")
        if row.status == "skipped_not_organizer" or row.outcome == "skipped":
            _outcome, _notes, reason = _explain(row)
            lines.append(f"{start}  skipped        {window:<12}  {row.summary}  [{row.instance_key}]")
            if reason:
                lines.append(f"    {reason}")
            continue
        lines.append(f"{start}  {window:<12}  {row.status:<18}  {row.summary}  [{row.instance_key}]")
    return "\n".join(lines)


def _row_dict(row: MeetingRow, tz: ZoneInfo) -> dict:
    def local(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.astimezone(tz).isoformat()

    outcome, notes, reason = _explain(row)
    return {
        "instance_key": row.instance_key,
        "summary": row.summary,
        "status": row.status,
        "outcome": row.outcome,
        "meet_url": row.meet_url,
        "start": local(row.start_utc),
        "end": local(row.end_utc),
        "joined_at": local(row.joined_at),
        "others_joined_at": local(row.others_joined_at),
        "notes_started_at": local(row.notes_started_at),
        "notes_verified_at": local(row.notes_verified_at),
        "notes_verified": row.notes_verified,
        "notes_already_on": row.notes_already_on,
        "verification_signal": row.verification_signal,
        "left_at": local(row.left_at),
        "error": row.error,
        "attempt_count": row.attempt_count,
        "log_outcome": outcome,
        "notes_started": notes,
        "reason": reason,
        "date": row.start_utc.astimezone(tz).strftime("%Y-%m-%d"),
        "scheduled_start": local(row.start_utc),
    }


def _explain(row: MeetingRow) -> tuple[str, str, str]:
    from meet_notes.meeting_log import explain_row

    return explain_row(row)


def _clock(value: datetime | None, tz: ZoneInfo) -> str:
    if value is None:
        return "-"
    return value.astimezone(tz).strftime("%H:%M")
