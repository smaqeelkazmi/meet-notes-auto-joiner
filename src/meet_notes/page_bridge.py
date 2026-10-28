"""Drive one Playwright async page from a worker thread.

Chromium allows one process to open a persistent profile. Concurrent meetings
are separate pages in that process. The sync Meet flow stays synchronous.
Each call is hopped onto the browser thread's event loop, so several worker
threads can run several pages at the same time.
"""

from __future__ import annotations

import asyncio
from typing import Any


class SyncPage:
    def __init__(self, loop: asyncio.AbstractEventLoop, page: Any) -> None:
        self._loop = loop
        self._page = page

    def goto(self, url: str, **kwargs: Any) -> Any:
        return _call(self._loop, self._page.goto(url, **kwargs))

    def locator(self, selector: str) -> SyncLocator:
        locator = _call(self._loop, self._locator(selector))
        return SyncLocator(self._loop, locator)

    def set_default_timeout(self, timeout: float) -> None:
        _call(self._loop, self._timeout(timeout))

    def screenshot(self, **kwargs: Any) -> Any:
        return _call(self._loop, self._page.screenshot(**kwargs))

    def close(self) -> None:
        _call(self._loop, self._page.close())

    @property
    def url(self) -> str:
        return _call(self._loop, self._url())

    async def _locator(self, selector: str) -> Any:
        return self._page.locator(selector)

    async def _timeout(self, timeout: float) -> None:
        self._page.set_default_timeout(timeout)

    async def _url(self) -> str:
        return self._page.url


class SyncLocator:
    def __init__(self, loop: asyncio.AbstractEventLoop, locator: Any) -> None:
        self._loop = loop
        self._locator = locator

    def count(self) -> int:
        return _call(self._loop, self._locator.count())

    def nth(self, index: int) -> SyncLocator:
        locator = _call(self._loop, self._nth(index))
        return SyncLocator(self._loop, locator)

    def is_visible(self) -> bool:
        return _call(self._loop, self._locator.is_visible())

    def click(self, **kwargs: Any) -> None:
        _call(self._loop, self._locator.click(**kwargs))

    def get_attribute(self, name: str) -> str | None:
        return _call(self._loop, self._locator.get_attribute(name))

    def inner_text(self) -> str:
        return _call(self._loop, self._locator.inner_text())

    async def _nth(self, index: int) -> Any:
        return self._locator.nth(index)


def _call(loop: asyncio.AbstractEventLoop, coro: Any) -> Any:
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()
