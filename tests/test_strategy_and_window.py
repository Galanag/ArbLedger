"""Fee-aware strategy and sliding-window calculator edge cases."""

from datetime import timedelta
from decimal import Decimal

import pytest

from arbledger.application.opportunity_engine import OpportunityEngine
from arbledger.application.sliding_window import SlidingWindowQuoteBook, SlidingWindowSpread
from arbledger.domain.exceptions import OutOfOrderQuoteError, StaleQuoteError, ValidationError
from arbledger.domain.strategy import ArbitrageStrategy, StrategyConfig
from conftest import BASE_TIME, make_quote


def test_strategy_returns_fee_adjusted_opportunity():
    strategy = ArbitrageStrategy(StrategyConfig(fee_bps=Decimal("2"), min_net_profit=Decimal("0.01"), max_quote_age_seconds=Decimal("3")))
    buy = make_quote("alpha", ask="1.0900")
    sell = make_quote("beta", bid="1.1100", ask="1.1200")
    opportunity = strategy.evaluate(buy, sell, Decimal("100"), now=BASE_TIME)
    assert opportunity is not None
    assert opportunity.gross_profit == Decimal("2.0000")
    assert opportunity.net_profit < opportunity.gross_profit
    assert opportunity.edge_bps > 0
    assert opportunity.expires_at > opportunity.observed_at


def test_strategy_rejects_non_profitable_cross_venue_pair():
    strategy = ArbitrageStrategy(StrategyConfig(min_net_profit=Decimal("0")))
    assert strategy.evaluate(make_quote("a", ask="1.101"), make_quote("b", bid="1.100", ask="1.110"), Decimal("1"), now=BASE_TIME) is None


def test_strategy_rejects_same_venue_and_stale_quotes():
    strategy = ArbitrageStrategy(StrategyConfig(max_quote_age_seconds=Decimal("1")))
    quote = make_quote("same")
    assert strategy.evaluate(quote, quote, Decimal("1"), now=BASE_TIME) is None
    assert strategy.evaluate(make_quote("a"), make_quote("b", second=-5, bid="1.2", ask="1.3"), Decimal("1"), now=BASE_TIME) is None
    # The buy-side quote may independently be stale, too.
    assert strategy.evaluate(make_quote("a", second=-5), make_quote("b", bid="1.2", ask="1.3"), Decimal("1"), now=BASE_TIME) is None


def test_strategy_invalid_inputs_and_config_are_rejected():
    with pytest.raises(ValidationError):
        StrategyConfig(fee_bps=Decimal("-1"))
    with pytest.raises(ValidationError):
        StrategyConfig(opportunity_ttl_seconds=Decimal("0"))
    strategy = ArbitrageStrategy()
    with pytest.raises(ValidationError):
        strategy.evaluate(make_quote("a"), make_quote("b", base="GBP", quote="USD"), Decimal("1"), now=BASE_TIME)
    with pytest.raises(ValidationError):
        strategy.evaluate(make_quote("a"), make_quote("b"), Decimal("0"), now=BASE_TIME)
    strict_threshold = ArbitrageStrategy(StrategyConfig(fee_bps=Decimal("100"), min_net_profit=Decimal("10")))
    assert strict_threshold.evaluate(make_quote("a", ask="1.09"), make_quote("b", bid="1.11", ask="1.12"), Decimal("1"), now=BASE_TIME) is None


def test_zero_fee_strategy_can_create_candidate():
    strategy = ArbitrageStrategy(StrategyConfig(fee_bps=Decimal("0"), min_net_profit=Decimal("0")))
    candidate = strategy.evaluate(make_quote("a", bid="0.9", ask="1.0"), make_quote("b", bid="1.1", ask="1.2"), Decimal("1"), now=BASE_TIME)
    assert candidate is not None
    assert candidate.buy_fee == Decimal("0.00")


def test_sliding_spread_stats_and_expiration():
    window = SlidingWindowSpread(5)
    assert window.statistics().count == 0
    assert window.add(BASE_TIME, Decimal("2")).mean == Decimal("2")
    assert window.add(BASE_TIME + timedelta(seconds=1), Decimal("-1")).minimum == Decimal("-1")
    result = window.add(BASE_TIME + timedelta(seconds=7), Decimal("5"))
    assert result.count == 1
    assert result.minimum == result.maximum == result.mean == Decimal("5")


def test_sliding_spread_handles_duplicate_extrema_and_rejects_bad_time():
    window = SlidingWindowSpread(3)
    window.add(BASE_TIME, Decimal("2"))
    window.add(BASE_TIME + timedelta(seconds=1), Decimal("2"))
    result = window.add(BASE_TIME + timedelta(seconds=4), Decimal("1"))
    assert result.count == 2
    assert result.minimum == Decimal("1")
    with pytest.raises(OutOfOrderQuoteError):
        window.add(BASE_TIME, Decimal("1"))
    with pytest.raises(ValidationError):
        window.add(BASE_TIME.replace(tzinfo=None), Decimal("1"))
    with pytest.raises(ValidationError):
        SlidingWindowSpread(0)


def test_quote_book_prunes_window_and_enforces_stream_order():
    book = SlidingWindowQuoteBook(5, max_quotes_per_stream=2)
    book.ingest(make_quote("a", second=0, sequence=1))
    book.ingest(make_quote("a", second=1, sequence=2))
    book.ingest(make_quote("a", second=2, sequence=3))
    assert book.quote_count() == 2
    book.ingest(make_quote("b", second=10, sequence=1))
    assert book.latest_quotes("EUR/USD") == (make_quote("b", second=10, sequence=1),)
    with pytest.raises(StaleQuoteError):
        book.ingest(make_quote("c", second=2, sequence=1))
    with pytest.raises(OutOfOrderQuoteError):
        book.ingest(make_quote("a", second=11, sequence=3))
    with pytest.raises(OutOfOrderQuoteError):
        book.ingest(make_quote("a", second=0, sequence=4))
    assert book.quote_count("MISSING/PAIR") == 0


def test_quote_book_configuration_and_capacity_validation():
    with pytest.raises(ValidationError):
        SlidingWindowQuoteBook(0)
    with pytest.raises(ValidationError):
        SlidingWindowQuoteBook(1, max_quotes_per_stream=0)
    with pytest.raises(ValidationError):
        SlidingWindowQuoteBook(1, max_quotes_per_stream=1.5)


def test_opportunity_engine_compares_only_configured_pairs():
    book = SlidingWindowQuoteBook(5)
    strategy = ArbitrageStrategy(StrategyConfig(fee_bps=Decimal("0"), min_net_profit=Decimal("0")))
    engine = OpportunityEngine(book, strategy, {"EUR/USD": Decimal("10")})
    assert engine.on_quote(make_quote("a", bid="0.9", ask="1.0")) == ()
    result = engine.on_quote(make_quote("b", bid="1.2", ask="1.3"))
    assert len(result) == 1
    assert result[0].buy_quote.venue == "a"
    assert engine.on_quote(make_quote("c", base="GBP", quote="USD", bid="1.2", ask="1.3")) == ()
    with pytest.raises(ValueError):
        OpportunityEngine(book, strategy, {"EUR/USD": Decimal("0")})
    with pytest.raises(ValueError):
        OpportunityEngine(book, strategy, {"EURUSD": Decimal("1")})
    with pytest.raises(ValueError):
        OpportunityEngine(book, strategy, {"USD/USD": Decimal("1")})


def test_sliding_windows_enforce_memory_caps():
    capped_spread = SlidingWindowSpread(60, max_samples=2)
    capped_spread.add(BASE_TIME, Decimal("2"))
    capped_spread.add(BASE_TIME, Decimal("3"))
    result = capped_spread.add(BASE_TIME, Decimal("4"))
    assert result.count == 2
    assert result.minimum == Decimal("3")
    assert result.maximum == Decimal("4")
    assert result.mean == Decimal("3.5")
    with pytest.raises(ValidationError):
        SlidingWindowSpread(1, max_samples=0)

    capped_book = SlidingWindowQuoteBook(60, max_streams=1)
    capped_book.ingest(make_quote("venue_a"))
    with pytest.raises(ValidationError, match="registered quote streams"):
        capped_book.ingest(make_quote("venue_b"))
    with pytest.raises(ValidationError):
        SlidingWindowQuoteBook(1, max_streams=0)


def test_window_apis_reject_non_domain_inputs_cleanly():
    window = SlidingWindowSpread(3)
    with pytest.raises(ValidationError, match="datetime"):
        window.add("not a datetime", Decimal("1"))  # type: ignore[arg-type]
    book = SlidingWindowQuoteBook(3)
    with pytest.raises(ValidationError, match="PriceQuote"):
        book.ingest(object())  # type: ignore[arg-type]
