"""Shared deterministic factories for the ArbLedger test suite."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from arbledger.domain.models import PriceQuote
from arbledger.domain.strategy import ArbitrageStrategy, StrategyConfig

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def base_time() -> datetime:
    return BASE_TIME


def make_quote(
    venue: str = "venue_a",
    *,
    bid: str = "1.0900",
    ask: str = "1.0910",
    second: int = 0,
    sequence: int = 1,
    base: str = "EUR",
    quote: str = "USD",
    timestamp: datetime | None = None,
) -> PriceQuote:
    return PriceQuote(
        venue=venue,
        base_currency=base,
        quote_currency=quote,
        bid=Decimal(bid),
        ask=Decimal(ask),
        timestamp=timestamp or BASE_TIME + timedelta(seconds=second),
        sequence=sequence,
    )


def make_opportunity(*, now: datetime | None = None, fee_bps: str = "2"):
    clock = now or BASE_TIME
    strategy = ArbitrageStrategy(
        StrategyConfig(
            fee_bps=Decimal(fee_bps),
            min_net_profit=Decimal("0.01"),
            max_quote_age_seconds=Decimal("10"),
            opportunity_ttl_seconds=Decimal("5"),
        )
    )
    buy = make_quote("venue_a", bid="1.0890", ask="1.0900", timestamp=clock)
    sell = make_quote("venue_b", bid="1.1100", ask="1.1110", timestamp=clock)
    result = strategy.evaluate(buy, sell, Decimal("100"), now=clock)
    assert result is not None
    return result
