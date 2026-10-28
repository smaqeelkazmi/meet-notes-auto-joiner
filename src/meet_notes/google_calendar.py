"""Read-only Google Calendar client.

The only OAuth scope is calendar.readonly. That scope can list events and,
when push is enabled, register a watch channel. It cannot edit events.
"""

from __future__ import annotations

import os
from pathlib import Path

from meet_notes.calendar_sync import SyncTokenExpired

CALENDAR_SCOPES = ("https://www.googleapis.com/auth/calendar.readonly",)


class GoogleCalendarClient:
    def __init__(self, credentials_file: Path, token_file: Path) -> None:
        self.credentials_file = credentials_file
        self.token_file = token_file
        self._creds = load_credentials(credentials_file, token_file)
        self._service = _build(self._creds)

    def list_events(
        self,
        *,
        calendar_id: str,
        sync_token: str | None = None,
        time_min: str | None = None,
        time_max: str | None = None,
        page_token: str | None = None,
    ) -> dict:
        kwargs: dict = {
            "calendarId": calendar_id,
            "singleEvents": True,
            "showDeleted": True,
            "maxResults": 250,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        if sync_token:
            kwargs["syncToken"] = sync_token
        else:
            kwargs["orderBy"] = "startTime"
            if time_min:
                kwargs["timeMin"] = time_min
            if time_max:
                kwargs["timeMax"] = time_max
        try:
            return self._service.events().list(**kwargs).execute()
        except Exception as exc:
            status = _http_status(exc)
            if status == 410:
                raise SyncTokenExpired("Calendar sync token expired") from exc
            raise

    def account_email(self) -> str | None:
        """Email of the primary calendar. calendar.readonly can read this. It is not a new scope."""
        try:
            calendar = self._service.calendarList().get(calendarId="primary").execute()
        except Exception:
            return None
        if not isinstance(calendar, dict):
            return None
        ident = calendar.get("id")
        if isinstance(ident, str) and "@" in ident:
            return ident.strip()
        return None

    def watch(self, calendar_id: str, body: dict) -> dict:
        return self._service.events().watch(calendarId=calendar_id, body=body).execute()

    def stop_channel(self, channel_id: str, resource_id: str) -> None:
        self._service.channels().stop(body={"id": channel_id, "resourceId": resource_id}).execute()


def load_credentials(credentials_file: Path, token_file: Path):
    if not token_file.exists():
        raise FileNotFoundError(
            f"No OAuth token at {token_file}. Run `meet-notes auth` on a machine with a browser, "
            "then copy token.json if this host is headless."
        )
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_file(str(token_file), list(CALENDAR_SCOPES))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        save_token(token_file, creds)
    if not creds.valid:
        raise RuntimeError("Calendar token is not valid. Run `meet-notes auth` again.")
    return creds


def run_auth_flow(credentials_file: Path, token_file: Path, *, port: int, open_browser: bool) -> None:
    if not credentials_file.exists():
        raise FileNotFoundError(
            f"OAuth client file not found at {credentials_file}. "
            "Download a Desktop app client from Google Cloud Console. Do not commit it."
        )
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), list(CALENDAR_SCOPES))
    # Bind and advertise 127.0.0.1. Using "localhost" as the redirect host
    # while binding only IPv4 can miss the browser on machines where
    # localhost resolves to IPv6 first.
    creds = flow.run_local_server(
        host="127.0.0.1",
        bind_addr="127.0.0.1",
        port=port,
        open_browser=open_browser,
        access_type="offline",
        prompt="consent",
    )
    save_token(token_file, creds)


def save_token(path: Path, creds) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")
    if os.name != "nt":
        os.chmod(path, 0o600)


def _build(creds):
    from googleapiclient.discovery import build

    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _http_status(exc: Exception) -> int | None:
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None
