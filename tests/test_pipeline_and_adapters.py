"""Async queue lifecycle, handler isolation, feed, structured log tests."""

import json
import logging
from decimal import Decimal

import pytest

from arbledger.application.opportunity_engine import OpportunityEngine
from arbledger.application.pipeline import MarketPipeline, PipelineState
from arbledger.application.sliding_window import SlidingWindowQuoteBook
from arbledger.domain.models import PriceQuote
from arbledger.domain.strategy import ArbitrageStrategy, StrategyConfig
from arbledger.infrastructure.logging import JsonLogFormatter, correlation_scope
from arbledger.infrastructure.memory_market import MemoryQuoteFeed
from arbledger.interfaces.cli import run_demo
from conftest import BASE_TIME, make_quote


def build_engine() -> OpportunityEngine:
    return OpportunityEngine(
        SlidingWindowQuoteBook(5),
        ArbitrageStrategy(StrategyConfig(fee_bps=Decimal("0"), min_net_profit=Decimal("0"), max_quote_age_seconds=Decimal("10"))),
        {"EUR/USD": Decimal("100")},
    )


@pytest.mark.asyncio
async def test_pipeline_processes_quotes_and_gracefully_stops():
    seen = []

    async def handler(opportunity):
        seen.append(opportunity.opportunity_id)

    pipeline = MarketPipeline(build_engine(), workers=2, queue_size=1, opportunity_handler=handler)
    await pipeline.start()
    await pipeline.submit(make_quote("a", bid="0.99", ask="1.0"))
    await pipeline.submit(make_quote("b", bid="1.2", ask="1.3"))
    await pipeline.stop()
    await pipeline.stop()
    metrics = pipeline.metrics()
    assert metrics.submitted == 2
    assert metrics.processed == 2
    assert metrics.opportunities == 1
    assert metrics.failures == 0
    assert metrics.queue_depth == 0
    assert metrics.state == "STOPPED"
    assert len(seen) == 1
    assert pipeline.state is PipelineState.STOPPED


@pytest.mark.asyncio
async def test_pipeline_guards_invalid_lifecycle_operations():
    pipeline = MarketPipeline(build_engine())
    with pytest.raises(RuntimeError):
        await pipeline.submit(make_quote())
    await pipeline.stop()
    with pytest.raises(RuntimeError):
        await pipeline.start()
    with pytest.raises(ValueError):
        MarketPipeline(build_engine(), workers=0)
    with pytest.raises(ValueError):
        MarketPipeline(build_engine(), queue_size=0)


@pytest.mark.asyncio
async def test_pipeline_isolates_bad_quote_and_bad_handler():
    async def bad_handler(_opportunity):
        raise RuntimeError("simulated handler failure")

    pipeline = MarketPipeline(build_engine(), workers=1, opportunity_handler=bad_handler)
    await pipeline.start()
    await pipeline.submit(make_quote("a", bid="0.99", ask="1.0", sequence=1))
    # Repeated sequence is rejected by the quote book and does not kill the worker.
    await pipeline.submit(make_quote("a", bid="0.99", ask="1.0", sequence=1))
    await pipeline.submit(make_quote("b", bid="1.2", ask="1.3"))
    await pipeline.stop()
    assert pipeline.metrics().processed == 2
    assert pipeline.metrics().failures == 2
    assert len(pipeline.errors) == 2
    assert any("sequence" in str(error) for error in pipeline.errors)
    assert any("handler failure" in str(error) for error in pipeline.errors)


@pytest.mark.asyncio
async def test_pipeline_drains_feed_and_does_not_require_a_handler():
    feed = MemoryQuoteFeed([make_quote("a"), make_quote("b", bid="1.2", ask="1.3")])
    pipeline = MarketPipeline(build_engine(), workers=1, queue_size=2)
    await pipeline.start()
    async for quote in feed.stream():
        await pipeline.submit(quote)
    await pipeline.stop()
    assert pipeline.metrics().processed == 2
    assert pipeline.metrics().opportunities == 1
    with pytest.raises(ValueError):
        MemoryQuoteFeed([], delay_seconds=-1)


def test_json_formatter_contains_context_and_exception():
    formatter = JsonLogFormatter()
    record = logging.LogRecord("arbledger.test", logging.ERROR, __file__, 10, "broken %s", ("quote",), None)
    with correlation_scope("corr-123"):
        rendered = formatter.format(record)
    payload = json.loads(rendered)
    assert payload["message"] == "broken quote"
    assert payload["correlation_id"] == "corr-123"
    assert payload["level"] == "ERROR"
    exception_record = logging.LogRecord("arbledger.test", logging.ERROR, __file__, 11, "failed", (), None)
    try:
        raise ValueError("test exception")
    except ValueError:
        import sys
        exception_record.exc_info = sys.exc_info()
    assert "test exception" in json.loads(formatter.format(exception_record))["exception"]


def test_quote_model_is_reused_in_feed_type():
    quote = make_quote("a")
    assert isinstance(quote, PriceQuote)
    assert quote.timestamp == BASE_TIME


@pytest.mark.asyncio
async def test_cli_demo_runs_end_to_end(capsys):
    code = await run_demo(workers=1, queue_size=2)
    output = capsys.readouterr().out
    assert code == 0
    assert '"event": "paper_execution"' in output
    assert '"state": "STOPPED"' in output


@pytest.mark.asyncio
async def test_concurrent_stop_calls_wait_for_the_same_worker_shutdown():
    import asyncio

    handler_started = asyncio.Event()
    release_handler = asyncio.Event()

    async def slow_handler(_opportunity):
        handler_started.set()
        await release_handler.wait()

    pipeline = MarketPipeline(build_engine(), workers=1, queue_size=2, opportunity_handler=slow_handler)
    await pipeline.start()
    await pipeline.submit(make_quote("a", bid="0.99", ask="1.0"))
    await pipeline.submit(make_quote("b", bid="1.2", ask="1.3"))
    await handler_started.wait()
    first_stop = asyncio.create_task(pipeline.stop())
    await asyncio.sleep(0)
    assert pipeline.state is PipelineState.STOPPING
    second_stop = asyncio.create_task(pipeline.stop())
    await asyncio.sleep(0)
    release_handler.set()
    await asyncio.gather(first_stop, second_stop)
    assert pipeline.state is PipelineState.STOPPED


def test_json_formatter_includes_optional_context_fields_and_configure_logging():
    formatter = JsonLogFormatter()
    record = logging.LogRecord("arbledger.test", logging.INFO, __file__, 10, "quote received", (), None)
    record.worker_id = 3
    record.venue = "venue_a"
    record.opportunity_id = "opp-123"
    record.event = "quote"
    payload = json.loads(formatter.format(record))
    assert payload["worker_id"] == 3
    assert payload["venue"] == "venue_a"
    assert payload["opportunity_id"] == "opp-123"
    assert payload["event"] == "quote"

    from arbledger.infrastructure.logging import configure_logging
    root = logging.getLogger()
    old_handlers = list(root.handlers)
    old_level = root.level
    try:
        configure_logging(logging.DEBUG)
        assert root.level == logging.DEBUG
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonLogFormatter)
    finally:
        root.handlers[:] = old_handlers
        root.setLevel(old_level)


@pytest.mark.asyncio
async def test_memory_feed_optional_delay_is_supported():
    import asyncio

    feed = MemoryQuoteFeed([make_quote("a"), make_quote("b")], delay_seconds=0.001)
    observed = []
    async for item in feed.stream():
        observed.append(item)
    assert len(observed) == 2


def test_cli_main_valid_and_invalid_arguments(monkeypatch):
    import sys
    from arbledger.interfaces import cli

    async def fake_demo(*, workers: int, queue_size: int) -> int:
        assert workers == 1
        assert queue_size == 2
        return 0

    monkeypatch.setattr(cli, "run_demo", fake_demo)
    monkeypatch.setattr(cli, "configure_logging", lambda _level: None)
    monkeypatch.setattr(sys, "argv", ["arbledger", "--workers", "1", "--queue-size", "2", "--log-level", "DEBUG"])
    with pytest.raises(SystemExit) as success:
        cli.main()
    assert success.value.code == 0

    monkeypatch.setattr(sys, "argv", ["arbledger", "--workers", "0"])
    with pytest.raises(SystemExit) as invalid:
        cli.main()
    assert invalid.value.code == 2


@pytest.mark.asyncio
async def test_pipeline_validates_messages_and_bounds_history_but_keeps_total_metrics():
    pipeline = MarketPipeline(build_engine(), workers=1, queue_size=4, history_limit=1)
    with pytest.raises(ValueError):
        MarketPipeline(build_engine(), history_limit=0)
    await pipeline.start()
    with pytest.raises(TypeError, match="PriceQuote"):
        await pipeline.submit("not a quote")  # type: ignore[arg-type]
    for venue, sequence in (("a", 1), ("b", 1), ("a", 2), ("b", 2)):
        quote = make_quote(
            venue,
            bid="0.99" if venue == "a" else "1.2",
            ask="1.0" if venue == "a" else "1.3",
            sequence=sequence,
        )
        await pipeline.submit(quote)
    await pipeline.stop()
    assert pipeline.metrics().opportunities == 3
    assert len(pipeline.opportunities) == 1
    assert pipeline.metrics().submitted == 4
    assert pipeline.metrics().processed == 4
