"""Small reproducible throughput benchmark for the in-memory quote pipeline.

Run from the repository root with:
    PYTHONPATH=src python benchmarks/benchmark_simulation.py --quotes 100000 --workers 4

This is an observational benchmark, not an SLA claim. For production capacity planning,
benchmark on the target CPU, Python build, data distribution, and persistence adapter.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from arbledger.application.opportunity_engine import OpportunityEngine
from arbledger.application.pipeline import MarketPipeline
from arbledger.application.sliding_window import SlidingWindowQuoteBook
from arbledger.domain.models import PriceQuote
from arbledger.domain.strategy import ArbitrageStrategy, StrategyConfig


async def benchmark(count: int, workers: int) -> None:
    start_time = datetime.now(timezone.utc)
    engine = OpportunityEngine(
        SlidingWindowQuoteBook(10, max_quotes_per_stream=250),
        ArbitrageStrategy(StrategyConfig(fee_bps=Decimal("1"), min_net_profit=Decimal("0"))),
        {"EUR/USD": Decimal("1000")},
    )
    pipeline = MarketPipeline(engine, workers=workers, queue_size=2000)
    await pipeline.start()
    started = time.perf_counter()
    try:
        for sequence in range(1, count + 1):
            venue = "venue_a" if sequence % 2 else "venue_b"
            offset = sequence // 2
            timestamp = start_time + timedelta(microseconds=offset)
            if venue == "venue_a":
                quote = PriceQuote(venue, "EUR", "USD", Decimal("1.0900"), Decimal("1.0910"), timestamp, sequence)
            else:
                quote = PriceQuote(venue, "EUR", "USD", Decimal("1.1040"), Decimal("1.1050"), timestamp, sequence)
            await pipeline.submit(quote)
    finally:
        await pipeline.stop()
    elapsed = time.perf_counter() - started
    print({
        "quotes": count,
        "workers": workers,
        "elapsed_seconds": round(elapsed, 4),
        "quotes_per_second": round(count / elapsed, 1) if elapsed else None,
        "opportunities": pipeline.metrics().opportunities,
        "failures": pipeline.metrics().failures,
    })


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quotes", type=int, default=10000)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.quotes < 1 or args.workers < 1:
        parser.error("--quotes and --workers must be positive")
    asyncio.run(benchmark(args.quotes, args.workers))


if __name__ == "__main__":
    main()
