"""Pull a canonical https://meet.google.com/... URL out of a Calendar event."""

from __future__ import annotations

import re
import urllib.parse

_MEET_RE = re.compile(r"https://meet\.google\.com/[^\s<>\"']+", re.IGNORECASE)
_REJECT_FIRST = frozenset({"new", "landing", "getalink", "_meet", "tel"})


def canonical_meet_url(raw: str) -> str | None:
    """Return a clean Meet URL, or None if this is not a joinable Meet link."""
    text = raw.strip().rstrip(").,>]\"'")
    if not text:
        return None
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme.lower() != "https":
        return None
    host = (parsed.hostname or "").lower()
    if host != "meet.google.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    first = parts[0].lower()
    if first in _REJECT_FIRST:
        return None
    if first == "lookup":
        if len(parts) < 2 or not parts[1]:
            return None
        return f"https://meet.google.com/lookup/{parts[1]}"
    if not re.fullmatch(r"[A-Za-z0-9-]{3,}", parts[0]):
        return None
    return f"https://meet.google.com/{parts[0]}"


def find_meet_urls(text: str) -> list[str]:
    found: list[str] = []
    for match in _MEET_RE.findall(text or ""):
        url = canonical_meet_url(match)
        if url and url not in found:
            found.append(url)
    return found


def extract_meet_url(item: dict) -> str | None:
    """Prefer the event's conference link over a URL pasted in the description."""
    candidates: list[str] = []
    hangout = item.get("hangoutLink")
    if isinstance(hangout, str):
        candidates.append(hangout)
    conference = item.get("conferenceData") or {}
    if isinstance(conference, dict):
        for entry in conference.get("entryPoints") or []:
            if not isinstance(entry, dict):
                continue
            entry_type = entry.get("entryPointType")
            uri = entry.get("uri")
            if isinstance(uri, str) and entry_type in (None, "video"):
                candidates.append(uri)
    for key in ("location", "description"):
        value = item.get(key)
        if isinstance(value, str):
            candidates.append(value)
    for raw in candidates:
        urls = find_meet_urls(raw) if "meet.google.com" in raw else []
        if not urls:
            url = canonical_meet_url(raw)
            if url:
                urls = [url]
        if urls:
            return urls[0]
    return None
