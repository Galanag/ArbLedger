"""Journal balancing, idempotency, and concurrency safety tests."""

import asyncio
from datetime import datetime
from decimal import Decimal

import pytest

from arbledger.domain.exceptions import (
    AccountNotFoundError,
    DuplicateAccountError,
    IdempotencyConflictError,
    InsufficientFundsError,
    LedgerInvariantError,
    ValidationError,
)
from arbledger.domain.ledger import Account, Ledger
from arbledger.domain.models import Posting
from conftest import BASE_TIME


def basic_ledger() -> Ledger:
    ledger = Ledger()
    ledger.add_account(Account("cash:main", {"USD": Decimal("100.00")}))
    ledger.add_account(Account("clearing:bank", allow_negative=True))
    return ledger


@pytest.mark.asyncio
async def test_balanced_transaction_updates_balances_and_journal():
    ledger = basic_ledger()
    result = await ledger.post_transaction(
        "key-1",
        [Posting("cash:main", "USD", Decimal("-10")), Posting("clearing:bank", "USD", Decimal("10"))],
        reference="payment-1",
        created_at=BASE_TIME,
    )
    assert result.replayed is False
    assert await ledger.get_balance("cash:main", "USD") == Decimal("90.00")
    assert await ledger.get_balance("clearing:bank", "USD") == Decimal("10")
    assert result.transaction.totals_by_currency() == {"USD": Decimal("0")}
    assert (await ledger.transactions()) == (result.transaction,)
    assert result.transaction.created_at == BASE_TIME


@pytest.mark.asyncio
async def test_unbalanced_journal_does_not_mutate_any_account():
    ledger = basic_ledger()
    with pytest.raises(LedgerInvariantError, match="at least one"):
        await ledger.post_transaction("empty", [], reference="empty-ref")
    with pytest.raises(ValidationError, match="Posting instances"):
        await ledger.post_transaction("not-posting", [object()], reference="bad-line")  # type: ignore[list-item]
    with pytest.raises(LedgerInvariantError, match="not balanced"):
        await ledger.post_transaction(
            "key-1", [Posting("cash:main", "USD", Decimal("-10"))], reference="payment-1"
        )
    assert await ledger.get_balance("cash:main", "USD") == Decimal("100.00")
    assert await ledger.transactions() == ()


@pytest.mark.asyncio
async def test_insufficient_funds_is_atomic_for_multiple_accounts():
    ledger = basic_ledger()
    ledger.add_account(Account("cash:secondary", {"USD": Decimal("1")}))
    with pytest.raises(InsufficientFundsError):
        await ledger.post_transaction(
            "key-1",
            [
                Posting("cash:main", "USD", Decimal("-50")),
                Posting("cash:secondary", "USD", Decimal("-5")),
                Posting("clearing:bank", "USD", Decimal("55")),
            ],
            reference="transfer-1",
        )
    assert await ledger.get_balance("cash:main", "USD") == Decimal("100.00")
    assert await ledger.get_balance("cash:secondary", "USD") == Decimal("1")
    assert await ledger.get_balance("clearing:bank", "USD") == Decimal("0")


@pytest.mark.asyncio
async def test_idempotency_replay_is_order_independent_and_does_not_double_post():
    ledger = basic_ledger()
    postings = [Posting("cash:main", "USD", Decimal("-10.00")), Posting("clearing:bank", "USD", Decimal("10.0"))]
    first = await ledger.post_transaction("retry-1", postings, reference="payment-1")
    second = await ledger.post_transaction("retry-1", list(reversed(postings)), reference="payment-1")
    assert first.replayed is False
    assert second.replayed is True
    assert second.transaction.transaction_id == first.transaction.transaction_id
    assert await ledger.get_balance("cash:main", "USD") == Decimal("90.00")
    assert len(await ledger.transactions()) == 1


@pytest.mark.asyncio
async def test_idempotency_conflict_rejects_different_payload():
    ledger = basic_ledger()
    await ledger.post_transaction(
        "retry-1", [Posting("cash:main", "USD", Decimal("-5")), Posting("clearing:bank", "USD", Decimal("5"))], reference="payment-1"
    )
    with pytest.raises(IdempotencyConflictError):
        await ledger.post_transaction(
            "retry-1", [Posting("cash:main", "USD", Decimal("-7")), Posting("clearing:bank", "USD", Decimal("7"))], reference="payment-1"
        )
    assert await ledger.get_balance("cash:main", "USD") == Decimal("95.00")


@pytest.mark.asyncio
async def test_missing_account_duplicate_registration_and_bad_time():
    ledger = basic_ledger()
    with pytest.raises(DuplicateAccountError):
        ledger.add_account(Account("cash:main"))
    with pytest.raises(AccountNotFoundError):
        await ledger.post_transaction(
            "key-missing", [Posting("cash:missing", "USD", Decimal("-1")), Posting("clearing:bank", "USD", Decimal("1"))], reference="payment-2"
        )
    with pytest.raises(ValidationError):
        await ledger.post_transaction(
            "key-bad-time",
            [Posting("cash:main", "USD", Decimal("-1")), Posting("clearing:bank", "USD", Decimal("1"))],
            reference="payment-3",
            created_at=datetime(2026, 1, 1),
        )


@pytest.mark.asyncio
async def test_snapshot_is_consistent_and_account_ids_are_stable():
    ledger = basic_ledger()
    snapshot = await ledger.snapshot()
    assert snapshot["cash:main"]["USD"] == Decimal("100.00")
    assert ledger.account_ids() == ("cash:main", "clearing:bank")
    assert await ledger._accounts["cash:main"].snapshot() == {"USD": Decimal("100.00")}
    assert ledger.has_account("cash:main") is True
    assert ledger.has_account("not:here") is False


def test_account_opening_balance_validation():
    with pytest.raises(ValidationError):
        Account("wallet:bad", {"USD": Decimal("-1")})
    with pytest.raises(ValidationError):
        Account("wallet:bad", {"USD": Decimal("-1")}, allow_negative=False)
    assert Account("clearing:ok", {"USD": Decimal("-1")}, allow_negative=True).opening_balances["USD"] == Decimal("-1")


@pytest.mark.asyncio
async def test_concurrent_withdrawals_never_overdraw_account():
    ledger = Ledger()
    ledger.add_account(Account("wallet:cash", {"USD": Decimal("100")}))
    ledger.add_account(Account("clearing:outside", allow_negative=True))

    async def withdraw(index: int):
        try:
            return await ledger.post_transaction(
                f"withdraw-{index}",
                [Posting("wallet:cash", "USD", Decimal("-3")), Posting("clearing:outside", "USD", Decimal("3"))],
                reference=f"withdrawal-{index}",
            )
        except InsufficientFundsError as exc:
            return exc

    results = await asyncio.gather(*(withdraw(i) for i in range(50)))
    committed = [result for result in results if not isinstance(result, Exception)]
    rejected = [result for result in results if isinstance(result, InsufficientFundsError)]
    assert len(committed) == 33
    assert len(rejected) == 17
    assert await ledger.get_balance("wallet:cash", "USD") == Decimal("1")
    assert await ledger.get_balance("clearing:outside", "USD") == Decimal("99")
    assert len(await ledger.transactions()) == 33


@pytest.mark.asyncio
async def test_same_idempotency_key_concurrent_retries_commit_once():
    ledger = basic_ledger()
    postings = [Posting("cash:main", "USD", Decimal("-4")), Posting("clearing:bank", "USD", Decimal("4"))]
    results = await asyncio.gather(*(
        ledger.post_transaction("same-key", postings, reference="one-payment") for _ in range(20)
    ))
    assert sum(not item.replayed for item in results) == 1
    assert sum(item.replayed for item in results) == 19
    assert await ledger.get_balance("cash:main", "USD") == Decimal("96.00")
    assert len(await ledger.transactions()) == 1
