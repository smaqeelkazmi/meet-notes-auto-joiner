"""Start due meetings together, up to a configured cap.

One Chromium profile cannot be opened by two processes. The runner therefore
keeps a single profile open and gives each meeting its own page. This module
does not launch the browser. It only decides which rows start, and it runs
the caller's joiner on a worker thread so a long call does not block the
calendar poll.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Callable

from meet_notes.config import AppConfig
from meet_notes.join_session import finalize_result
from meet_notes.logging_setup import log_event
from meet_notes.models import CLAIMABLE_STATUSES, FlowResult
from meet_notes.scheduler import classify_window
from meet_notes.state import StateStore
from meet_notes.timeutil import utc_now

logger = logging.getLogger("meet_notes.runner")


class JoinCoordinator:
    def __init__(
        self,
        store: StateStore,
        joiner: Callable,
        cfg: AppConfig,
        *,
        fill_on_finish: bool = False,
        stop_event: threading.Event | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.joiner = joiner
        self.cfg = cfg
        self.fill_on_finish = fill_on_finish
        self.stop_event = stop_event
        self._clock = clock or utc_now
        self._lock = threading.Lock()
        self._inflight: dict[str, object] = {}
        self._closed = False
        workers = max(1, cfg.max_concurrent_joins)
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="meet-join")

    def dispatch(self, now: datetime, *, wait: bool = False) -> list[str]:
        """Claim and start due meetings.

        wait=True finishes every meeting that is due at `now`, while still
        only running `max_concurrent_joins` of them at once. A meeting that
        fails before the page opens is not retried until a later dispatch.
        """
        started: list[str] = []
        attempted: set[str] = set()
        while True:
            batch = self._once(now, attempted)
            started.extend(batch)
            if not wait:
                return started
            if self._busy():
                self._wait_idle()
                continue
            if not batch:
                return started

    def wait_idle(self) -> None:
        """Block until every join that has already started has finished."""
        self._wait_idle()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            futures = list(self._inflight.values())
        for future in futures:
            try:
                future.result()  # type: ignore[attr-defined]
            except Exception:
                logger.exception("join thread failed")
        self._executor.shutdown(wait=True)

    def _once(self, now: datetime, attempted: set[str]) -> list[str]:
        if self.stop_event is not None and self.stop_event.is_set():
            self._mark_out_of_window(now)
            return []
        started: list[str] = []
        with self._lock:
            if self._closed:
                return []
            slots = self.cfg.max_concurrent_joins - len(self._inflight)
        if slots > 0:
            for row in self._due_rows(now):
                if slots <= 0:
                    break
                if row.instance_key in attempted:
                    continue
                if self.stop_event is not None and self.stop_event.is_set():
                    break
                if not self.store.claim_join(row.instance_key, now, self.cfg.max_prejoin_attempts):
                    continue
                attempted.add(row.instance_key)
                with self._lock:
                    if self._closed or len(self._inflight) >= self.cfg.max_concurrent_joins:
                        self.store.release_claim(row.instance_key, now)
                        break
                    try:
                        future = self._executor.submit(self._run, row)
                    except RuntimeError:
                        self.store.release_claim(row.instance_key, now)
                        break
                    self._inflight[row.instance_key] = future
                started.append(row.instance_key)
                slots -= 1
                log_event(
                    logger,
                    logging.INFO,
                    "joining meeting",
                    instance_key=row.instance_key,
                    summary=row.summary,
                    start=row.start_utc.isoformat(),
                    concurrent=self._busy_count(),
                    cap=self.cfg.max_concurrent_joins,
                )
        self._mark_out_of_window(now)
        return started

    def _run(self, row) -> None:
        try:
            if self.stop_event is not None and self.stop_event.is_set():
                result = FlowResult(outcome="stopped", error="Shutdown requested")
            else:
                result = self.joiner(row)
            finalize_result(self.store, row.instance_key, result, self._clock())
            self._publish(row.instance_key, email=True)
            log_event(
                logger,
                logging.INFO,
                "meeting finished",
                instance_key=row.instance_key,
                outcome=result.outcome,
                notes_verified=result.notes_verified,
                left=result.left,
            )
        except Exception as exc:
            logger.exception("join crashed")
            self.store.record_exception(row.instance_key, exc, self._clock())
            self._publish(row.instance_key, email=True)
        finally:
            with self._lock:
                self._inflight.pop(row.instance_key, None)
                closed = self._closed
            # Skip this key so a pre-join failure waits for the next poll
            # instead of burning every retry in a tight loop. Other meetings
            # that were waiting on the cap can start now.
            if self.fill_on_finish and not closed and not self._stopping():
                try:
                    self._once(self._clock(), {row.instance_key})
                except Exception:
                    logger.exception("could not start a waiting meeting")

    def _due_rows(self, now: datetime):
        with self._lock:
            busy = set(self._inflight)
        rows = [
            row
            for row in self.store.list_meetings(sorted(CLAIMABLE_STATUSES))
            if row.meet_opened_at is None
            and row.meet_url
            and row.instance_key not in busy
            and classify_window(row.start_utc, row.end_utc, now, self.cfg) == "due"
        ]
        rows.sort(key=lambda row: (row.start_utc, row.instance_key))
        return rows

    def _mark_out_of_window(self, now: datetime) -> None:
        for row in self.store.list_meetings(sorted(CLAIMABLE_STATUSES)):
            if row.meet_opened_at is not None:
                continue
            kind = classify_window(row.start_utc, row.end_utc, now, self.cfg)
            if kind in {"missed", "ended"}:
                self.store.mark(
                    row.instance_key,
                    status="missed",
                    outcome="missed",
                    error="The join window passed before the bot opened the meeting.",
                    updated_at=now,
                )
                self._publish(row.instance_key, email=False)

    def _publish(self, instance_key: str, *, email: bool) -> None:
        from meet_notes.meeting_log import publish_attempt

        row = self.store.get(instance_key)
        if row is None or row.status in {"scheduled", "joining"}:
            return
        publish_attempt(self.cfg, self.store, instance_key, self._clock(), email=email)

    def _stopping(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()

    def _busy(self) -> bool:
        return self._busy_count() > 0

    def _busy_count(self) -> int:
        with self._lock:
            return len(self._inflight)

    def _wait_idle(self) -> None:
        while True:
            with self._lock:
                futures = list(self._inflight.values())
            if not futures:
                return
            for future in futures:
                try:
                    future.result()  # type: ignore[attr-defined]
                except Exception:
                    logger.exception("join thread failed")
