"""Glue between a Calendar row and one Meet visit. No browser import here."""

from __future__ import annotations

from datetime import datetime

from meet_notes.models import FlowResult, MeetTarget, MeetingRow
from meet_notes.state import StateStore


def target_from_row(row: MeetingRow) -> MeetTarget:
    if not row.meet_url:
        raise ValueError(f"{row.instance_key} has no Meet link")
    return MeetTarget(
        url=row.meet_url,
        instance_key=row.instance_key,
        summary=row.summary,
        start_utc=row.start_utc,
        end_utc=row.end_utc,
    )


def finalize_result(store: StateStore, instance_key: str, result: FlowResult, now: datetime) -> None:
    if result.outcome == "stopped" and not result.opened:
        if store.release_claim(instance_key, now):
            return
    store.save_flow_result(instance_key, result, now)
