from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from meet_notes.config import AppConfig
from meet_notes.meet_flow import FlowOptions

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
UTC = timezone.utc


def make_cfg(tmp_path: Path, **kwargs) -> AppConfig:
    values = dict(
        timezone="Asia/Karachi",
        profile_dir=tmp_path / "profile",
        credentials_file=tmp_path / "credentials.json",
        token_file=tmp_path / "token.json",
        state_db=tmp_path / "state.db",
        log_dir=tmp_path / "logs",
        join_lead_minutes=3,
        join_after_start_minutes=10,
        lookahead_hours=48,
        missing_polls_before_cancel=2,
        max_prejoin_attempts=3,
        poll_interval_seconds=30,
        nobody_joined_timeout_minutes=10,
        admit_timeout_minutes=5,
    )
    values.update(kwargs)
    cfg = AppConfig(**values)
    cfg.validate()
    return cfg


def flow_options(**kwargs) -> FlowOptions:
    values = dict(
        allowed_hosts=frozenset({"127.0.0.1", "meet.google.com", "localhost"}),
        self_display_names=("You", "Aqeel"),
        stay_mode="leave_after_notes",
        leave_even_if_unverified=True,
        stay_even_if_unverified=False,
        nobody_joined_timeout_seconds=4,
        admit_timeout_seconds=4,
        participant_poll_seconds=0.05,
        alone_confirm_seconds=0.6,
        notes_ui_timeout_seconds=4,
        stay_grace_seconds=0,
        navigation_timeout_ms=5000,
    )
    values.update(kwargs)
    return FlowOptions(**values)


def gcal_event(event_id: str, start: datetime, end: datetime, **extra) -> dict:
    status = extra.pop("status", "confirmed")
    summary = extra.pop("summary", "Design review")
    with_meet = extra.pop("with_meet", True)
    item = {
        "id": event_id,
        "status": status,
        "summary": summary,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
        "iCalUID": f"{event_id}@google.com",
    }
    hangout = extra.pop("hangoutLink", None)
    organizer = extra.pop("organizer", {"email": "aqeel@example.com", "self": True})
    if hangout is not None:
        item["hangoutLink"] = hangout
    elif with_meet:
        item["hangoutLink"] = "https://meet.google.com/abc-defg-hij"
    item["organizer"] = organizer
    item.update(extra)
    return item


class FakeCalendar:
    def __init__(self, pages: list[dict]) -> None:
        self.pages = list(pages)
        self.calls: list[dict] = []
        self.account: str | None = None

    def account_email(self) -> str | None:
        return self.account

    def list_events(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("sync_token") == "expired":
            from meet_notes.calendar_sync import SyncTokenExpired

            raise SyncTokenExpired("expired")
        if not self.pages:
            return {"items": []}
        return self.pages.pop(0)


@pytest.fixture(scope="module")
def meet_server():
    class Quiet(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(FIXTURE_DIR), **kwargs)

        def log_message(self, fmt, *args):
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        launched = playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            yield launched
        finally:
            launched.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    tab = context.new_page()
    tab.set_default_timeout(3000)
    try:
        yield tab
    finally:
        context.close()


def meet_url(meet_server, scenario: str) -> str:
    port = meet_server.server_address[1]
    return f"http://127.0.0.1:{port}/meet_page.html?scenario={scenario}"


def due_bounds(now: datetime | None = None) -> tuple[datetime, datetime, datetime]:
    current = now or datetime(2026, 10, 28, 4, 0, tzinfo=UTC)
    start = current - timedelta(minutes=1)
    end = current + timedelta(minutes=30)
    return current, start, end
