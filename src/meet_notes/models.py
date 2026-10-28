"""Shared records. Status strings are the run history the CLI prints."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


CLAIMABLE_STATUSES = frozenset(
    {
        "scheduled",
        "failed_before_join",
        "auth_required",
        "stopped",
    }
)

IN_PROGRESS_STATUSES = frozenset(
    {
        "joining",
        "in_call",
        "waiting",
        "starting_notes",
        "staying",
        "leaving",
    }
)

# Outcomes that mean the Meet page was used successfully enough to stop.
SUCCESS_OUTCOMES = frozenset({"left", "left_alone", "left_after_end"})

ABSENCE_WATCH_STATUSES = frozenset(
    {
        "scheduled",
        "missed",
        "failed_before_join",
        "auth_required",
        "stopped",
        "skipped_declined",
        "skipped_no_meet",
        "skipped_all_day",
        "skipped_not_organizer",
    }
)


@dataclass
class ParsedEvent:
    instance_key: str
    event_id: str
    recurring_event_id: str | None
    ical_uid: str | None
    summary: str
    meet_url: str | None
    start_utc: datetime | None
    end_utc: datetime | None
    cancelled: bool
    declined: bool
    all_day: bool
    organizer_self: bool | None = None
    organizer_email: str | None = None


@dataclass
class MeetingRow:
    instance_key: str
    calendar_id: str
    event_id: str
    recurring_event_id: str | None
    ical_uid: str | None
    summary: str
    meet_url: str | None
    start_utc: datetime
    end_utc: datetime
    status: str
    join_claimed_at: datetime | None
    meet_opened_at: datetime | None
    joined_at: datetime | None
    others_joined_at: datetime | None
    notes_started_at: datetime | None
    notes_verified_at: datetime | None
    notes_verified: bool | None
    notes_already_on: bool
    verification_signal: str | None
    left_at: datetime | None
    outcome: str | None
    error: str | None
    attempt_count: int
    absent_polls: int
    created_at: datetime
    updated_at: datetime
    reason: str | None = None


@dataclass
class MeetTarget:
    url: str
    instance_key: str
    summary: str
    start_utc: datetime
    end_utc: datetime


def final_status(result: FlowResult) -> str:
    """Map a visit onto the status stored for restart safety.

    auth_required, stopped, and failed stay retryable only when the Meet page
    never loaded. Anything after the page loads is final for the automatic loop.
    """
    if result.outcome == "auth_required":
        return "auth_required"
    if not result.opened and result.outcome == "stopped":
        return "stopped"
    if not result.opened and result.outcome == "failed":
        return "failed_before_join"
    return result.outcome


@dataclass
class FlowResult:
    """What one Meet visit did. `opened` is true only after a Meet page loaded."""

    opened: bool = False
    mic_off: bool = False
    camera_off: bool = False
    joined: bool = False
    others_seen: int = 0
    participant_signal: str | None = None
    notes_already_on: bool = False
    notes_clicked: bool = False
    consent_clicked: bool = False
    notes_verified: bool = False
    verification_signal: str | None = None
    left: bool = False
    outcome: str = "failed"
    error: str | None = None
    extra: dict = field(default_factory=dict)
