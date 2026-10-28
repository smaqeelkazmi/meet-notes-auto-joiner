from __future__ import annotations

import threading
from datetime import timedelta

from tests.conftest import flow_options, meet_url

from meet_notes.meet_flow import run_meet_flow
from meet_notes.models import MeetTarget
from meet_notes.timeutil import utc_now


class RedirectPage:
    def __init__(self, landing: str) -> None:
        self.url = "about:blank"
        self.gotos: list[str] = []
        self.landing = landing

    def goto(self, url, **kwargs):
        self.gotos.append(url)
        self.url = self.landing


class BoomPage:
    def goto(self, url, **kwargs):
        raise AssertionError(f"should not navigate to {url}")


def _target(url: str, **kwargs) -> MeetTarget:
    now = utc_now()
    values = dict(
        url=url,
        instance_key="evt-1",
        summary="Design review",
        start_utc=now,
        end_utc=now + timedelta(minutes=30),
    )
    values.update(kwargs)
    return MeetTarget(**values)


def test_blocks_non_meet_host_without_navigating():
    page = BoomPage()
    result = run_meet_flow(page, _target("https://evil.example/join"), flow_options())
    assert result.outcome == "blocked_url"
    assert result.opened is False


def test_login_redirect_does_not_click():
    page = RedirectPage("https://accounts.google.com/ServiceLogin")
    result = run_meet_flow(
        page,
        _target("https://meet.google.com/abc-defg-hij"),
        flow_options(allowed_hosts=frozenset({"meet.google.com"})),
    )
    assert result.outcome == "auth_required"
    assert result.opened is False
    assert page.gotos == ["https://meet.google.com/abc-defg-hij"]


def test_shutdown_before_open_does_not_navigate():
    page = BoomPage()
    stop = threading.Event()
    stop.set()
    result = run_meet_flow(
        page,
        _target("https://meet.google.com/abc-defg-hij"),
        flow_options(allowed_hosts=frozenset({"meet.google.com"})),
        stop_event=stop,
    )
    assert result.outcome == "stopped"


def test_fixture_happy_path(page, meet_server):
    result = run_meet_flow(page, _target(meet_url(meet_server, "happy")), flow_options())
    assert result.outcome == "left"
    assert result.mic_off is True
    assert result.camera_off is True
    assert result.joined is True
    assert result.notes_verified is True
    assert result.notes_clicked is True
    assert result.left is True
    assert result.verification_signal
    assert page.locator("#left-banner").is_visible()
    assert page.locator("#mic").get_attribute("aria-label") == "Turn on microphone"
    assert page.locator("#cam").get_attribute("aria-label") == "Turn on camera"


def test_fixture_nobody_joined(page, meet_server):
    result = run_meet_flow(
        page,
        _target(meet_url(meet_server, "nobody")),
        flow_options(wait_for_participant=True, nobody_joined_timeout_seconds=1.2),
    )
    assert result.outcome == "nobody_joined"
    assert result.notes_clicked is False
    assert result.left is True
    assert page.locator("#left-banner").is_visible()


def test_fixture_notes_already_on(page, meet_server):
    result = run_meet_flow(page, _target(meet_url(meet_server, "already-on")), flow_options())
    assert result.outcome == "left"
    assert result.notes_already_on is True
    assert result.notes_verified is True
    assert result.notes_clicked is False
    assert result.left is True


def test_fixture_notes_unavailable(page, meet_server):
    result = run_meet_flow(
        page,
        _target(meet_url(meet_server, "no-notes")),
        flow_options(notes_ui_timeout_seconds=1.5),
    )
    assert result.outcome == "notes_unavailable"
    assert result.notes_verified is False
    assert result.left is True


def test_fixture_consent_and_overflow(page, meet_server):
    consent = run_meet_flow(page, _target(meet_url(meet_server, "consent")), flow_options())
    assert consent.outcome == "left"
    assert consent.consent_clicked is True
    assert consent.notes_verified is True


def test_fixture_overflow_menu(page, meet_server):
    result = run_meet_flow(page, _target(meet_url(meet_server, "overflow")), flow_options())
    assert result.outcome == "left"
    assert result.notes_clicked is True
    assert result.notes_verified is True


def test_fixture_ask_to_join(page, meet_server):
    result = run_meet_flow(page, _target(meet_url(meet_server, "admit")), flow_options())
    assert result.joined is True
    assert result.notes_verified is True
    assert result.outcome == "left"


def test_fixture_until_alone(page, meet_server):
    now = utc_now()
    result = run_meet_flow(
        page,
        _target(
            meet_url(meet_server, "until-alone"),
            start_utc=now,
            end_utc=now + timedelta(minutes=20),
        ),
        flow_options(stay_mode="until_alone", alone_confirm_seconds=0.5),
    )
    assert result.notes_verified is True
    assert result.outcome == "left_alone"
    assert result.left is True


def test_fixture_starts_notes_without_waiting_for_anyone(page, meet_server):
    import time

    started = time.monotonic()
    result = run_meet_flow(
        page,
        _target(meet_url(meet_server, "nobody")),
        flow_options(nobody_joined_timeout_seconds=8),
    )
    elapsed = time.monotonic() - started
    assert result.outcome == "left"
    assert result.notes_verified is True
    assert result.notes_clicked is True
    assert result.left is True
    assert elapsed < 3


def test_fixture_mutes_after_join_when_prejoin_controls_are_missing(page, meet_server):
    result = run_meet_flow(page, _target(meet_url(meet_server, "mute-late")), flow_options())
    assert result.mic_off is True
    assert result.camera_off is True
    assert result.notes_verified is True
    assert result.outcome == "left"
    assert page.locator("#call-mic").get_attribute("aria-label") == "Turn on microphone"
    assert page.locator("#call-cam").get_attribute("aria-label") == "Turn on camera"


def test_fixture_late_join_starts_notes_immediately(page, meet_server):
    result = run_meet_flow(page, _target(meet_url(meet_server, "late")), flow_options())
    assert result.joined is True
    assert result.notes_verified is True
    assert result.notes_already_on is False
    assert result.outcome == "left"


def test_fixture_host_never_admits(page, meet_server):
    result = run_meet_flow(
        page,
        _target(meet_url(meet_server, "no-admit")),
        flow_options(admit_timeout_seconds=1.2),
    )
    assert result.outcome == "blocked_by_host"
    assert result.joined is False
    assert result.notes_clicked is False
    assert result.left is True
    assert page.locator("#left-banner").is_visible()
    assert "did not admit" in (result.error or "")


def test_fixture_until_meeting_end(page, meet_server):
    now = utc_now()
    result = run_meet_flow(
        page,
        _target(
            meet_url(meet_server, "happy"),
            start_utc=now,
            end_utc=now + timedelta(seconds=4),
        ),
        flow_options(stay_mode="until_meeting_end", stay_grace_seconds=0),
    )
    assert result.notes_verified is True
    assert result.outcome == "left_after_end"
    assert result.left is True
