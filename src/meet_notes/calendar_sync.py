"""Turn Calendar API payloads into scheduled rows.

Polling lists a lookahead window with showDeleted so cancellations come back
as status=cancelled. A time change keeps the same instance id and updates the
row when Meet has not been opened yet. If an event vanishes from the window
for `missing_polls_before_cancel` successful polls, it is marked cancelled.
One empty or partial glitch does not cancel it.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from meet_notes.config import AppConfig
from meet_notes.meet_links import extract_meet_url
from meet_notes.models import ABSENCE_WATCH_STATUSES, IN_PROGRESS_STATUSES, ParsedEvent
from meet_notes.scheduler import poll_window
from meet_notes.state import StateStore
from meet_notes.timeutil import parse_rfc3339, to_rfc3339


class SyncTokenExpired(Exception):
    """Reserved for a future incremental sync. Window polls do not use it."""


def parse_event(item: dict, default_tz: ZoneInfo) -> ParsedEvent | None:
    event_id = item.get("id")
    if not isinstance(event_id, str) or not event_id:
        return None
    cancelled = item.get("status") == "cancelled"
    start, all_day = _parse_bound(item.get("start"), default_tz)
    end, end_all_day = _parse_bound(item.get("end"), default_tz)
    if end_all_day:
        all_day = True
    summary = item.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        summary = "(no title)"
    recurring = item.get("recurringEventId")
    ical = item.get("iCalUID")
    organizer_self, organizer_email = _organizer(item)
    return ParsedEvent(
        instance_key=event_id,
        event_id=event_id,
        recurring_event_id=recurring if isinstance(recurring, str) else None,
        ical_uid=ical if isinstance(ical, str) else None,
        summary=summary.strip()[:300],
        meet_url=extract_meet_url(item),
        start_utc=start,
        end_utc=end,
        cancelled=cancelled,
        declined=_is_declined(item),
        all_day=all_day,
        organizer_self=organizer_self,
        organizer_email=organizer_email,
    )


def fetch_changes(
    client,
    *,
    calendar_id: str,
    time_min: datetime,
    time_max: datetime,
) -> list[dict]:
    """Read every page in the window. Raises if the client raises."""
    items: list[dict] = []
    page_token = None
    while True:
        response = client.list_events(
            calendar_id=calendar_id,
            sync_token=None,
            time_min=to_rfc3339(time_min),
            time_max=to_rfc3339(time_max),
            page_token=page_token,
        )
        batch = response.get("items") or []
        if not isinstance(batch, list):
            raise RuntimeError("Calendar response items were not a list")
        items.extend(item for item in batch if isinstance(item, dict))
        page_token = response.get("nextPageToken")
        if not page_token:
            return items


def sync_calendar(store: StateStore, client, cfg: AppConfig, now: datetime) -> dict[str, int]:
    window_start, window_end = poll_window(now, cfg)
    try:
        raw_items = fetch_changes(
            client,
            calendar_id=cfg.calendar_id,
            time_min=window_start,
            time_max=window_end,
        )
    except Exception as exc:
        previous = store.get_sync(cfg.calendar_id)
        token = previous["sync_token"] if previous else None
        store.set_sync(cfg.calendar_id, token, error=str(exc)[:2000], when=now)
        raise
    parsed = []
    for item in raw_items:
        event = parse_event(item, cfg.tzinfo)
        if event is not None:
            parsed.append(event)
    account_email = _lookup_account_email(client) if cfg.only_organizer_meetings else None
    stats = apply_events(store, parsed, cfg, now, account_email=account_email)
    stats.update(_note_absences(store, parsed, cfg, now, window_start, window_end))
    store.set_sync(cfg.calendar_id, None, error=None, when=now)
    return stats


def apply_events(
    store: StateStore,
    events: list[ParsedEvent],
    cfg: AppConfig,
    now: datetime,
    *,
    account_email: str | None = None,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for event in events:
        counts[apply_one(store, event, cfg, now, account_email=account_email)] += 1
    return dict(counts)


def organized_by_account(event: ParsedEvent, account_email: str | None) -> bool:
    """True when this calendar user is the organizer.

    `organizer.self` is the signal on the primary calendar. When Google omits
    `self`, the organizer email is compared with the signed-in account.
    """
    if event.organizer_self is True:
        return True
    if event.organizer_self is False:
        return False
    if account_email and event.organizer_email:
        return event.organizer_email.casefold() == account_email.casefold()
    return False


def not_organizer_reason(event: ParsedEvent) -> str:
    email = event.organizer_email or "unknown"
    return f"not organizer (organizer: {email})"


def apply_one(
    store: StateStore,
    event: ParsedEvent,
    cfg: AppConfig,
    now: datetime,
    *,
    account_email: str | None = None,
) -> str:
    existing = store.get(event.instance_key)
    if existing is not None and existing.status in IN_PROGRESS_STATUSES:
        # A visit is already running. Do not put the row back to scheduled,
        # or a second poll could claim the same instance again.
        fields: dict[str, object] = {"absent_polls": 0, "updated_at": now}
        if event.summary and existing.summary != event.summary:
            fields["summary"] = event.summary
        store.mark(event.instance_key, **fields)
        return "kept_in_progress"
    if existing is not None and existing.meet_opened_at is not None:
        if existing.summary != event.summary:
            store.mark(event.instance_key, summary=event.summary, absent_polls=0, updated_at=now)
        elif existing.absent_polls:
            store.mark(event.instance_key, absent_polls=0, updated_at=now)
        return "kept_after_join"

    if event.cancelled:
        if existing is None:
            return "ignored_cancelled"
        store.mark(
            event.instance_key,
            status="cancelled",
            outcome="cancelled",
            error=None,
            absent_polls=0,
            updated_at=now,
        )
        _publish_passive(cfg, store, event.instance_key, now)
        return "cancelled"

    if (
        cfg.only_organizer_meetings
        and event.meet_url
        and event.start_utc is not None
        and event.end_utc is not None
        and not organized_by_account(event, account_email)
    ):
        return _skip_not_organizer(store, event, cfg, now)

    if event.all_day and cfg.skip_all_day:
        if existing is not None:
            store.mark(
                event.instance_key,
                status="skipped_all_day",
                outcome="skipped_all_day",
                absent_polls=0,
                updated_at=now,
            )
            _publish_passive(cfg, store, event.instance_key, now)
        return "skip_all_day"

    if event.declined and cfg.skip_declined:
        if existing is not None:
            store.mark(
                event.instance_key,
                status="skipped_declined",
                outcome="skipped_declined",
                absent_polls=0,
                updated_at=now,
            )
            _publish_passive(cfg, store, event.instance_key, now)
        return "skipped_declined"

    if not event.meet_url:
        if existing is not None:
            store.mark(
                event.instance_key,
                status="skipped_no_meet",
                outcome="skipped_no_meet",
                meet_url=None,
                absent_polls=0,
                updated_at=now,
            )
            _publish_passive(cfg, store, event.instance_key, now)
            return "skipped_no_meet"
        return "ignored"

    if event.start_utc is None or event.end_utc is None or event.end_utc <= event.start_utc:
        return "ignored_bad_time"

    if existing is None:
        store.insert_scheduled(event, cfg.calendar_id, now)
        return "inserted"

    changed_time = existing.start_utc != event.start_utc or existing.end_utc != event.end_utc
    fields: dict[str, object] = {
        "summary": event.summary,
        "meet_url": event.meet_url,
        "start_utc": event.start_utc,
        "end_utc": event.end_utc,
        "recurring_event_id": event.recurring_event_id,
        "ical_uid": event.ical_uid,
        "absent_polls": 0,
        "updated_at": now,
    }
    # Time changes and restored events have not been opened, so they may be
    # joined once. A pre-join failure keeps its attempt count until the time
    # actually changes or the owner requeues it.
    restorable = {
        "cancelled",
        "missed",
        "skipped_no_meet",
        "skipped_declined",
        "skipped_all_day",
        "skipped_not_organizer",
    }
    if changed_time or existing.status in restorable:
        fields["status"] = "scheduled"
        fields["outcome"] = None
        fields["error"] = None
        fields["reason"] = None
        if changed_time:
            fields["attempt_count"] = 0
    store.mark(event.instance_key, **fields)
    if changed_time:
        return "rescheduled"
    if existing.status != "scheduled" and fields.get("status") == "scheduled":
        return "restored"
    return "updated"


def _note_absences(
    store: StateStore,
    events: list[ParsedEvent],
    cfg: AppConfig,
    now: datetime,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, int]:
    seen = {event.instance_key for event in events}
    missing_once = 0
    missing_cancelled = 0
    for row in store.list_meetings(sorted(ABSENCE_WATCH_STATUSES)):
        if row.meet_opened_at is not None:
            continue
        if row.start_utc < window_start or row.start_utc > window_end:
            continue
        if row.instance_key in seen:
            continue
        polls = row.absent_polls + 1
        if polls >= cfg.missing_polls_before_cancel:
            store.mark(
                row.instance_key,
                status="cancelled",
                outcome="cancelled",
                error="Event was missing from the calendar for consecutive polls",
                absent_polls=polls,
                updated_at=now,
            )
            _publish_passive(cfg, store, row.instance_key, now)
            missing_cancelled += 1
        else:
            store.mark(row.instance_key, absent_polls=polls, updated_at=now)
            missing_once += 1
    return {"missing_once": missing_once, "missing_cancelled": missing_cancelled}


def _skip_not_organizer(store: StateStore, event: ParsedEvent, cfg: AppConfig, now: datetime) -> str:
    reason = not_organizer_reason(event)
    existing = store.get(event.instance_key)
    if existing is None:
        store.insert_scheduled(event, cfg.calendar_id, now)
        existing = store.get(event.instance_key)
    if existing is None:
        return "skipped_not_organizer"
    if existing.meet_opened_at is not None or existing.status in IN_PROGRESS_STATUSES:
        return "kept_after_join"
    if existing.status != "skipped_not_organizer" or existing.reason != reason:
        store.mark(
            event.instance_key,
            status="skipped_not_organizer",
            outcome="skipped",
            reason=reason,
            error=None,
            summary=event.summary,
            meet_url=event.meet_url,
            start_utc=event.start_utc,
            end_utc=event.end_utc,
            absent_polls=0,
            updated_at=now,
        )
    _publish_passive(cfg, store, event.instance_key, now)
    return "skipped_not_organizer"


def _publish_passive(cfg: AppConfig, store: StateStore, instance_key: str, now: datetime) -> None:
    from meet_notes.meeting_log import publish_attempt

    publish_attempt(cfg, store, instance_key, now, email=False)


def _lookup_account_email(client) -> str | None:
    method = getattr(client, "account_email", None)
    if not callable(method):
        return None
    try:
        value = method()
    except Exception:
        return None
    if isinstance(value, str) and "@" in value:
        return value.strip()
    return None


def _organizer(item: dict) -> tuple[bool | None, str | None]:
    raw = item.get("organizer")
    if not isinstance(raw, dict):
        return None, None
    flag = raw.get("self")
    organizer_self = flag if isinstance(flag, bool) else None
    email = raw.get("email")
    organizer_email = email.strip() if isinstance(email, str) and email.strip() else None
    return organizer_self, organizer_email


def _is_declined(item: dict) -> bool:
    attendees = item.get("attendees") or []
    if not isinstance(attendees, list):
        return False
    for attendee in attendees:
        if not isinstance(attendee, dict):
            continue
        if attendee.get("self") and attendee.get("responseStatus") == "declined":
            return True
    return False


def _parse_bound(bound: object, default_tz: ZoneInfo) -> tuple[datetime | None, bool]:
    if not isinstance(bound, dict):
        return None, False
    if isinstance(bound.get("dateTime"), str):
        return parse_rfc3339(bound["dateTime"]), False
    if isinstance(bound.get("date"), str):
        tz_name = bound.get("timeZone")
        try:
            tz = ZoneInfo(tz_name) if isinstance(tz_name, str) and tz_name else default_tz
        except Exception:
            tz = default_tz
        local = datetime.fromisoformat(bound["date"]).replace(tzinfo=tz)
        from datetime import timezone

        return local.astimezone(timezone.utc), True
    return None, False
