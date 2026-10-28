"""Poll the calendar and join each due meeting at most once.

Meetings that are due together are started together, up to
max_concurrent_joins. The poll does not wait for a call to finish, so a
second meeting is not stuck behind the first one.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta

from meet_notes.calendar_sync import sync_calendar
from meet_notes.config import AppConfig
from meet_notes.join_pool import JoinCoordinator
from meet_notes.logging_setup import log_event
from meet_notes.models import CLAIMABLE_STATUSES, MeetingRow
from meet_notes.push import should_renew
from meet_notes.scheduler import classify_window
from meet_notes.state import StateStore
from meet_notes.timeutil import utc_now

logger = logging.getLogger("meet_notes.runner")


def sync_and_join(
    store: StateStore,
    client,
    joiner,
    cfg: AppConfig,
    now: datetime,
    *,
    join: bool,
) -> list[str]:
    stats = sync_calendar(store, client, cfg, now)
    log_event(logger, logging.INFO, "calendar sync finished", **{key: stats[key] for key in sorted(stats)})
    if not join or cfg.dry_run:
        return []
    return join_due(store, joiner, cfg, now)


def join_due(
    store: StateStore,
    joiner,
    cfg: AppConfig,
    now: datetime,
    *,
    wait: bool = True,
    coordinator: JoinCoordinator | None = None,
) -> list[str]:
    """Join every meeting that is due at `now`.

    The default waits until those visits finish, still running only
    `max_concurrent_joins` at once. Pass a coordinator and wait=False to
    return as soon as the visits have been started.
    """
    owns = coordinator is None
    if owns and not wait:
        raise ValueError("Pass a coordinator when wait is false, and close it yourself")
    active = coordinator or JoinCoordinator(store, joiner, cfg)
    try:
        return active.dispatch(now, wait=wait)
    finally:
        if owns:
            active.close()


def preview_rows(store: StateStore, cfg: AppConfig, now: datetime) -> list[tuple[MeetingRow, str]]:
    rows = store.list_meetings(None)
    labeled = []
    for row in rows:
        if row.meet_opened_at is not None or row.status not in CLAIMABLE_STATUSES | {"missed", "scheduled"}:
            window = row.status
        else:
            window = classify_window(row.start_utc, row.end_utc, now, cfg)
        labeled.append((row, window))
    return labeled


def serve(cfg: AppConfig, store: StateStore, client, joiner, stop_event: threading.Event) -> None:
    recovered = store.recover_abandoned(utc_now())
    for instance_key, status in recovered:
        log_event(
            logger,
            logging.WARNING,
            "recovered an abandoned meeting",
            instance_key=instance_key,
            status=status,
        )
    wake = threading.Event()
    push_server = None
    if cfg.push_enabled:
        from meet_notes.push import PushServer

        push_server = PushServer(cfg.push_listen_host, cfg.push_listen_port, cfg.push_channel_token, wake)
        push_server.start()
        log_event(
            logger,
            logging.INFO,
            "push listener started",
            host=cfg.push_listen_host,
            port=cfg.push_listen_port,
        )
    coordinator = JoinCoordinator(
        store,
        joiner,
        cfg,
        fill_on_finish=True,
        stop_event=stop_event,
    )
    try:
        while not stop_event.is_set():
            if cfg.push_enabled:
                _renew_watch(cfg, store, client, push_server, utc_now())
            try:
                now = utc_now()
                stats = sync_calendar(store, client, cfg, now)
                log_event(
                    logger,
                    logging.INFO,
                    "calendar sync finished",
                    **{key: stats[key] for key in sorted(stats)},
                )
                if not cfg.dry_run:
                    coordinator.dispatch(now, wait=False)
            except Exception as exc:
                log_event(logger, logging.ERROR, "poll failed", error=str(exc)[:2000])
                logger.exception("poll failed")
                try:
                    from meet_notes.notify import notify_calendar_auth

                    notify_calendar_auth(cfg, store, str(exc))
                except Exception:
                    logger.exception("calendar auth notification failed")
            if stop_event.wait(cfg.poll_interval_seconds):
                break
            if wake.is_set():
                wake.clear()
                log_event(logger, logging.INFO, "calendar push received")
    finally:
        stop_event.set()
        coordinator.close()
        if push_server is not None:
            _stop_watch(cfg, store, client)
            push_server.stop()


def _renew_watch(cfg: AppConfig, store: StateStore, client, push_server, now: datetime) -> None:
    if push_server is None:
        return
    existing = store.get_push_channel(cfg.calendar_id)
    expiration = int(existing["expiration_ms"]) if existing and existing["expiration_ms"] else None
    if existing and not should_renew(expiration, now, cfg.push_renew_minutes_before):
        push_server.channel_id = existing["channel_id"]
        return
    channel_id = uuid.uuid4().hex
    expiration_ms = int((now + timedelta(days=6)).timestamp() * 1000)
    body = {
        "id": channel_id,
        "type": "web_hook",
        "address": cfg.push_public_url,
        "token": cfg.push_channel_token,
        "expiration": str(expiration_ms),
    }
    try:
        created = client.watch(cfg.calendar_id, body)
    except Exception as exc:
        log_event(logger, logging.ERROR, "calendar watch failed", error=str(exc)[:2000])
        return
    resource_id = str(created.get("resourceId") or "")
    raw_expiration = created.get("expiration") or expiration_ms
    try:
        expiration_ms = int(raw_expiration)
    except (TypeError, ValueError):
        expiration_ms = int((now + timedelta(days=6)).timestamp() * 1000)
    store.set_push_channel(
        cfg.calendar_id,
        channel_id=str(created.get("id") or channel_id),
        resource_id=resource_id,
        expiration_ms=expiration_ms,
        token=cfg.push_channel_token,
    )
    push_server.channel_id = str(created.get("id") or channel_id)
    log_event(logger, logging.INFO, "calendar watch registered", channel_id=push_server.channel_id)


def _stop_watch(cfg: AppConfig, store: StateStore, client) -> None:
    existing = store.get_push_channel(cfg.calendar_id)
    if not existing:
        return
    try:
        client.stop_channel(existing["channel_id"], existing["resource_id"])
    except Exception as exc:
        log_event(logger, logging.WARNING, "calendar watch stop failed", error=str(exc)[:500])
    store.clear_push_channel(cfg.calendar_id)


def install_stop_signals(stop_event: threading.Event) -> None:
    import signal

    def handle(signum, frame) -> None:
        log_event(logger, logging.INFO, "shutdown requested", signal=signum)
        stop_event.set()

    signal.signal(signal.SIGINT, handle)
    if hasattr(signal, "SIGTERM"):
        try:
            signal.signal(signal.SIGTERM, handle)
        except ValueError:
            # Not the main thread.
            pass
