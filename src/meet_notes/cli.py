"""Command line for the one-user Meet notes joiner."""

from __future__ import annotations

import argparse
import sys
import threading

from meet_notes import __version__
from meet_notes.config import ensure_runtime_dirs, load_config
from meet_notes.errors import AlreadyRunning, ConfigError
from meet_notes.logging_setup import configure_logging, get_logger
from meet_notes.timeutil import utc_now


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meet-notes",
        description=(
            "Watch one Google Calendar and join its Meet calls long enough to start "
            "Gemini notes, then leave. Overlapping meetings are joined together."
        ),
    )
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("--log-level", help="DEBUG, INFO, WARNING, or ERROR")
    sub = parser.add_subparsers(dest="command", required=True)

    auth = sub.add_parser("auth", help="Save a Calendar read-only OAuth token")
    auth.add_argument("--oauth-port", type=int, default=None, help="Loopback port. Use 8765 with an SSH tunnel.")
    auth.add_argument("--no-browser", action="store_true", help="Print the URL instead of opening a browser")

    login = sub.add_parser("login", help="Sign the persistent browser profile into Google")
    login.add_argument(
        "--wait-minutes",
        type=float,
        default=None,
        help="On a VNC session, wait this long for a Google session cookie instead of reading Enter",
    )

    run = sub.add_parser("run", help="Poll the calendar and join due meetings")
    run.add_argument("--dry-run", action="store_true", help="Sync and log what would be joined. Do not open Meet.")

    sub.add_parser("plan", help="Sync the calendar and print what would be joined")

    status = sub.add_parser("status", help="Show local health without calling Google")
    status.add_argument("--json", action="store_true")
    status.add_argument("--strict", action="store_true", help="Exit 1 when there are warnings too")

    history = sub.add_parser("history", help="Show the meeting run history")
    history.add_argument("--limit", type=int, default=20)
    history.add_argument("--json", action="store_true")

    requeue = sub.add_parser(
        "requeue",
        help="Manually allow one more join. The automatic loop never does this.",
    )
    requeue.add_argument("instance_key")
    requeue.add_argument(
        "--confirm",
        action="store_true",
        help="Required. This can join a meeting a second time.",
    )

    sub.add_parser("check-profile", help="Look for a Google session cookie in the local profile")
    sub.add_parser("test-email", help="Send one SMTP test message to notify.to")
    sub.add_parser("version", help="Print the version")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    try:
        cfg = load_config(args.config)
        if args.log_level:
            cfg.log_level = args.log_level.upper()
            cfg.validate()
        if args.command == "run" and args.dry_run:
            cfg.dry_run = True
        if args.command == "auth" and args.oauth_port is not None:
            cfg.oauth_port = args.oauth_port
            cfg.validate()
        return _dispatch(args, cfg)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except AlreadyRunning as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _dispatch(args, cfg) -> int:
    if args.command == "status":
        return _status(cfg, as_json=args.json, strict=args.strict)
    if args.command == "history":
        return _history(cfg, limit=args.limit, as_json=args.json)
    if args.command == "requeue":
        return _requeue(cfg, args.instance_key, confirm=args.confirm)
    if args.command == "check-profile":
        return _check_profile(cfg)
    if args.command == "auth":
        return _auth(cfg, open_browser=not args.no_browser)
    if args.command == "login":
        return _login(cfg, wait_minutes=args.wait_minutes)
    if args.command == "plan":
        return _plan(cfg)
    if args.command == "run":
        return _run(cfg)
    if args.command == "test-email":
        return _test_email(cfg)
    print(f"Unknown command {args.command}", file=sys.stderr)
    return 2


def _status(cfg, *, as_json: bool, strict: bool) -> int:
    from meet_notes.health import inspect_health
    from meet_notes.present import format_status

    report = inspect_health(cfg)
    print(format_status(report, as_json=as_json))
    if not report.ready:
        return 1
    if strict and report.warnings:
        return 1
    return 0


def _history(cfg, *, limit: int, as_json: bool) -> int:
    from meet_notes.present import format_history
    from meet_notes.state import StateStore

    if limit < 1:
        print("limit must be at least 1", file=sys.stderr)
        return 2
    store = StateStore(cfg.state_db)
    try:
        rows = store.history(limit)
    finally:
        store.close()
    print(format_history(rows, cfg.tzinfo, as_json=as_json))
    return 0


def _requeue(cfg, instance_key: str, *, confirm: bool) -> int:
    if not confirm:
        print(
            "Refusing to requeue without --confirm. This can make the bot join a meeting again.",
            file=sys.stderr,
        )
        return 2
    from meet_notes.state import StateStore

    store = StateStore(cfg.state_db)
    try:
        found = store.requeue(instance_key, utc_now())
    finally:
        store.close()
    if not found:
        print(f"No meeting recorded with id {instance_key}", file=sys.stderr)
        return 1
    print(f"Requeued {instance_key}. The next run may join it once.")
    return 0


def _test_email(cfg) -> int:
    from meet_notes.notify import send_test_email

    ok, message = send_test_email(cfg)
    print(message)
    return 0 if ok else 1


def _check_profile(cfg) -> int:
    from meet_notes.health import _session_cookie_present

    profile = cfg.profile_dir
    print(f"Profile: {profile}")
    if profile is None or not profile.exists():
        print("Profile directory does not exist. Run meet-notes login.")
        return 1
    cookie = _session_cookie_present(profile)
    if cookie is True:
        print("A Google session cookie is stored in this profile.")
        print("This does not prove the session is still accepted. The first live meeting will.")
        return 0
    if cookie is None:
        print("The cookie database could not be read. If Chromium is open, close it and try again.")
        return 1
    print("No Google session cookie found. Run meet-notes login.")
    return 1


def _auth(cfg, *, open_browser: bool) -> int:
    from meet_notes.google_calendar import run_auth_flow

    ensure_runtime_dirs(cfg)
    configure_logging(cfg.log_dir, cfg.log_level)
    print("Opening Google's consent screen for Calendar read-only access.")
    print("This does not grant access to Gmail, Drive, or editing events.")
    if not open_browser:
        print("A URL will be printed. Open it on a machine where you can sign in as Aqeel.")
    run_auth_flow(
        cfg.credentials_file,
        cfg.token_file,
        port=cfg.oauth_port,
        open_browser=open_browser,
    )
    print(f"Token saved at {cfg.token_file}")
    return 0


def _login(cfg, *, wait_minutes: float | None) -> int:
    from meet_notes.browser import login_profile

    ensure_runtime_dirs(cfg)
    configure_logging(cfg.log_dir, cfg.log_level)
    return login_profile(cfg, wait_minutes)


def _plan(cfg) -> int:
    from meet_notes.google_calendar import GoogleCalendarClient
    from meet_notes.present import format_preview
    from meet_notes.runner import preview_rows
    from meet_notes.state import StateStore

    ensure_runtime_dirs(cfg)
    configure_logging(cfg.log_dir, cfg.log_level)
    client = GoogleCalendarClient(cfg.credentials_file, cfg.token_file)
    store = StateStore(cfg.state_db)
    try:
        from meet_notes.calendar_sync import sync_calendar

        sync_calendar(store, client, cfg, utc_now())
        print(format_preview(preview_rows(store, cfg, utc_now()), cfg.tzinfo))
    finally:
        store.close()
    return 0


def _run(cfg) -> int:
    from meet_notes.browser import ProfileSession
    from meet_notes.google_calendar import GoogleCalendarClient
    from meet_notes.locking import SingleInstance
    from meet_notes.runner import install_stop_signals, serve
    from meet_notes.state import StateStore

    ensure_runtime_dirs(cfg)
    configure_logging(cfg.log_dir, cfg.log_level)
    logger = get_logger("meet_notes")
    logger.info(
        "starting meet-notes %s dry_run=%s concurrent=%s",
        __version__,
        cfg.dry_run,
        cfg.max_concurrent_joins,
    )
    client = GoogleCalendarClient(cfg.credentials_file, cfg.token_file)
    store = StateStore(cfg.state_db)
    stop_event = threading.Event()
    install_stop_signals(stop_event)
    session = ProfileSession(cfg, store)

    def joiner(row):
        return session.join(row, stop_event)

    lock = SingleInstance(cfg.state_db.with_suffix(".lock"))
    try:
        with lock:
            serve(cfg, store, client, joiner, stop_event)
    finally:
        session.close()
        store.close()
    return 0
