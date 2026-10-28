"""Join a Meet page, start notes, and leave.

The page is whatever the caller passes. Production passes a Playwright page.
Tests pass a Playwright page pointed at tests/fixtures/meet_page.html.
This module does not know how the browser was launched, and it refuses any
URL whose host is not in the caller's allow list. The allow list is code,
not configuration, so a config file cannot send the signed-in profile to
some other site.
"""

from __future__ import annotations

import logging
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from meet_notes.logging_setup import log_event
from meet_notes.models import SUCCESS_OUTCOMES, FlowResult, MeetTarget
from meet_notes.participants import observe_participants
from meet_notes.selectors import (
    CAMERA_IS_OFF_SELECTORS,
    CAMERA_TURN_OFF_SELECTORS,
    CONSENT_ACCEPT_SELECTORS,
    DEVICE_LABEL_SELECTORS,
    JOIN_SELECTORS,
    LEAVE_CONFIRM_SELECTORS,
    LEAVE_SELECTORS,
    LEFT_CONFIRM_SELECTORS,
    MIC_IS_OFF_SELECTORS,
    MIC_TURN_OFF_SELECTORS,
    MORE_OPTIONS_SELECTORS,
    NOTES_ACTIVE_SELECTORS,
    NOTES_ENTRY_SELECTORS,
    NOTES_MENU_ITEM_SELECTORS,
    PEOPLE_BUTTON_SELECTORS,
    PREJOIN_DISMISS_SELECTORS,
    START_NOTES_SELECTORS,
)

logger = logging.getLogger("meet_notes.meet")

Progress = Callable[..., None]
Sleep = Callable[[float], None]
Now = Callable[[], datetime]


@dataclass
class FlowOptions:
    allowed_hosts: frozenset[str]
    self_display_names: tuple[str, ...]
    stay_mode: str
    leave_even_if_unverified: bool
    stay_even_if_unverified: bool
    nobody_joined_timeout_seconds: float
    admit_timeout_seconds: float
    participant_poll_seconds: float
    alone_confirm_seconds: float
    notes_ui_timeout_seconds: float
    stay_grace_seconds: float
    navigation_timeout_ms: int
    wait_for_participant: bool = False
    on_failure: Callable[[object, FlowResult], None] | None = None


def flow_options_from_config(cfg) -> FlowOptions:
    return FlowOptions(
        allowed_hosts=frozenset({"meet.google.com"}),
        self_display_names=tuple(cfg.self_display_names),
        stay_mode=cfg.stay_mode,
        leave_even_if_unverified=cfg.leave_even_if_unverified,
        stay_even_if_unverified=cfg.stay_even_if_unverified,
        nobody_joined_timeout_seconds=cfg.nobody_joined_timeout_minutes * 60,
        admit_timeout_seconds=cfg.admit_timeout_minutes * 60,
        participant_poll_seconds=cfg.participant_poll_seconds,
        alone_confirm_seconds=cfg.alone_confirm_seconds,
        notes_ui_timeout_seconds=cfg.notes_ui_timeout_seconds,
        stay_grace_seconds=cfg.stay_grace_minutes * 60,
        navigation_timeout_ms=cfg.navigation_timeout_seconds * 1000,
        wait_for_participant=cfg.wait_for_participant,
    )


def run_meet_flow(
    page,
    target: MeetTarget,
    options: FlowOptions,
    *,
    progress: Progress | None = None,
    stop_event=None,
    sleep: Sleep = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    now: Now | None = None,
) -> FlowResult:
    from meet_notes.timeutil import utc_now

    progress = progress or (lambda *args, **kwargs: None)
    now = now or utc_now
    result = FlowResult()

    def halted() -> bool:
        return bool(stop_event is not None and stop_event.is_set())

    def finish() -> FlowResult:
        _capture_failure(page, options, result)
        if result.opened and not result.left and _host_allowed(getattr(page, "url", ""), options):
            if _leave(page):
                result.left = True
                progress("leaving", left_at=now())
        return result

    rejection = unsafe_target(target.url, options.allowed_hosts)
    if rejection:
        result.outcome = "blocked_url"
        result.error = rejection
        log_event(logger, logging.WARNING, "blocked meet url", outcome=result.outcome, error=rejection)
        return result

    if halted():
        result.outcome = "stopped"
        result.error = "Shutdown requested before the Meet link was opened"
        return result

    try:
        page.goto(
            target.url,
            wait_until="domcontentloaded",
            timeout=options.navigation_timeout_ms,
        )
    except Exception as exc:
        result.outcome = "failed"
        result.error = f"Could not open the Meet link: {exc}"
        log_event(logger, logging.ERROR, "meet open failed", instance_key=target.instance_key, error=result.error)
        return result

    current = getattr(page, "url", "") or ""
    if _is_login_url(current):
        result.outcome = "auth_required"
        result.error = (
            "Browser profile is not signed in. Run meet-notes login. "
            "The bot does not type Google credentials."
        )
        log_event(
            logger,
            logging.WARNING,
            "meet profile signed out",
            instance_key=target.instance_key,
            outcome=result.outcome,
        )
        return result
    if not _host_allowed(current, options):
        result.outcome = "blocked_url"
        result.error = "The Meet link redirected to a host this bot will not use"
        log_event(logger, logging.WARNING, "meet redirect blocked", instance_key=target.instance_key, url=current)
        return result

    result.opened = True
    progress("joining", meet_opened_at=now())
    log_event(logger, logging.INFO, "meet opened", instance_key=target.instance_key, summary=target.summary)

    if not _enter_call(page, options, result, progress, halted, sleep, monotonic, now):
        return finish()

    result.joined = True
    progress("in_call", joined_at=now())
    log_event(
        logger,
        logging.INFO,
        "joined call",
        instance_key=target.instance_key,
        mic_off=result.mic_off,
        camera_off=result.camera_off,
    )
    _log_participants(page, options, result, logging.INFO)

    if halted():
        result.outcome = "left_on_shutdown"
        result.error = "Shutdown requested after joining"
        return finish()

    if options.wait_for_participant:
        if not _wait_for_others(page, options, result, progress, halted, sleep, monotonic, now):
            return finish()

    progress("starting_notes")
    _ensure_notes(page, options, result, progress, halted, sleep, monotonic, now)
    if result.outcome == "notes_unavailable":
        return finish()
    if halted():
        result.outcome = "left_on_shutdown"
        result.error = "Shutdown requested while starting notes"
        return finish()

    _verify(page, result, progress, now)
    if _should_stay(result, options):
        _stay(page, target, options, result, progress, halted, sleep, monotonic, now)
    elif result.notes_verified:
        result.outcome = "left"
    elif result.outcome != "notes_unavailable":
        result.outcome = "left_unverified"
        if not result.error:
            result.error = "The UI did not confirm that notes are active"

    log_event(
        logger,
        logging.INFO,
        "notes check finished",
        instance_key=target.instance_key,
        outcome=result.outcome,
        notes_verified=result.notes_verified,
        verification_signal=result.verification_signal,
        notes_already_on=result.notes_already_on,
    )
    return finish()


def unsafe_target(url: str, allowed_hosts: frozenset[str]) -> str | None:
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return "Only http and https Meet links can be opened"
    host = (parsed.hostname or "").lower()
    if host not in {item.lower() for item in allowed_hosts}:
        return "That host is not a Meet host this bot is allowed to open"
    if parsed.username or parsed.password:
        return "Meet links must not contain a username or password"
    return None


def media_is_off(aria_label: str | None) -> bool | None:
    """Meet's button label is the action, so 'Turn on' or 'Unmute' means the device is off.

    'Unmute' contains 'mute', so the off-state words are checked first.
    """
    if not aria_label:
        return None
    label = aria_label.strip().casefold()
    if label.startswith("turn on") or label.startswith("unmute"):
        return True
    if label.startswith("turn off") or label.startswith("mute"):
        return False
    return None


def _enter_call(page, options, result, progress, halted, sleep, monotonic, now) -> bool:
    deadline = monotonic() + options.admit_timeout_seconds
    last_join_click = None
    while monotonic() < deadline:
        if halted():
            result.outcome = "stopped" if not result.joined else "left_on_shutdown"
            result.error = "Shutdown requested while entering the call"
            return False
        if _in_call(page):
            _confirm_muted(page, result, sleep, monotonic)
            return True
        _mute(page, result)
        _click_first(page, PREJOIN_DISMISS_SELECTORS)
        join_visible = _first_visible(page, JOIN_SELECTORS)[0] is not None
        waited = last_join_click is None or monotonic() - last_join_click > 3
        if join_visible and waited:
            clicked = _click_first(page, JOIN_SELECTORS)
            if clicked:
                last_join_click = monotonic()
                if "ask to join" in clicked.casefold():
                    result.extra["asked_to_join"] = True
                log_event(logger, logging.INFO, "clicked join", asked_to_join=bool(result.extra.get("asked_to_join")))
        sleep(options.participant_poll_seconds)
    if result.extra.get("asked_to_join"):
        result.outcome = "blocked_by_host"
        result.error = "The host did not admit this account. Notes were not started."
    else:
        result.outcome = "join_failed"
        result.error = (
            "Could not enter the call before the admit timeout. "
            "The account may be waiting for the host, or the Meet UI changed. "
            "Update selectors.py if the greenroom no longer matches."
        )
    log_event(logger, logging.WARNING, "join failed", outcome=result.outcome, error=result.error)
    return False


def _wait_for_others(page, options, result, progress, halted, sleep, monotonic, now) -> bool:
    progress("waiting")
    deadline = monotonic() + options.nobody_joined_timeout_seconds
    last_signal = "unknown"
    while monotonic() < deadline:
        if halted():
            result.outcome = "left_on_shutdown"
            result.error = "Shutdown requested while waiting for another participant"
            return False
        if not _in_call(page) and _left_banner(page):
            result.outcome = "failed"
            result.error = "The call ended before another participant joined"
            return False
        others, signal, _detail = _log_participants(page, options, result, logging.DEBUG)
        last_signal = signal
        if others >= 1:
            result.others_seen = others
            progress("waiting", others_joined_at=now())
            log_event(logger, logging.INFO, "another participant joined", others=others, signal=signal)
            return True
        # The debug line above is every poll. An info line is only when the count changes.
        sleep(options.participant_poll_seconds)
    result.outcome = "nobody_joined"
    result.participant_signal = last_signal
    if last_signal == "unknown":
        result.error = (
            "No other participant could be confirmed. The people list was not readable, "
            "so selectors.py may need an update, and the nobody-joined timeout expired."
        )
    else:
        result.error = "No other participant joined before the timeout"
    log_event(
        logger,
        logging.INFO,
        "nobody joined",
        signal=last_signal,
        error=result.error,
    )
    return False


def _ensure_notes(page, options, result, progress, halted, sleep, monotonic, now) -> None:
    deadline = monotonic() + options.notes_ui_timeout_seconds
    while monotonic() < deadline:
        if halted():
            return
        signal = _notes_signal(page)
        if signal:
            result.notes_verified = True
            result.verification_signal = signal
            if not result.notes_clicked:
                result.notes_already_on = True
            return
        if not result.notes_clicked:
            if _click_first(page, NOTES_ENTRY_SELECTORS):
                result.notes_clicked = True
                progress("starting_notes", notes_started_at=now())
                log_event(logger, logging.INFO, "opened notes control")
                sleep(options.participant_poll_seconds)
                continue
            if _click_first(page, MORE_OPTIONS_SELECTORS):
                if _click_first(page, NOTES_MENU_ITEM_SELECTORS):
                    result.notes_clicked = True
                    progress("starting_notes", notes_started_at=now())
                    log_event(logger, logging.INFO, "opened notes control from the more menu")
                sleep(options.participant_poll_seconds)
                continue
        if _click_first(page, START_NOTES_SELECTORS):
            result.notes_clicked = True
            result.consent_clicked = True
            if result.extra.get("notes_started_marked") is not True:
                progress("starting_notes", notes_started_at=now())
                result.extra["notes_started_marked"] = True
            log_event(logger, logging.INFO, "clicked start notes")
            sleep(options.participant_poll_seconds)
            continue
        if _click_first(page, CONSENT_ACCEPT_SELECTORS):
            result.consent_clicked = True
            log_event(logger, logging.INFO, "accepted notes consent")
            sleep(options.participant_poll_seconds)
            continue
        sleep(options.participant_poll_seconds)

    if _notes_signal(page):
        result.notes_verified = True
        result.verification_signal = _notes_signal(page)
        if not result.notes_clicked:
            result.notes_already_on = True
        return
    if not result.notes_clicked:
        result.outcome = "notes_unavailable"
        result.error = (
            "The notes control was not in the Meet UI. The organizer may lack a Gemini "
            "license, the admin may have disabled it, host controls may block this account, "
            "or the button label changed. The bot is leaving the call."
        )
        log_event(logger, logging.WARNING, "notes unavailable", error=result.error)
        return
    result.error = (
        "The notes control was clicked, but the meeting never showed notes as active. "
        "The bot is leaving the call."
    )
    log_event(logger, logging.WARNING, "notes not confirmed", error=result.error)


def _verify(page, result, progress, now) -> None:
    signal = _notes_signal(page)
    if signal:
        result.notes_verified = True
        result.verification_signal = signal
        progress(
            "starting_notes",
            notes_verified=True,
            notes_verified_at=now(),
            notes_already_on=result.notes_already_on,
            verification_signal=signal,
        )
        log_event(logger, logging.INFO, "notes verified", signal=signal, already_on=result.notes_already_on)
    else:
        result.notes_verified = False
        log_event(logger, logging.WARNING, "notes not verified", clicked=result.notes_clicked)


def _should_stay(result: FlowResult, options: FlowOptions) -> bool:
    if result.outcome == "notes_unavailable":
        return False
    if options.stay_mode == "leave_after_notes":
        return False
    if result.notes_verified:
        return True
    return bool(options.stay_even_if_unverified)


def _stay(page, target, options, result, progress, halted, sleep, monotonic, now) -> None:
    progress("staying")
    deadline = target.end_utc + timedelta(seconds=options.stay_grace_seconds)
    alone_since = None
    log_event(
        logger,
        logging.INFO,
        "staying in the call",
        stay_mode=options.stay_mode,
        until=deadline.isoformat(),
    )
    while True:
        if halted():
            result.outcome = "left_on_shutdown"
            result.error = "Shutdown requested while staying in the call"
            return
        current = now()
        if options.stay_mode == "until_alone":
            others, signal, _detail = _log_participants(page, options, result, logging.DEBUG)
            if others == 0 and signal != "unknown":
                if alone_since is None:
                    alone_since = monotonic()
                elif monotonic() - alone_since >= options.alone_confirm_seconds:
                    result.outcome = "left_alone"
                    _verify(page, result, progress, now)
                    return
            else:
                alone_since = None
            if current > deadline:
                result.outcome = "left_after_end"
                _verify(page, result, progress, now)
                return
        else:
            if current >= deadline:
                result.outcome = "left_after_end"
                _verify(page, result, progress, now)
                return
        if not _in_call(page) and _left_banner(page):
            result.left = True
            result.outcome = result.outcome if result.outcome in SUCCESS_OUTCOMES else "left"
            return
        sleep(options.participant_poll_seconds)


def _confirm_muted(page, result: FlowResult, sleep: Sleep, monotonic) -> None:
    """Re-read the mic and camera after the call opens. Meet often turns them back on."""
    deadline = monotonic() + 8
    while True:
        _mute(page, result)
        if result.mic_off and result.camera_off:
            log_event(
                logger,
                logging.INFO,
                "mic and camera confirmed off",
                mic_off=True,
                camera_off=True,
            )
            return
        if monotonic() >= deadline:
            log_event(
                logger,
                logging.WARNING,
                "mic or camera still not confirmed off",
                mic_off=result.mic_off,
                camera_off=result.camera_off,
                labels=", ".join(_visible_device_labels(page))[:300],
            )
            return
        sleep(0.2)


def _mute(page, result: FlowResult) -> None:
    result.mic_off = _settle_device(page, result.mic_off, MIC_TURN_OFF_SELECTORS, MIC_IS_OFF_SELECTORS)
    result.camera_off = _settle_device(page, result.camera_off, CAMERA_TURN_OFF_SELECTORS, CAMERA_IS_OFF_SELECTORS)


def _settle_device(page, previous: bool, turn_off: list[str], is_off: list[str]) -> bool:
    state = _device_is_off(page, turn_off, is_off)
    if state is False:
        _click_first(page, turn_off)
        state = _device_is_off(page, turn_off, is_off)
    if state is None:
        return previous
    return state


def _device_is_off(page, turn_off: list[str], is_off: list[str]) -> bool | None:
    if _first_visible(page, is_off)[0]:
        return True
    if _first_visible(page, turn_off)[0]:
        return False
    return None


def _visible_device_labels(page) -> list[str]:
    labels: list[str] = []
    for selector in DEVICE_LABEL_SELECTORS:
        try:
            locator = page.locator(selector)
            count = min(locator.count(), 4)
        except Exception:
            continue
        for index in range(count):
            item = locator.nth(index)
            try:
                if not item.is_visible():
                    continue
                label = item.get_attribute("aria-label")
            except Exception:
                continue
            if label and label not in labels:
                labels.append(label)
    return labels


def _log_participants(page, options: FlowOptions, result: FlowResult, level: int):
    others, signal, detail = observe_participants(page, options.self_display_names)
    previous = result.extra.get("last_observed_others")
    changed = previous != others or result.participant_signal != signal
    result.extra["last_observed_others"] = others
    result.participant_signal = signal
    if others > result.others_seen:
        result.others_seen = others
    log_event(
        logger,
        logging.INFO if changed else level,
        "participant count",
        others=others,
        signal=signal,
        **detail,
    )
    return others, signal, detail


def _leave(page) -> bool:
    clicked = _click_first(page, LEAVE_SELECTORS)
    if not clicked:
        return False
    _click_first(page, LEAVE_CONFIRM_SELECTORS)
    return True


def _in_call(page) -> bool:
    """The meeting UI is up. A lobby hang-up button alone is not enough."""
    if _first_visible(page, LEAVE_SELECTORS)[0] is None:
        return False
    if _first_visible(page, PEOPLE_BUTTON_SELECTORS)[0] is not None:
        return True
    if _first_visible(page, NOTES_ENTRY_SELECTORS)[0] is not None:
        return True
    if _first_visible(page, NOTES_ACTIVE_SELECTORS)[0] is not None:
        return True
    return False


def _left_banner(page) -> bool:
    return _first_visible(page, LEFT_CONFIRM_SELECTORS)[0] is not None


def _notes_signal(page) -> str | None:
    return _first_visible(page, NOTES_ACTIVE_SELECTORS)[0]


def _first_visible(page, selectors: list[str]):
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = min(locator.count(), 5)
        except Exception:
            continue
        for index in range(count):
            item = locator.nth(index)
            try:
                if item.is_visible():
                    return selector, item
            except Exception:
                continue
    return None, None


def _click_first(page, selectors: list[str]) -> str | None:
    selector, locator = _first_visible(page, selectors)
    if locator is None:
        return None
    try:
        locator.click(timeout=1500)
    except Exception:
        return None
    return selector


def _host_allowed(url: str, options: FlowOptions) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return host in {item.lower() for item in options.allowed_hosts}


def _is_login_url(url: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return host in {"accounts.google.com", "accounts.youtube.com"}


def _capture_failure(page, options: FlowOptions, result: FlowResult) -> None:
    if options.on_failure is None:
        return
    if result.outcome in SUCCESS_OUTCOMES or result.outcome == "nobody_joined":
        return
    try:
        options.on_failure(page, result)
    except Exception:
        logger.exception("failure screenshot failed")
