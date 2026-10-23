"""Command-line demonstration entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal

from arbledger.application.execution_engine import ExecutionEngine
from arbledger.application.opportunity_engine import OpportunityEngine
from arbledger.application.pipeline import MarketPipeline
from arbledger.application.sliding_window import SlidingWindowQuoteBook
from arbledger.domain.ledger import Ledger
from arbledger.domain.models import ArbitrageOpportunity, PriceQuote
from arbledger.domain.strategy import ArbitrageStrategy, StrategyConfig
from arbledger.infrastructure.logging import configure_logging


def build_demo_pipeline(
    *, workers: int = 2, queue_size: int = 8
) -> tuple[MarketPipeline, ExecutionEngine]:
    """Assemble a demo pipeline with pre-funded paper accounts."""
    ledger = Ledger()
    executor = ExecutionEngine(ledger)
    executor.register_venue("venue_a", {"USD": Decimal(100000), "EUR": Decimal(0)})
    executor.register_venue("venue_b", {"EUR": Decimal(10000), "USD": Decimal(0)})
    executor.register_fee_currency("USD")
    strategy = ArbitrageStrategy(StrategyConfig(fee_bps=Decimal(2), min_net_profit=Decimal("0.01")))
    engine = OpportunityEngine(SlidingWindowQuoteBook(5), strategy, {"EUR/USD": Decimal(1000)})

    async def execute_candidate(opportunity: ArbitrageOpportunity) -> None:
        result = await executor.execute(
            opportunity, idempotency_key=f"exec-{opportunity.opportunity_id}"
        )
        print(
            json.dumps(
                {
                    "event": "paper_execution",
                    "opportunity_id": result.opportunity_id,
                    "net_profit": str(result.net_profit),
                    "fees_paid": str(result.fees_paid),
                    "transaction_id": result.transaction.transaction_id,
                }
            )
        )

    return MarketPipeline(
        engine, workers=workers, queue_size=queue_size, opportunity_handler=execute_candidate
    ), executor


async def run_demo(*, workers: int = 2, queue_size: int = 8) -> int:
    """Run a self-contained EUR/USD simulation and report final metrics."""
    pipeline, _executor = build_demo_pipeline(workers=workers, queue_size=queue_size)
    await pipeline.start()
    now = datetime.now(UTC)
    quotes = (
        PriceQuote("venue_a", "EUR", "USD", Decimal("1.0900"), Decimal("1.0910"), now, 1),
        PriceQuote("venue_b", "EUR", "USD", Decimal("1.1040"), Decimal("1.1050"), now, 1),
    )
    try:
        for quote in quotes:
            await pipeline.submit(quote)
    finally:
        await pipeline.stop()
    metrics = pipeline.metrics()
    print(
        json.dumps(
            {
                "event": "pipeline_metrics",
                "submitted": metrics.submitted,
                "processed": metrics.processed,
                "opportunities": metrics.opportunities,
                "failures": metrics.failures,
                "queue_depth": metrics.queue_depth,
                "state": metrics.state,
            }
        )
    )
    return 0 if not pipeline.errors else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="ArbLedger paper-trading arbitrage simulator")
    parser.add_argument("--workers", type=int, default=2, help="async quote workers (default: 2)")
    parser.add_argument(
        "--queue-size", type=int, default=8, help="bounded input queue size (default: 8)"
    )
    parser.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )
    args = parser.parse_args()
    if args.workers < 1 or args.queue_size < 1:
        parser.error("--workers and --queue-size must be positive integers")
    import logging

    configure_logging(getattr(logging, args.log_level))
    raise SystemExit(asyncio.run(run_demo(workers=args.workers, queue_size=args.queue_size)))
