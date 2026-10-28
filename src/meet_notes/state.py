"""SQLite run history and the one-join-per-meeting claim.

A meeting instance can be claimed only while Meet has not been opened for it.
Opening the Meet link sets meet_opened_at. After that the automatic loop will
not join that instance again, including across restarts.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import fields
from datetime import datetime
from pathlib import Path

from meet_notes.models import (
    ABSENCE_WATCH_STATUSES,
    CLAIMABLE_STATUSES,
    IN_PROGRESS_STATUSES,
    FlowResult,
    MeetingRow,
    ParsedEvent,
    final_status,
)
from meet_notes.timeutil import dump_dt, load_dt, utc_now

_DATETIME_FIELDS = frozenset(
    {
        "start_utc",
        "end_utc",
        "join_claimed_at",
        "meet_opened_at",
        "joined_at",
        "others_joined_at",
        "notes_started_at",
        "notes_verified_at",
        "left_at",
        "created_at",
        "updated_at",
    }
)

_MUTABLE = frozenset(
    {
        "summary",
        "meet_url",
        "start_utc",
        "end_utc",
        "status",
        "join_claimed_at",
        "meet_opened_at",
        "joined_at",
        "others_joined_at",
        "notes_started_at",
        "notes_verified_at",
        "notes_verified",
        "notes_already_on",
        "verification_signal",
        "left_at",
        "outcome",
        "error",
        "attempt_count",
        "absent_polls",
        "recurring_event_id",
        "ical_uid",
        "event_id",
        "reason",
        "updated_at",
    }
)

_BOOL_FIELDS = frozenset({"notes_verified", "notes_already_on"})


def _serialized(method):
    """Hold the store lock for the whole call, including multi-statement claims."""

    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper


class StateStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        # One connection is shared by the poll thread and the join threads.
        self._conn = sqlite3.connect(
            path,
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    @_serialized
    def close(self) -> None:
        self._conn.close()

    def _migrate(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meetings (
                instance_key TEXT PRIMARY KEY,
                calendar_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                recurring_event_id TEXT,
                ical_uid TEXT,
                summary TEXT NOT NULL,
                meet_url TEXT,
                start_utc TEXT NOT NULL,
                end_utc TEXT NOT NULL,
                status TEXT NOT NULL,
                join_claimed_at TEXT,
                meet_opened_at TEXT,
                joined_at TEXT,
                others_joined_at TEXT,
                notes_started_at TEXT,
                notes_verified_at TEXT,
                notes_verified INTEGER,
                notes_already_on INTEGER NOT NULL DEFAULT 0,
                verification_signal TEXT,
                left_at TEXT,
                outcome TEXT,
                error TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                absent_polls INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS meetings_status_start
                ON meetings (status, start_utc);
            CREATE TABLE IF NOT EXISTS sync_state (
                calendar_id TEXT PRIMARY KEY,
                sync_token TEXT,
                last_sync_at TEXT,
                last_error TEXT
            );
            CREATE TABLE IF NOT EXISTS push_channels (
                calendar_id TEXT PRIMARY KEY,
                channel_id TEXT,
                resource_id TEXT,
                expiration_ms INTEGER,
                token TEXT
            );
            """
        )
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(meetings)")}
        if "reason" not in columns:
            self._conn.execute("ALTER TABLE meetings ADD COLUMN reason TEXT")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS attempt_publications (
                instance_key TEXT NOT NULL,
                attempt_count INTEGER NOT NULL,
                outcome TEXT NOT NULL,
                logged_at TEXT NOT NULL,
                PRIMARY KEY (instance_key, attempt_count, outcome)
            );
            CREATE TABLE IF NOT EXISTS notify_dedup (
                dedup_key TEXT PRIMARY KEY,
                sent_at TEXT NOT NULL
            );
            """
        )

    @_serialized
    def insert_scheduled(
        self,
        event: ParsedEvent,
        calendar_id: str,
        now: datetime,
    ) -> None:
        if event.start_utc is None or event.end_utc is None:
            raise ValueError("scheduled meetings need a start and end")
        stamp = dump_dt(now)
        self._conn.execute(
            """
            INSERT INTO meetings (
                instance_key, calendar_id, event_id, recurring_event_id, ical_uid,
                summary, meet_url, start_utc, end_utc, status,
                notes_already_on, attempt_count, absent_polls, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', 0, 0, 0, ?, ?)
            """,
            (
                event.instance_key,
                calendar_id,
                event.event_id,
                event.recurring_event_id,
                event.ical_uid,
                event.summary,
                event.meet_url,
                dump_dt(event.start_utc),
                dump_dt(event.end_utc),
                stamp,
                stamp,
            ),
        )

    @_serialized
    def get(self, instance_key: str) -> MeetingRow | None:
        row = self._conn.execute(
            "SELECT * FROM meetings WHERE instance_key = ?",
            (instance_key,),
        ).fetchone()
        return _meeting_from_row(row) if row else None

    @_serialized
    def list_meetings(self, statuses: list[str] | None = None) -> list[MeetingRow]:
        if statuses is None:
            rows = self._conn.execute(
                "SELECT * FROM meetings ORDER BY start_utc ASC"
            ).fetchall()
        elif not statuses:
            return []
        else:
            marks = ",".join("?" for _ in statuses)
            rows = self._conn.execute(
                f"SELECT * FROM meetings WHERE status IN ({marks}) ORDER BY start_utc ASC",
                tuple(statuses),
            ).fetchall()
        return [_meeting_from_row(row) for row in rows]

    @_serialized
    def history(self, limit: int) -> list[MeetingRow]:
        rows = self._conn.execute(
            "SELECT * FROM meetings ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_meeting_from_row(row) for row in rows]

    @_serialized
    def mark(self, instance_key: str, **fields: object) -> None:
        stamp = fields.pop("updated_at", None)
        if not isinstance(stamp, datetime):
            stamp = utc_now()
        unknown = set(fields) - _MUTABLE
        if unknown:
            raise ValueError(f"Cannot update columns: {sorted(unknown)}")
        if not fields:
            return
        assignments = []
        values: list[object] = []
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            values.append(_adapt(value))
        assignments.append("updated_at = ?")
        values.append(dump_dt(stamp))
        values.append(instance_key)
        self._conn.execute(
            f"UPDATE meetings SET {', '.join(assignments)} WHERE instance_key = ?",
            values,
        )

    @_serialized
    def claim_join(self, instance_key: str, now: datetime, max_attempts: int) -> bool:
        """Return True when this process wins the one automatic join."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                """
                SELECT status, meet_opened_at, attempt_count
                FROM meetings WHERE instance_key = ?
                """,
                (instance_key,),
            ).fetchone()
            if row is None or row["meet_opened_at"] is not None:
                self._conn.execute("ROLLBACK")
                return False
            if row["status"] not in CLAIMABLE_STATUSES or row["attempt_count"] >= max_attempts:
                self._conn.execute("ROLLBACK")
                return False
            stamp = dump_dt(now)
            self._conn.execute(
                """
                UPDATE meetings
                SET status = 'joining',
                    join_claimed_at = ?,
                    attempt_count = attempt_count + 1,
                    updated_at = ?,
                    error = NULL
                WHERE instance_key = ?
                """,
                (stamp, stamp, instance_key),
            )
            self._conn.execute("COMMIT")
            return True
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    @_serialized
    def release_claim(self, instance_key: str, now: datetime) -> bool:
        """Give back a claim that never opened Meet, so a restart can join once."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                """
                SELECT status, meet_opened_at, attempt_count
                FROM meetings WHERE instance_key = ?
                """,
                (instance_key,),
            ).fetchone()
            if (
                row is None
                or row["meet_opened_at"] is not None
                or row["status"] != "joining"
            ):
                self._conn.execute("ROLLBACK")
                return False
            attempts = max(0, int(row["attempt_count"]) - 1)
            stamp = dump_dt(now)
            self._conn.execute(
                """
                UPDATE meetings
                SET status = 'scheduled',
                    join_claimed_at = NULL,
                    attempt_count = ?,
                    updated_at = ?
                WHERE instance_key = ?
                """,
                (attempts, stamp, instance_key),
            )
            self._conn.execute("COMMIT")
            return True
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    @_serialized
    def recover_abandoned(self, now: datetime) -> list[tuple[str, str]]:
        """Restart safety: an in-progress row is never claimed again if Meet opened."""
        changed: list[tuple[str, str]] = []
        for row in self.list_meetings(sorted(IN_PROGRESS_STATUSES)):
            if row.meet_opened_at is not None:
                new_status = "interrupted"
                error = "Process restarted after the Meet link was opened. Not joining again."
            else:
                new_status = "failed_before_join"
                error = "Process restarted before the Meet link opened."
            self.mark(
                row.instance_key,
                status=new_status,
                outcome=new_status,
                error=error,
                updated_at=now,
            )
            changed.append((row.instance_key, new_status))
        return changed

    @_serialized
    def record_exception(self, instance_key: str, exc: BaseException, now: datetime) -> None:
        row = self.get(instance_key)
        opened = row is not None and row.meet_opened_at is not None
        status = "failed" if opened else "failed_before_join"
        message = str(exc).strip() or exc.__class__.__name__
        self.mark(
            instance_key,
            status=status,
            outcome=status,
            error=message[:2000],
            updated_at=now,
        )

    @_serialized
    def save_flow_result(self, instance_key: str, result: FlowResult, now: datetime) -> None:
        row = self.get(instance_key)
        status = final_status(result)
        fields: dict[str, object] = {
            "status": status,
            "outcome": result.outcome,
            "notes_verified": bool(result.notes_verified),
            "notes_already_on": bool(result.notes_already_on),
            "verification_signal": result.verification_signal,
            "error": (result.error or None),
            "updated_at": now,
        }
        if row is not None:
            if result.joined and row.joined_at is None:
                fields["joined_at"] = now
            if result.others_seen and row.others_joined_at is None:
                fields["others_joined_at"] = now
            if (result.notes_clicked or result.notes_verified) and row.notes_started_at is None:
                fields["notes_started_at"] = now
            if result.notes_verified and row.notes_verified_at is None:
                fields["notes_verified_at"] = now
            if result.left and row.left_at is None:
                fields["left_at"] = now
            if result.opened and row.meet_opened_at is None:
                fields["meet_opened_at"] = now
        self.mark(instance_key, **fields)

    @_serialized
    def requeue(self, instance_key: str, now: datetime) -> bool:
        """Manual override. The automatic loop never calls this."""
        row = self.get(instance_key)
        if row is None:
            return False
        self.mark(
            instance_key,
            status="scheduled",
            outcome=None,
            error="Manually requeued",
            meet_opened_at=None,
            join_claimed_at=None,
            joined_at=None,
            others_joined_at=None,
            notes_started_at=None,
            notes_verified_at=None,
            notes_verified=None,
            notes_already_on=False,
            verification_signal=None,
            left_at=None,
            reason=None,
            attempt_count=0,
            absent_polls=0,
            updated_at=now,
        )
        return True

    @_serialized
    def set_sync(
        self,
        calendar_id: str,
        sync_token: str | None,
        *,
        error: str | None,
        when: datetime,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO sync_state (calendar_id, sync_token, last_sync_at, last_error)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(calendar_id) DO UPDATE SET
                sync_token = excluded.sync_token,
                last_sync_at = excluded.last_sync_at,
                last_error = excluded.last_error
            """,
            (calendar_id, sync_token, dump_dt(when), error),
        )

    @_serialized
    def get_sync(self, calendar_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM sync_state WHERE calendar_id = ?",
            (calendar_id,),
        ).fetchone()

    @_serialized
    def set_push_channel(
        self,
        calendar_id: str,
        *,
        channel_id: str,
        resource_id: str,
        expiration_ms: int,
        token: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO push_channels (
                calendar_id, channel_id, resource_id, expiration_ms, token
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(calendar_id) DO UPDATE SET
                channel_id = excluded.channel_id,
                resource_id = excluded.resource_id,
                expiration_ms = excluded.expiration_ms,
                token = excluded.token
            """,
            (calendar_id, channel_id, resource_id, expiration_ms, token),
        )

    @_serialized
    def get_push_channel(self, calendar_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM push_channels WHERE calendar_id = ?",
            (calendar_id,),
        ).fetchone()

    @_serialized
    def claim_publication(self, instance_key: str, attempt_count: int, outcome: str) -> bool:
        """Return True the first time this attempt outcome is written to the meeting log."""
        cursor = self._conn.execute(
            """
            INSERT OR IGNORE INTO attempt_publications (
                instance_key, attempt_count, outcome, logged_at
            ) VALUES (?, ?, ?, ?)
            """,
            (instance_key, int(attempt_count), outcome, dump_dt(utc_now())),
        )
        return cursor.rowcount == 1

    @_serialized
    def claim_notification(self, key: str) -> bool:
        """Return True the first time a deduped hard-failure email is claimed."""
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO notify_dedup (dedup_key, sent_at) VALUES (?, ?)",
            (key, dump_dt(utc_now())),
        )
        return cursor.rowcount == 1

    @_serialized
    def forget_notification(self, key: str) -> None:
        self._conn.execute("DELETE FROM notify_dedup WHERE dedup_key = ?", (key,))

    @_serialized
    def clear_push_channel(self, calendar_id: str) -> None:
        self._conn.execute(
            "DELETE FROM push_channels WHERE calendar_id = ?",
            (calendar_id,),
        )


def absence_statuses() -> list[str]:
    return sorted(ABSENCE_WATCH_STATUSES)


def _adapt(value: object) -> object:
    if isinstance(value, datetime):
        return dump_dt(value)
    if isinstance(value, bool):
        return int(value)
    return value


def _meeting_from_row(row: sqlite3.Row) -> MeetingRow:
    data = dict(row)
    for key in _DATETIME_FIELDS:
        if data.get(key):
            data[key] = load_dt(data[key])
        else:
            data[key] = None
    if data.get("notes_verified") is not None:
        data["notes_verified"] = bool(data["notes_verified"])
    data["notes_already_on"] = bool(data.get("notes_already_on") or 0)
    data["attempt_count"] = int(data["attempt_count"])
    data["absent_polls"] = int(data["absent_polls"])
    data["reason"] = data.get("reason") or None
    allowed = {item.name for item in fields(MeetingRow)}
    return MeetingRow(**{key: value for key, value in data.items() if key in allowed})
