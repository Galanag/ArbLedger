"""Time-window data structures for quotes and cross-venue spread statistics."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Deque

from arbledger.domain.exceptions import OutOfOrderQuoteError, StaleQuoteError, ValidationError
from arbledger.domain.models import PriceQuote
from arbledger.domain.validation import decimal_value


@dataclass(frozen=True, slots=True)
class SpreadStatistics:
    """Summary statistics for spread observations remaining inside a time window."""

    count: int
    minimum: Decimal | None
    maximum: Decimal | None
    mean: Decimal | None


class SlidingWindowSpread:
    """Maintain exact running min/max/mean for a time-windowed spread stream.

    The implementation combines a FIFO sample deque with two monotonic deques for
    min/max. Each observation is inserted and removed at most once, yielding O(1)
    amortized update cost and O(W) memory for W active observations.
    """

    def __init__(self, window_seconds: Decimal | str | int, *, max_samples: int = 10000) -> None:
        window = decimal_value(window_seconds, field="window_seconds")
        if window <= 0:
            raise ValidationError("window_seconds must be greater than zero")
        if isinstance(max_samples, bool) or not isinstance(max_samples, int) or max_samples < 1:
            raise ValidationError("max_samples must be a positive integer")
        self._max_samples = max_samples
        self._window = timedelta(seconds=float(window))
        self._samples: Deque[tuple[datetime, Decimal]] = deque()
        self._mins: Deque[tuple[datetime, Decimal]] = deque()
        self._maxs: Deque[tuple[datetime, Decimal]] = deque()
        self._total = Decimal("0")
        self._last_timestamp: datetime | None = None

    def add(self, timestamp: datetime, spread: Decimal) -> SpreadStatistics:
        """Add a timestamped difference and return current statistics.

        Timestamps must be non-decreasing. Observations strictly earlier than the
        cutoff are expired; those exactly at the cutoff remain in the window.
        """
        if not isinstance(timestamp, datetime):
            raise ValidationError("spread timestamp must be a datetime")
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValidationError("spread timestamp must be timezone-aware")
        timestamp = timestamp.astimezone(timezone.utc)
        value = decimal_value(spread, field="spread")
        if self._last_timestamp is not None and timestamp < self._last_timestamp:
            raise OutOfOrderQuoteError("spread observation timestamp moved backwards")
        self._last_timestamp = timestamp
        cutoff = timestamp - self._window
        while self._samples and self._samples[0][0] < cutoff:
            self._evict_oldest()
        self._samples.append((timestamp, value))
        self._total += value
        while self._mins and self._mins[-1][1] > value:
            self._mins.pop()
        self._mins.append((timestamp, value))
        while self._maxs and self._maxs[-1][1] < value:
            self._maxs.pop()
        self._maxs.append((timestamp, value))
        while len(self._samples) > self._max_samples:
            self._evict_oldest()
        return self.statistics()

    def _evict_oldest(self) -> None:
        old_time, old_value = self._samples.popleft()
        self._total -= old_value
        if self._mins and self._mins[0] == (old_time, old_value):
            self._mins.popleft()
        if self._maxs and self._maxs[0] == (old_time, old_value):
            self._maxs.popleft()

    def statistics(self) -> SpreadStatistics:
        """Return current statistics without mutating the window."""
        count = len(self._samples)
        if count == 0:
            return SpreadStatistics(0, None, None, None)
        return SpreadStatistics(
            count=count,
            minimum=self._mins[0][1],
            maximum=self._maxs[0][1],
            mean=self._total / Decimal(count),
        )


class SlidingWindowQuoteBook:
    """Bounded per-pair/per-venue quote windows with stale and replay rejection.

    A monotonically increasing global watermark allows modest cross-venue event-time
    disorder while discarding records outside the window. Within one venue/pair stream,
    sequence numbers must strictly increase and timestamps may not regress.
    """

    def __init__(
        self,
        window_seconds: Decimal | str | int,
        *,
        max_quotes_per_stream: int = 1000,
        max_streams: int = 1000,
    ) -> None:
        window = decimal_value(window_seconds, field="window_seconds")
        if window <= 0:
            raise ValidationError("window_seconds must be greater than zero")
        if isinstance(max_quotes_per_stream, bool) or not isinstance(max_quotes_per_stream, int):
            raise ValidationError("max_quotes_per_stream must be an integer")
        if max_quotes_per_stream < 1:
            raise ValidationError("max_quotes_per_stream must be at least 1")
        if isinstance(max_streams, bool) or not isinstance(max_streams, int) or max_streams < 1:
            raise ValidationError("max_streams must be a positive integer")
        self._window = timedelta(seconds=float(window))
        self._max_quotes = max_quotes_per_stream
        self._max_streams = max_streams
        self._streams: dict[tuple[str, str], Deque[PriceQuote]] = {}
        self._last_seen: dict[tuple[str, str], PriceQuote] = {}
        self._watermark: datetime | None = None

    def ingest(self, quote: PriceQuote) -> None:
        """Validate stream ordering, append a quote, and evict expired observations."""
        if not isinstance(quote, PriceQuote):
            raise ValidationError("quote must be a PriceQuote")
        key = (quote.pair, quote.venue)
        previous = self._last_seen.get(key)
        if previous is not None:
            if quote.sequence <= previous.sequence:
                raise OutOfOrderQuoteError(
                    f"sequence for {quote.venue}/{quote.pair} must increase beyond {previous.sequence}"
                )
            if quote.timestamp < previous.timestamp:
                raise OutOfOrderQuoteError(f"timestamp for {quote.venue}/{quote.pair} moved backwards")

        candidate_watermark = max(self._watermark, quote.timestamp) if self._watermark else quote.timestamp
        if quote.timestamp < candidate_watermark - self._window:
            raise StaleQuoteError(f"quote {quote.venue}/{quote.pair} is outside the active window")

        stream = self._streams.get(key)
        if stream is None:
            if len(self._streams) >= self._max_streams:
                raise ValidationError("maximum registered quote streams reached")
            stream = self._streams.setdefault(key, deque())
        stream.append(quote)
        self._last_seen[key] = quote
        self._watermark = candidate_watermark
        self._prune(candidate_watermark)
        while len(stream) > self._max_quotes:
            stream.popleft()

    def latest_quotes(self, pair: str) -> tuple[PriceQuote, ...]:
        """Return at most one current quote per venue for ``pair``, sorted by venue."""
        return tuple(
            stream[-1]
            for (stream_pair, _venue), stream in sorted(self._streams.items())
            if stream_pair == pair and stream
        )

    def quote_count(self, pair: str | None = None) -> int:
        """Count quote records currently retained by the window."""
        return sum(
            len(stream)
            for (stream_pair, _venue), stream in self._streams.items()
            if pair is None or pair == stream_pair
        )

    def _prune(self, watermark: datetime) -> None:
        cutoff = watermark - self._window
        for stream in self._streams.values():
            while stream and stream[0].timestamp < cutoff:
                stream.popleft()
