"""Playwright persistent profile. Login is headed. The run loop is headless.

The profile is created by `meet-notes login` and then reused. One Chromium
process keeps that profile open. Each meeting gets its own page in that
process. This module never types a Google password. If the profile is signed
out, the Meet flow stops on the accounts host and logs auth_required.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from meet_notes.config import AppConfig
from meet_notes.join_session import target_from_row
from meet_notes.logging_setup import log_event
from meet_notes.meet_flow import FlowOptions, flow_options_from_config, run_meet_flow, unsafe_target
from meet_notes.models import FlowResult, MeetingRow
from meet_notes.page_bridge import SyncPage
from meet_notes.selectors import TOOLBAR_HTML_SELECTORS
from meet_notes.state import StateStore

logger = logging.getLogger("meet_notes.browser")

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


class ProfileSession:
    """One signed-in Chromium profile, one page per meeting."""

    def __init__(self, cfg: AppConfig, store: StateStore) -> None:
        self.cfg = cfg
        self.store = store
        self._start_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._playwright = None
        self._context = None

    def join(self, row: MeetingRow, stop_event, options: FlowOptions | None = None) -> FlowResult:
        options = options or flow_options_from_config(self.cfg)
        if not row.meet_url or unsafe_target(row.meet_url, options.allowed_hosts):
            return FlowResult(
                outcome="blocked_url",
                error="Refusing to open a link that is not a https://meet.google.com meeting.",
            )
        self.ensure_started()
        page = self.new_page()
        try:
            page.set_default_timeout(self.cfg.navigation_timeout_seconds * 1000)
            local = replace(options)
            if self.cfg.screenshot_on_failure and self.cfg.log_dir is not None:
                shot_dir = self.cfg.log_dir / "screenshots"

                def on_failure(failed_page, result: FlowResult, key=row.instance_key) -> None:
                    capture_failure_artifacts(failed_page, shot_dir, key, result.outcome)

                local.on_failure = on_failure

            def progress(status: str, **fields: object) -> None:
                self.store.mark(row.instance_key, status=status, **fields)

            return run_meet_flow(
                page,
                target_from_row(row),
                local,
                progress=progress,
                stop_event=stop_event,
            )
        finally:
            self.close_page(page)

    def new_page(self) -> SyncPage:
        self.ensure_started()
        assert self._loop is not None and self._context is not None

        async def _open():
            return await self._context.new_page()

        page = asyncio.run_coroutine_threadsafe(_open(), self._loop).result()
        return SyncPage(self._loop, page)

    def close_page(self, page: SyncPage) -> None:
        try:
            page.close()
        except Exception:
            logger.exception("could not close a Meet page")

    def ensure_started(self) -> None:
        with self._start_lock:
            if self._context is not None:
                return
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise RuntimeError(
                    "Playwright is not installed. Run `pip install -e .` and `playwright install chromium`."
                ) from exc
            profile = self.cfg.profile_dir
            assert profile is not None
            profile.mkdir(parents=True, exist_ok=True)
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="meet-browser",
                daemon=True,
            )
            self._thread.start()
            try:
                asyncio.run_coroutine_threadsafe(self._launch(async_playwright), self._loop).result()
            except Exception:
                if self._loop is not None and self._loop.is_running():
                    try:
                        asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop).result(timeout=15)
                    except Exception:
                        logger.exception("browser startup cleanup failed")
                self._stop_loop()
                self._context = None
                self._playwright = None
                raise
            log_event(
                logger,
                logging.INFO,
                "browser profile started",
                headless=self.cfg.headless,
                profile=str(profile),
            )

    def close(self) -> None:
        with self._start_lock:
            loop = self._loop
            if loop is None:
                return
            if loop.is_running():
                try:
                    asyncio.run_coroutine_threadsafe(self._shutdown(), loop).result(timeout=30)
                except Exception:
                    logger.exception("browser shutdown failed")
                self._stop_loop()
            self._context = None
            self._playwright = None

    async def _launch(self, async_playwright) -> None:
        self._playwright = await async_playwright().start()
        profile = self.cfg.profile_dir
        assert profile is not None
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=self.cfg.headless,
            **_launch_kwargs(self.cfg),
        )

    async def _shutdown(self) -> None:
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _stop_loop(self) -> None:
        loop = self._loop
        thread = self._thread
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=10)
        self._loop = None
        self._thread = None


def launch_and_join(
    cfg: AppConfig,
    store: StateStore,
    row: MeetingRow,
    stop_event,
) -> FlowResult:
    """Join one meeting, then close the profile. Concurrent runs use ProfileSession."""
    session = ProfileSession(cfg, store)
    try:
        return session.join(row, stop_event)
    finally:
        session.close()


def login_profile(cfg: AppConfig, wait_minutes: float | None) -> int:
    """Open a headed window so Aqeel can sign in. The profile is saved on close."""
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        print(
            "No graphical session is available (DISPLAY is unset).\n"
            "On a laptop, run this command from the desktop session.\n"
            "On EC2, start a VNC server and export DISPLAY, or copy a profile\n"
            "created by `meet-notes login` on the laptop. See the README.",
            file=sys.stderr,
        )
        if wait_minutes is None:
            return 1
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed. Run `pip install -e .` and `playwright install chromium`.", file=sys.stderr)
        return 1

    profile = cfg.profile_dir
    assert profile is not None
    profile.mkdir(parents=True, exist_ok=True)
    _write_profile_notice(profile)

    playwright = sync_playwright().start()
    context = None
    try:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,
            **_launch_kwargs(cfg),
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://accounts.google.com/", wait_until="domcontentloaded")
        print("Sign in to the Google Workspace account that should join Meet.")
        print("Use that account only. When the account page is visible, come back here.")
        if wait_minutes is None:
            try:
                input("Press Enter to open Meet, then Enter again to save the profile and close... ")
                page.goto("https://meet.google.com/", wait_until="domcontentloaded")
                input("Press Enter to save the profile and close the browser... ")
            except EOFError:
                print("No terminal input. Re-run with --wait-minutes on a VNC session.", file=sys.stderr)
                return 1
        else:
            print(f"Waiting up to {wait_minutes} minutes for a Google session cookie.")
            page.goto("https://meet.google.com/", wait_until="domcontentloaded")
            import time

            deadline = time.monotonic() + wait_minutes * 60
            while time.monotonic() < deadline:
                if _context_has_session(context):
                    print("Session cookie found. Saving the profile.")
                    break
                time.sleep(2)
            else:
                print("Timed out before a Google session cookie appeared.", file=sys.stderr)
                return 1
        signed_in = _context_has_session(context)
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        playwright.stop()

    if not signed_in:
        print(
            "The profile directory was saved, but no Google session cookie was visible. "
            "Sign in and run `meet-notes login` again.",
            file=sys.stderr,
        )
        return 1
    print(f"Profile saved at {profile}")
    return 0


def _launch_kwargs(cfg: AppConfig) -> dict:
    from meet_notes.blank_media import ensure_blank_media

    args = [
        "--disable-blink-features=AutomationControlled",
        "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if cfg.profile_dir is not None:
        audio, video = ensure_blank_media(cfg.profile_dir / "blank-media")
        args.append(f"--use-file-for-fake-audio-capture={audio}")
        args.append(f"--use-file-for-fake-video-capture={video}")
    if cfg.no_sandbox or (hasattr(os, "geteuid") and os.geteuid() == 0):
        args.append("--no-sandbox")
    kwargs = {
        "viewport": {"width": 1280, "height": 800},
        "locale": cfg.browser_locale,
        "timezone_id": cfg.timezone,
        "args": args,
        "ignore_default_args": ["--enable-automation"],
        "accept_downloads": False,
    }
    if cfg.browser_channel and cfg.browser_channel != "chromium":
        kwargs["channel"] = cfg.browser_channel
    return kwargs


def _context_has_session(context) -> bool:
    try:
        cookies = context.cookies(["https://meet.google.com", "https://accounts.google.com"])
    except Exception:
        return False
    names = {cookie.get("name") for cookie in cookies}
    return bool(names & {"SID", "SAPISID", "__Secure-1PSID", "__Secure-3PSID"})


def capture_failure_artifacts(page, directory: Path, instance_key: str, outcome: str) -> Path:
    """Screenshot plus the accessibility tree and toolbar HTML.

    Written beside each other when a join or notes attempt fails, so the
    next Meet UI change can be diffed without another live call.
    """
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = _SAFE.sub("_", instance_key)[:80] or "meeting"
    path = directory / f"{stamp}-{safe}-{outcome}.png"
    try:
        page.screenshot(path=str(path), full_page=False)
    except Exception:
        logger.exception("failure screenshot failed")
    sidecar = path.with_suffix(".dom.txt")
    sidecar.write_text(_failure_dom(page), encoding="utf-8")
    log_event(
        logger,
        logging.INFO,
        "saved failure artifacts",
        screenshot=str(path),
        dom=str(sidecar),
        outcome=outcome,
    )
    return path


def _failure_dom(page) -> str:
    parts = [
        "# accessibility",
        _accessibility_snapshot(page),
        "",
        "# toolbar",
        _toolbar_html(page) or "(no toolbar element found)",
    ]
    return "\n".join(parts)[:100_000]


def _accessibility_snapshot(page) -> str:
    try:
        locator = page.locator("body")
        snapshot = locator.aria_snapshot(timeout=2000)
    except Exception as exc:
        return f"(accessibility snapshot failed: {exc})"
    text = (snapshot or "").strip()
    return text or "(empty accessibility snapshot)"


def _toolbar_html(page) -> str | None:
    for selector in TOOLBAR_HTML_SELECTORS:
        try:
            locator = page.locator(selector)
            count = min(locator.count(), 3)
        except Exception:
            continue
        for index in range(count):
            item = locator.nth(index)
            try:
                if not item.is_visible():
                    continue
                html = item.evaluate("node => node.outerHTML")
            except Exception:
                continue
            if html:
                return str(html)[:20_000]
    return None


def _write_profile_notice(profile: Path) -> None:
    notice = profile / "PROFILE_README.txt"
    if notice.exists():
        return
    notice.write_text(
        "This Chromium profile is a signed-in Google session.\n"
        "Treat it like a password. Do not commit it or send it in chat.\n"
        "It was created by meet-notes login for joining Google Meet.\n",
        encoding="utf-8",
    )
