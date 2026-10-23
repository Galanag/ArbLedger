"""Bounded asynchronous quote processing pipeline with graceful shutdown."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum, auto

from arbledger.domain.models import ArbitrageOpportunity, PriceQuote

from .opportunity_engine import OpportunityEngine

logger = logging.getLogger(__name__)
_STOP = object()
OpportunityHandler = Callable[[ArbitrageOpportunity], Awaitable[object]]


class PipelineState(Enum):
    """Lifecycle state of a market data pipeline."""

    NEW = auto()
    RUNNING = auto()
    STOPPING = auto()
    STOPPED = auto()


@dataclass(frozen=True, slots=True)
class PipelineMetrics:
    """Point-in-time processing metrics suitable for monitoring adapters."""

    submitted: int
    processed: int
    opportunities: int
    failures: int
    queue_depth: int
    state: str


class MarketPipeline:
    """Process market events through a bounded queue and fixed worker pool.

    Queue size bounds memory and ``submit`` naturally applies backpressure when the
    pipeline cannot keep up. Each queue item is acknowledged exactly once. Expected
    per-message errors are captured and logged so one malformed quote doesn't kill a
    worker; cancellation remains cooperative. ``stop`` drains queued messages before
    joining workers.
    """

    def __init__(
        self,
        engine: OpportunityEngine,
        *,
        workers: int = 4,
        queue_size: int = 1000,
        history_limit: int = 1000,
        opportunity_handler: OpportunityHandler | None = None,
    ) -> None:
        if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
            raise ValueError("workers must be a positive integer")
        if isinstance(queue_size, bool) or not isinstance(queue_size, int) or queue_size < 1:
            raise ValueError("queue_size must be a positive integer")
        if isinstance(history_limit, bool) or not isinstance(history_limit, int) or history_limit < 1:
            raise ValueError("history_limit must be a positive integer")
        self.engine = engine
        self.workers = workers
        self.queue: asyncio.Queue[PriceQuote | object] = asyncio.Queue(maxsize=queue_size)
        self.opportunity_handler = opportunity_handler
        self.history_limit = history_limit
        self.state = PipelineState.NEW
        self._tasks: list[asyncio.Task[None]] = []
        self.submitted = 0
        self.processed = 0
        self._opportunities_detected = 0
        self._failures_total = 0
        # Retain bounded diagnostic history while counters remain cumulative. This
        # prevents a long-running simulation from consuming memory per quote/error.
        self.opportunities: deque[ArbitrageOpportunity] = deque(maxlen=history_limit)
        self.errors: deque[Exception] = deque(maxlen=history_limit)

    async def start(self) -> None:
        """Start the worker pool; the lifecycle is single-use."""
        if self.state is not PipelineState.NEW:
            raise RuntimeError(f"cannot start pipeline in state {self.state.name}")
        self.state = PipelineState.RUNNING
        self._tasks = [asyncio.create_task(self._worker(i), name=f"arbledger-worker-{i}") for i in range(self.workers)]

    async def submit(self, quote: PriceQuote) -> None:
        """Enqueue a quote, waiting when the bounded queue is full."""
        if self.state is not PipelineState.RUNNING:
            raise RuntimeError(f"cannot submit quote in state {self.state.name}")
        if not isinstance(quote, PriceQuote):
            raise TypeError("quote must be a PriceQuote")
        await self.queue.put(quote)
        self.submitted += 1

    async def stop(self) -> None:
        """Gracefully close intake, drain messages, and await all worker tasks."""
        if self.state is PipelineState.STOPPED:
            return
        if self.state is PipelineState.NEW:
            self.state = PipelineState.STOPPED
            return
        if self.state is PipelineState.STOPPING:
            if self._tasks:
                await asyncio.gather(*self._tasks)
            return
        self.state = PipelineState.STOPPING
        for _ in self._tasks:
            await self.queue.put(_STOP)
        await self.queue.join()
        await asyncio.gather(*self._tasks)
        self.state = PipelineState.STOPPED

    def metrics(self) -> PipelineMetrics:
        """Return counters and queue depth without blocking the event loop."""
        return PipelineMetrics(
            submitted=self.submitted,
            processed=self.processed,
            opportunities=self._opportunities_detected,
            failures=self._failures_total,
            queue_depth=self.queue.qsize(),
            state=self.state.name,
        )

    async def _worker(self, worker_id: int) -> None:
        while True:
            item = await self.queue.get()
            try:
                if item is _STOP:
                    return
                assert isinstance(item, PriceQuote)
                try:
                    candidates = self.engine.on_quote(item)
                except Exception as exc:  # isolate malformed/out-of-order messages
                    self.errors.append(exc)
                    self._failures_total += 1
                    logger.exception("quote processing failed", extra={"worker_id": worker_id, "venue": item.venue})
                    continue
                self.processed += 1
                for candidate in candidates:
                    self.opportunities.append(candidate)
                    self._opportunities_detected += 1
                    if self.opportunity_handler is not None:
                        try:
                            await self.opportunity_handler(candidate)
                        except Exception as exc:
                            self.errors.append(exc)
                            self._failures_total += 1
                            logger.exception(
                                "opportunity handler failed",
                                extra={"worker_id": worker_id, "opportunity_id": candidate.opportunity_id},
                            )
            finally:
                self.queue.task_done()
