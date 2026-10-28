"""When a stored meeting should be joined. All comparisons are absolute instants."""

from __future__ import annotations

from datetime import datetime, timedelta

from meet_notes.config import AppConfig


def classify_window(
    start: datetime,
    end: datetime,
    now: datetime,
    cfg: AppConfig,
) -> str:
    """Return too_early, due, missed, or ended.

    due means now is inside [start - lead, start + grace] and the meeting has
    not ended. missed means the start grace has passed but the meeting is still
    on the calendar. ended means now is past the end.
    """
    lead = timedelta(minutes=cfg.join_lead_minutes)
    grace = timedelta(minutes=cfg.join_after_start_minutes)
    if now > end:
        return "ended"
    if now < start - lead:
        return "too_early"
    if now > start + grace:
        return "missed"
    return "due"


def poll_window(now: datetime, cfg: AppConfig) -> tuple[datetime, datetime]:
    start = now - timedelta(minutes=cfg.join_after_start_minutes + 30)
    end = now + timedelta(hours=cfg.lookahead_hours)
    return start, end
