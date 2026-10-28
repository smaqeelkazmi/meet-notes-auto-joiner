from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from meet_notes.config import AppConfig
from meet_notes.scheduler import classify_window

PKT = ZoneInfo("Asia/Karachi")


def test_karachi_lead_window():
    # 09:00 in Karachi is 04:00 UTC. Pakistan has no daylight saving.
    start = datetime(2026, 10, 28, 9, 0, tzinfo=PKT)
    end = start + timedelta(minutes=30)
    cfg = AppConfig(timezone="Asia/Karachi", join_lead_minutes=3, join_after_start_minutes=10)
    start_utc = start.astimezone(ZoneInfo("UTC"))
    end_utc = end.astimezone(ZoneInfo("UTC"))
    assert start_utc.hour == 4
    assert classify_window(start_utc, end_utc, start_utc - timedelta(minutes=4), cfg) == "too_early"
    assert classify_window(start_utc, end_utc, start_utc - timedelta(minutes=3), cfg) == "due"
    assert classify_window(start_utc, end_utc, start_utc + timedelta(minutes=5), cfg) == "due"
    assert classify_window(start_utc, end_utc, start_utc + timedelta(minutes=11), cfg) == "missed"
    assert classify_window(start_utc, end_utc, end_utc + timedelta(minutes=1), cfg) == "ended"
