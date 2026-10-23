"""Deterministic in-memory quote source used by demos and tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable

from arbledger.domain.models import PriceQuote


class MemoryQuoteFeed:
    """Yield a predefined quote sequence asynchronously.

    ``delay_seconds`` can be used to exercise queue backpressure and worker shutdown
    in local simulations. Set it to zero for deterministic fast replay.
    """

    def __init__(self, quotes: Iterable[PriceQuote], *, delay_seconds: float = 0.0) -> None:
        if delay_seconds < 0:
            raise ValueError("delay_seconds cannot be negative")
        self._quotes = tuple(quotes)
        self.delay_seconds = delay_seconds

    async def stream(self) -> AsyncIterator[PriceQuote]:
        for quote in self._quotes:
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            yield quote
