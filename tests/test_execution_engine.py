"""Paper execution integration tests across quote strategy and ledger."""

from datetime import timedelta
from decimal import Decimal

import pytest

from arbledger.application.execution_engine import ExecutionEngine
from arbledger.domain.exceptions import AccountNotFoundError, InsufficientFundsError, OpportunityExpiredError, ValidationError
from arbledger.domain.ledger import Ledger
from conftest import BASE_TIME, make_opportunity


def funded_executor(*, venue_b_eur: str = "1000", fee_bps: str = "2") -> ExecutionEngine:
    ledger = Ledger()
    executor = ExecutionEngine(ledger, max_execution_age_seconds="10")
    executor.register_venue("venue_a", {"USD": Decimal("10000"), "EUR": Decimal("0")})
    executor.register_venue("venue_b", {"EUR": Decimal(venue_b_eur), "USD": Decimal("0")})
    executor.register_fee_currency("USD")
    return executor


@pytest.mark.asyncio
async def test_execution_posts_two_legs_atomically_and_balances_ledger():
    executor = funded_executor()
    opportunity = make_opportunity(now=BASE_TIME)
    result = await executor.execute(opportunity, idempotency_key="exec-001", now=BASE_TIME)
    assert result.gross_profit == Decimal("2.0000")
    assert result.fees_paid == opportunity.buy_fee + opportunity.sell_fee
    assert result.net_profit == opportunity.net_profit
    assert await executor.ledger.get_balance("wallet:venue_a", "EUR") == Decimal("100")
    assert await executor.ledger.get_balance("wallet:venue_a", "USD") == Decimal("9890.98")
    assert await executor.ledger.get_balance("wallet:venue_b", "EUR") == Decimal("900")
    assert await executor.ledger.get_balance("wallet:venue_b", "USD") == Decimal("110.98")
    assert await executor.ledger.get_balance("fees:USD", "USD") == Decimal("0.04")
    tx = result.transaction
    assert all(value == 0 for value in tx.totals_by_currency().values())


@pytest.mark.asyncio
async def test_execution_idempotent_retry_does_not_double_apply():
    executor = funded_executor()
    opportunity = make_opportunity(now=BASE_TIME)
    first = await executor.execute(opportunity, idempotency_key="same-execution", now=BASE_TIME)
    second = await executor.execute(opportunity, idempotency_key="same-execution", now=BASE_TIME)
    assert not first.idempotent_replay
    assert second.idempotent_replay
    assert first.transaction.transaction_id == second.transaction.transaction_id
    assert await executor.ledger.get_balance("wallet:venue_a", "USD") == Decimal("9890.98")


@pytest.mark.asyncio
async def test_insufficient_sell_inventory_leaves_every_account_unchanged():
    executor = funded_executor(venue_b_eur="10")
    opportunity = make_opportunity(now=BASE_TIME)
    before = await executor.ledger.snapshot()
    with pytest.raises(InsufficientFundsError):
        await executor.execute(opportunity, idempotency_key="low-inventory", now=BASE_TIME)
    assert await executor.ledger.snapshot() == before
    assert await executor.ledger.transactions() == ()


@pytest.mark.asyncio
async def test_execution_rejects_expired_or_malformed_request():
    executor = funded_executor()
    opportunity = make_opportunity(now=BASE_TIME)
    with pytest.raises(OpportunityExpiredError):
        await executor.execute(opportunity, idempotency_key="too-late", now=BASE_TIME + timedelta(seconds=8))
    with pytest.raises(ValidationError):
        await executor.execute(object(), idempotency_key="malformed", now=BASE_TIME)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        await executor.execute(opportunity, idempotency_key="not allowed", now=BASE_TIME)


@pytest.mark.asyncio
async def test_missing_fee_account_and_duplicate_setup_are_detected():
    ledger = Ledger()
    with pytest.raises(ValidationError):
        ExecutionEngine(ledger, max_execution_age_seconds=0)
    executor = ExecutionEngine(ledger)
    executor.register_venue("venue_a", {"USD": Decimal("1000")})
    executor.register_venue("venue_b", {"EUR": Decimal("1000")})
    with pytest.raises(Exception):
        executor.register_venue("venue_a", {"USD": Decimal("2")})
    opportunity = make_opportunity(now=BASE_TIME)
    with pytest.raises(AccountNotFoundError):
        await executor.execute(opportunity, idempotency_key="no-fees", now=BASE_TIME)


@pytest.mark.asyncio
async def test_zero_fee_execution_omits_zero_journal_lines():
    ledger = Ledger()
    executor = ExecutionEngine(ledger)
    executor.register_venue("venue_a", {"USD": Decimal("1000")})
    executor.register_venue("venue_b", {"EUR": Decimal("1000")})
    executor.register_fee_currency("USD")
    opportunity = make_opportunity(now=BASE_TIME, fee_bps="0")
    result = await executor.execute(opportunity, idempotency_key="zero-fee", now=BASE_TIME)
    assert result.fees_paid == Decimal("0.00")
    assert all(entry.amount != 0 for entry in result.transaction.entries)


@pytest.mark.asyncio
async def test_two_concurrent_executions_compete_safely_for_same_inventory():
    executor = funded_executor(venue_b_eur="100")
    opportunity = make_opportunity(now=BASE_TIME)
    import asyncio

    results = await asyncio.gather(
        executor.execute(opportunity, idempotency_key="compete-a", now=BASE_TIME),
        executor.execute(opportunity, idempotency_key="compete-b", now=BASE_TIME),
        return_exceptions=True,
    )
    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, InsufficientFundsError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert await executor.ledger.get_balance("wallet:venue_b", "EUR") == Decimal("0")
    assert len(await executor.ledger.transactions()) == 1
