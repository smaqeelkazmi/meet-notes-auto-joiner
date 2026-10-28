"""Regression for the Uzma / Rev9 false join_failed.

The failure screenshot was already in the call: red hang-up in the bottom
toolbar, mic and camera with fake-media "!" badges, the rest of the in-call
toolbar, a top-right count of 2, a pencil chip, and a "Gemini is available"
popover. The old in-call rule required the hang-up plus a People button or
an exact notes label, so the bot timed out, left, and emailed join failed.
"""

from __future__ import annotations

import logging
import smtplib
from datetime import timedelta

from tests.conftest import due_bounds, flow_options, gcal_event, meet_url
from tests.test_notify import FakeSMTP, _notify_cfg

from meet_notes.browser import capture_failure_artifacts
from meet_notes.calendar_sync import parse_event
from meet_notes.meet_flow import _dismiss_callouts, _in_call, run_meet_flow
from meet_notes.meeting_log import publish_attempt
from meet_notes.models import MeetTarget
from meet_notes.state import StateStore
from meet_notes.timeutil import utc_now

STYLE = (
    "<style>button,[role='button'],header{min-width:24px;min-height:24px;"
    "display:inline-block}</style>"
)


def _target(url: str, **kwargs) -> MeetTarget:
    now = utc_now()
    values = dict(
        url=url,
        instance_key="evt-uzma",
        summary="Uzma / Rev9",
        start_utc=now,
        end_utc=now + timedelta(minutes=30),
    )
    values.update(kwargs)
    return MeetTarget(**values)


def _uzma(meet_server, scenario: str) -> str:
    return meet_url(meet_server, scenario, filename="uzma_in_call.html")


def test_lobby_hangup_alone_is_not_in_call(page):
    page.set_content(
        STYLE + "<p>Asking to be let in</p><button aria-label='Leave call'>Leave call</button>"
    )
    assert _in_call(page) is False


def test_greenroom_mic_and_join_are_not_in_call(page):
    page.set_content(
        STYLE
        + "<button aria-label='Turn off microphone'>Mic</button>"
        + "<button aria-label='Join now'>Join now</button>"
    )
    assert _in_call(page) is False


def test_toolbar_hangup_is_enough_even_with_a_shortcut_suffix(page):
    page.set_content(
        STYLE
        + "<div role='toolbar' aria-label='Call controls' data-panel='call-controls'>"
        + "<button aria-label='Leave call (Ctrl+Alt+Q)'></button>"
        + "</div>"
    )
    assert page.locator("button[aria-label='Leave call']").count() == 0
    assert _in_call(page) is True


def test_toolbar_tooltip_hangup_is_enough(page):
    page.set_content(
        STYLE
        + "<div role='toolbar' aria-label='Call controls'>"
        + "<button data-tooltip='Leave call'></button>"
        + "</div>"
    )
    assert page.locator("button[aria-label='Leave call']").count() == 0
    assert _in_call(page) is True


def test_toolbar_button_without_hangup_is_in_call(page):
    page.set_content(STYLE + "<button aria-label='Raise hand'>Raise</button>")
    assert _in_call(page) is True


def test_participant_badge_without_people_label_is_in_call(page):
    page.set_content(
        STYLE
        + "<div role='button' data-people-badge='2' aria-label='2 people in the call'>2</div>"
    )
    assert page.locator("button[aria-label^='People']").count() == 0
    assert page.locator("[data-participant-count]").count() == 0
    assert _in_call(page) is True


def test_title_bar_without_hangup_is_in_call(page):
    page.set_content(
        STYLE + "<header data-call-titlebar><span data-meeting-title>9:42 PM | Uzma / Rev9</span></header>"
    )
    assert _in_call(page) is True


def test_curly_apostrophe_dont_show_again_is_dismissed(page):
    page.set_content(
        STYLE
        + "<div data-callout='gemini' aria-label='Gemini is available'>"
        + "<button id='dismiss' type='button'>Don\u2019t show again</button>"
        + "</div>"
    )
    page.locator("#dismiss").evaluate("node => node.addEventListener('click', () => { node.dataset.dismissed = 'yes'; })")
    _dismiss_callouts(page)
    assert page.locator("#dismiss").get_attribute("data-dismissed") == "yes"


def test_shown_uzma_screen_is_in_call_without_old_companions(page, meet_server):
    page.goto(_uzma(meet_server, "shown"))
    assert page.locator("button[aria-label^='People']").count() == 0
    assert page.locator("[aria-label='Taking notes']").count() == 0
    assert page.locator("[data-meet-notes='active']").count() == 0
    assert page.locator("button[aria-label='Take notes for me']").is_visible() is False
    assert page.locator("#notes-chip").get_attribute("aria-label") == "Taking notes with Gemini"
    assert page.locator("#people-badge").inner_text() == "2"
    assert _in_call(page) is True


def test_uzma_already_taking_notes_is_success_not_join_failed(page, meet_server, tmp_path, monkeypatch, caplog):
    FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setenv("MEET_NOTES_SMTP_PASSWORD", "s3cret-password")
    caplog.set_level(logging.INFO, logger="meet_notes.meet")

    result = run_meet_flow(page, _target(_uzma(meet_server, "already")), flow_options())

    assert result.outcome == "left"
    assert result.joined is True
    assert result.left is True
    assert result.notes_verified is True
    assert result.notes_already_on is True
    assert result.notes_clicked is False
    assert result.mic_off is True
    assert result.camera_off is True
    assert result.verification_signal
    assert "Taking notes" in result.verification_signal or "Gemini is taking notes" in result.verification_signal
    assert "in call detected" in caplog.text
    assert "notes_started" in caplog.text
    assert "join failed" not in caplog.text.casefold()

    assert page.locator("#gemini-popover").get_attribute("data-dismissed") == "yes"
    assert page.locator("#gemini-learn").get_attribute("data-clicked") != "yes"
    assert page.locator("#uzma-mic").get_attribute("aria-label") == "Turn on microphone"
    assert page.locator("#uzma-cam").get_attribute("aria-label") == "Turn on camera"
    assert page.locator("#uzma-mic .bang").get_attribute("aria-label") == "Microphone problem"
    assert page.locator("#uzma-cam .bang").get_attribute("aria-label") == "Camera problem"
    assert page.locator("#left-banner").is_visible()

    cfg = _notify_cfg(tmp_path)
    now, start, end = due_bounds()
    store = StateStore(cfg.state_db)
    event = parse_event(
        gcal_event("evt-uzma", start, end, summary="Uzma / Rev9"),
        cfg.tzinfo,
    )
    assert event is not None
    store.insert_scheduled(event, "primary", now)
    store.claim_join("evt-uzma", now, 3)
    store.save_flow_result("evt-uzma", result, now)
    publish_attempt(cfg, store, "evt-uzma", now, email=True)
    assert len(FakeSMTP.sent) == 1
    message = FakeSMTP.sent[0]
    assert "notes started" in message["Subject"]
    assert "Uzma / Rev9" in message["Subject"]
    assert "join failed" not in message["Subject"].casefold()
    body = message.get_content()
    assert "Notes started: yes" in body
    assert "Notes started at: -" not in body
    assert "already on" in body.casefold()
    assert "join failed" not in body.casefold()
    store.close()


def test_popover_is_dismissed_before_notes_control_is_clicked(page, meet_server, caplog):
    caplog.set_level(logging.INFO, logger="meet_notes.meet")
    result = run_meet_flow(page, _target(_uzma(meet_server, "start")), flow_options())
    assert result.outcome == "left"
    assert result.joined is True
    assert result.notes_verified is True
    assert result.notes_clicked is True
    assert result.notes_already_on is False
    assert result.consent_clicked is True
    assert result.mic_off is True
    assert result.camera_off is True
    assert page.locator("#gemini-popover").get_attribute("data-dismissed") == "yes"
    assert page.locator("#got-it-tip").get_attribute("data-dismissed") == "yes"
    assert page.locator("#gemini-learn").get_attribute("data-clicked") != "yes"
    assert "notes_started" in caplog.text
    assert page.locator("#uzma-mic .bang").get_attribute("aria-label") == "Microphone problem"
    assert page.locator("#uzma-mic").get_attribute("aria-label") == "Turn on microphone"


def test_notes_failure_saves_toolbar_html_next_to_the_screenshot(page, meet_server, tmp_path):
    shots = tmp_path / "screenshots"

    def on_failure(failed, result):
        capture_failure_artifacts(failed, shots, "uzma-rev9", result.outcome)

    result = run_meet_flow(
        page,
        _target(_uzma(meet_server, "no-notes")),
        flow_options(notes_ui_timeout_seconds=1.2, on_failure=on_failure),
    )
    assert result.outcome == "notes_unavailable"
    assert result.joined is True
    assert result.notes_verified is False
    pngs = list(shots.glob("*.png"))
    doms = list(shots.glob("*.dom.txt"))
    assert len(pngs) == 1
    assert len(doms) == 1
    assert pngs[0].stat().st_size > 100
    assert pngs[0].with_suffix(".dom.txt") == doms[0]
    text = doms[0].read_text(encoding="utf-8")
    assert "# accessibility" in text
    assert "# toolbar" in text
    assert "Call controls" in text
    assert "Leave call" in text
    assert "call-controls" in text
    assert "<button" in text


def test_join_failure_saves_an_accessibility_snapshot(page, meet_server, tmp_path):
    shots = tmp_path / "screenshots"

    def on_failure(failed, result):
        capture_failure_artifacts(failed, shots, "evt-1", result.outcome)

    result = run_meet_flow(
        page,
        _target(meet_url(meet_server, "no-admit")),
        flow_options(admit_timeout_seconds=1.2, on_failure=on_failure),
    )
    assert result.outcome == "blocked_by_host"
    assert result.joined is False
    doms = list(shots.glob("*.dom.txt"))
    pngs = list(shots.glob("*.png"))
    assert len(doms) == 1 and len(pngs) == 1
    text = doms[0].read_text(encoding="utf-8")
    assert "# accessibility" in text
    assert "Leave call" in text or "Asking to be let in" in text
    assert "(no toolbar element found)" in text
