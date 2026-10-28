"""Decide whether anyone other than Aqeel's account is in the call.

Meet's people button usually includes yourself in the count. Names come
from the roster and from tiles. A people-button headcount is used as well.
If the names only show this account but the button count is higher, the
button wins. Otherwise the highest count wins. A name is "self" when it is
exactly one of `self_display_names`, or when Meet marks it as You.
"""

from __future__ import annotations

import re

from meet_notes.selectors import PEOPLE_BUTTON_SELECTORS, ROSTER_ITEM_SELECTORS, TILE_SELECTORS

_COUNT_RE = re.compile(r"(\d+)")


def is_self_name(name: str, self_names: tuple[str, ...]) -> bool:
    folded = " ".join((name or "").replace("\n", " ").split()).casefold()
    if not folded:
        return True
    if "(you)" in folded or folded in {"you", "you (host)", "you - host"}:
        return True
    cleaned = folded.replace("(you)", "").strip(" -()")
    for raw in self_names:
        candidate = raw.strip().casefold()
        if not candidate or candidate == "you":
            continue
        if folded == candidate or cleaned == candidate:
            return True
    return False


def others_from_people_label(label: str) -> int | None:
    """Meet labels look like 'People - 3 joined' or 'Show everyone 4'. The number includes you."""
    match = _COUNT_RE.search(label or "")
    if not match:
        return None
    return max(int(match.group(1)) - 1, 0)


def count_real_others(names: list[str], self_names: tuple[str, ...]) -> int:
    return sum(1 for name in names if not is_self_name(name, self_names))


def observe_participants(page, self_names: tuple[str, ...]) -> tuple[int, str, dict]:
    """Return (other_people, signal, detail).

    signal is roster, tile, people_button, or unknown. detail is safe to log.
    """
    roster_names = _scrape_names(page, ROSTER_ITEM_SELECTORS)
    tile_names = _scrape_names(page, TILE_SELECTORS)
    people_label = _people_label(page)
    button_others = others_from_people_label(people_label) if people_label else None
    roster_others = count_real_others(roster_names, self_names) if roster_names else None
    tile_others = count_real_others(tile_names, self_names) if tile_names else None

    name_counts = [count for count in (roster_others, tile_others) if count is not None]
    names_say_empty = bool(name_counts) and all(count == 0 for count in name_counts)
    if names_say_empty and button_others is not None and button_others > 0:
        others = button_others
        signal = "people_button"
    else:
        ranked: list[tuple[int, str]] = []
        if roster_others is not None:
            ranked.append((roster_others, "roster"))
        if tile_others is not None:
            ranked.append((tile_others, "tile"))
        if button_others is not None:
            ranked.append((button_others, "people_button"))
        if not ranked:
            others, signal = 0, "unknown"
        else:
            others, signal = max(ranked, key=lambda item: item[0])

    detail = {
        "roster_others": roster_others if roster_others is not None else -1,
        "tile_others": tile_others if tile_others is not None else -1,
        "button_others": button_others if button_others is not None else -1,
        "people_label": (people_label or "")[:120],
        "roster_names": len(roster_names),
        "tile_names": len(tile_names),
    }
    return others, signal, detail


def count_others_on_page(page, self_names: tuple[str, ...]) -> tuple[int, str]:
    """Return (other_people, signal). signal is roster, tile, people_button, or unknown."""
    others, signal, _detail = observe_participants(page, self_names)
    return others, signal


def _people_label(page) -> str | None:
    for selector in PEOPLE_BUTTON_SELECTORS:
        try:
            locator = page.locator(selector)
            count = locator.count()
        except Exception:
            continue
        for index in range(min(count, 5)):
            item = locator.nth(index)
            try:
                if not item.is_visible():
                    continue
            except Exception:
                continue
            parts = []
            for reader in (
                lambda: item.get_attribute("aria-label"),
                lambda: item.get_attribute("data-participant-count"),
                lambda: item.inner_text(),
            ):
                try:
                    value = reader()
                except Exception:
                    value = None
                if value:
                    parts.append(str(value))
            if parts:
                return " ".join(parts)
    return None


def _scrape_names(page, selectors: list[str]) -> list[str]:
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = locator.count()
        except Exception:
            continue
        names: list[str] = []
        visible = 0
        for index in range(min(count, 50)):
            item = locator.nth(index)
            try:
                if not item.is_visible():
                    continue
                visible += 1
                name = _item_name(item)
                if name:
                    names.append(name)
            except Exception:
                continue
        if visible:
            return names
    return []


def _item_name(item) -> str:
    for reader in (
        lambda: item.get_attribute("data-self-name"),
        lambda: item.get_attribute("aria-label"),
        lambda: item.inner_text(),
    ):
        try:
            value = reader()
        except Exception:
            value = None
        text = " ".join((value or "").split())
        if text:
            return text
    return ""
