"""Optional Calendar push channel.

Google POSTs to a public HTTPS URL. The handler only checks the channel
token and wakes the poll loop. It does not read a body and it does not
touch the calendar. Polling still runs, so a missed push is not fatal.
Push is off unless push.enabled is set.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def push_decision(headers, expected_token: str, expected_channel: str | None = None) -> tuple[int, bool]:
    """Return (http_status, should_sync)."""
    supplied = _header(headers, "X-Goog-Channel-Token")
    if supplied != expected_token:
        return 403, False
    channel = _header(headers, "X-Goog-Channel-ID")
    if expected_channel and channel and channel != expected_channel:
        return 200, False
    if _header(headers, "X-Goog-Resource-State") == "sync":
        return 200, False
    return 200, True


def should_renew(expiration_ms: int | None, now: datetime, minutes_before: int) -> bool:
    if not expiration_ms:
        return True
    expiry = datetime.fromtimestamp(expiration_ms / 1000, tz=timezone.utc)
    return expiry <= now + timedelta(minutes=minutes_before)


class PushServer:
    def __init__(self, host: str, port: int, token: str, wake: threading.Event) -> None:
        self.wake = wake
        self.token = token
        self.channel_id: str | None = None
        handler = _handler_factory(self)
        self.httpd = ThreadingHTTPServer((host, port), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="calendar-push", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _header(headers, name: str) -> str:
    if headers is None:
        return ""
    getter = getattr(headers, "get", None)
    if getter is None:
        return ""
    value = getter(name)
    if value is None:
        value = getter(name.lower())
    return "" if value is None else str(value)


def _handler_factory(server: PushServer):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            status, wake = push_decision(self.headers, server.token, server.channel_id)
            if wake:
                server.wake.set()
            self.send_response(status)
            self.end_headers()

        def log_message(self, fmt: str, *args) -> None:
            return

    return Handler
