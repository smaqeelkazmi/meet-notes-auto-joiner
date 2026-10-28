"""Local readiness report. This does not call Google."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from meet_notes.config import AppConfig
from meet_notes.models import IN_PROGRESS_STATUSES
from meet_notes.state import StateStore


@dataclass
class HealthReport:
    ready: bool
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


def inspect_health(cfg: AppConfig) -> HealthReport:
    problems: list[str] = []
    warnings: list[str] = []
    details: dict = {
        "calendar_id": cfg.calendar_id,
        "timezone": cfg.timezone,
        "stay_mode": cfg.stay_mode,
        "max_concurrent_joins": cfg.max_concurrent_joins,
        "wait_for_participant": cfg.wait_for_participant,
        "only_organizer_meetings": cfg.only_organizer_meetings,
        "notify_enabled": cfg.notify_enabled,
        "meeting_log": str(cfg.meeting_log) if cfg.meeting_log else None,
        "config_path": str(cfg.config_path) if cfg.config_path else None,
        "credentials_file": str(cfg.credentials_file),
        "token_file": str(cfg.token_file),
        "profile_dir": str(cfg.profile_dir),
        "state_db": str(cfg.state_db),
        "log_dir": str(cfg.log_dir),
        "headless": cfg.headless,
        "push_enabled": cfg.push_enabled,
    }
    _check_credentials(cfg, problems, warnings, details)
    _check_token(cfg, problems, warnings, details)
    _check_profile(cfg, problems, warnings, details)
    _check_database(cfg, problems, warnings, details)
    return HealthReport(ready=not problems, problems=problems, warnings=warnings, details=details)


def _check_credentials(cfg: AppConfig, problems: list[str], warnings: list[str], details: dict) -> None:
    path = cfg.credentials_file
    if path is None or not path.is_file():
        problems.append("OAuth client file is missing. Download a Desktop client JSON from Google Cloud.")
        details["credentials"] = "missing"
        return
    details["credentials"] = "present"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        problems.append("OAuth client file is not valid JSON.")
        return
    if isinstance(data, dict) and "web" in data and "installed" not in data:
        problems.append("OAuth client is a Web client. Create a Desktop app client instead.")
    elif not isinstance(data, dict) or "installed" not in data:
        problems.append("OAuth client file does not look like a Desktop app client.")


def _check_token(cfg: AppConfig, problems: list[str], warnings: list[str], details: dict) -> None:
    path = cfg.token_file
    if path is None or not path.is_file():
        problems.append("Calendar token is missing. Run meet-notes auth.")
        details["token"] = "missing"
        return
    details["token"] = "present"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        problems.append("Calendar token file is not valid JSON.")
        return
    if not isinstance(data, dict) or not data.get("refresh_token"):
        warnings.append("Calendar token has no refresh_token. Run meet-notes auth again and consent.")
    scopes = data.get("scopes") or data.get("scope") or ""
    if isinstance(scopes, list):
        scopes = " ".join(scopes)
    if "calendar.readonly" not in str(scopes) and scopes:
        warnings.append("Token scopes do not mention calendar.readonly. Run meet-notes auth again.")


def _check_profile(cfg: AppConfig, problems: list[str], warnings: list[str], details: dict) -> None:
    profile = cfg.profile_dir
    if profile is None or not profile.exists() or not any(profile.iterdir()):
        problems.append("Browser profile is missing. Run meet-notes login once.")
        details["profile"] = "missing"
        details["google_session_cookie"] = False
        return
    details["profile"] = "present"
    cookie = _session_cookie_present(profile)
    details["google_session_cookie"] = cookie
    if cookie is False:
        warnings.append(
            "No Google session cookie was found in the profile. "
            "Run meet-notes login if Meet later reports auth_required."
        )


def _check_database(cfg: AppConfig, problems: list[str], warnings: list[str], details: dict) -> None:
    path = cfg.state_db
    if path is None:
        problems.append("State database path is not set.")
        return
    try:
        if path.exists():
            store = StateStore(path)
        else:
            details["database"] = "will be created on first run"
            details["last_sync_at"] = None
            details["last_sync_error"] = None
            details["interrupted"] = []
            return
        sync = store.get_sync(cfg.calendar_id)
        details["database"] = "ok"
        details["last_sync_at"] = sync["last_sync_at"] if sync else None
        details["last_sync_error"] = sync["last_error"] if sync else None
        if sync and sync["last_error"]:
            warnings.append(f"Last calendar sync failed: {sync['last_error']}")
        interrupted = [row.instance_key for row in store.list_meetings(["interrupted"])]
        in_progress = [row.instance_key for row in store.list_meetings(sorted(IN_PROGRESS_STATUSES))]
        details["interrupted"] = interrupted
        details["in_progress"] = in_progress
        if interrupted:
            warnings.append(
                f"{len(interrupted)} meeting(s) were interrupted after Meet opened and will not be joined again."
            )
        store.close()
    except sqlite3.Error as exc:
        problems.append(f"State database cannot be read: {exc}")


def _session_cookie_present(profile: Path) -> bool | None:
    candidates = [
        profile / "Default" / "Network" / "Cookies",
        profile / "Default" / "Cookies",
        profile / "Cookies",
    ]
    for candidate in candidates:
        if not candidate.is_file() or candidate.stat().st_size == 0:
            continue
        uri = f"file:{candidate.as_posix()}?mode=ro&immutable=1"
        try:
            conn = sqlite3.connect(uri, uri=True)
        except sqlite3.Error:
            return None
        try:
            rows = conn.execute(
                "SELECT 1 FROM cookies WHERE name IN ('SID', 'SAPISID') LIMIT 1"
            ).fetchone()
        except sqlite3.Error:
            return None
        finally:
            conn.close()
        return bool(rows)
    return False
