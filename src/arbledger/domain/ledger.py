"""Concurrency-safe multi-currency double-entry ledger.

All transaction writes follow one lock order: the idempotency registry first, then
account locks sorted by account identifier. That global order prevents deadlocks when
concurrent transactions overlap accounts. Once locks are acquired, validation and
mutation execute without an ``await``, so task cancellation cannot expose partial
account updates inside this single-process simulator.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import defaultdict
from collections.abc import AsyncIterator, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from .exceptions import (
    AccountNotFoundError,
    DuplicateAccountError,
    IdempotencyConflictError,
    InsufficientFundsError,
    LedgerInvariantError,
    ValidationError,
)
from .models import LedgerEntry, LedgerTransaction, Posting, PostResult
from .validation import aware_datetime, currency_code, decimal_value, identifier, utc_now


@dataclass(slots=True)
class Account:
    """Mutable ledger account guarded by its private async lock.

    Opening balances represent a configured opening position. Journal transactions
    after startup are always double-entry balanced by currency. ``allow_negative`` is
    reserved for clearing/counterparty and designated control accounts.
    """

    account_id: str
    opening_balances: Mapping[str, Decimal | str | int] = field(default_factory=dict)
    allow_negative: bool = False
    _balances: dict[str, Decimal] = field(init=False, repr=False)
    _lock: asyncio.Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.account_id = identifier(self.account_id, field="account_id")
        normalized: dict[str, Decimal] = {}
        for currency, amount in self.opening_balances.items():
            code = currency_code(currency)
            parsed = decimal_value(amount, field=f"opening balance {code}")
            if parsed < 0 and not self.allow_negative:
                raise ValidationError(
                    f"opening balance for {self.account_id}/{code} cannot be negative"
                )
            normalized[code] = normalized.get(code, Decimal(0)) + parsed
        self._balances = normalized
        self.opening_balances = dict(normalized)
        self._lock = asyncio.Lock()

    def _balance(self, currency: str) -> Decimal:
        return self._balances.get(currency, Decimal(0))

    def _apply(self, currency: str, amount: Decimal) -> None:
        self._balances[currency] = self._balance(currency) + amount

    async def snapshot(self) -> dict[str, Decimal]:
        """Return a consistent account balance copy."""
        async with self._lock:
            return dict(self._balances)


class Ledger:
    """In-memory journal supporting atomic, idempotent financial postings.

    The storage interface is intentionally narrow so a durable SQL repository can be
    substituted later. This implementation provides process-local atomicity only; a
    multi-process deployment must replace it with database transactions and row locks.
    """

    def __init__(self) -> None:
        self._accounts: dict[str, Account] = {}
        self._transactions: list[LedgerTransaction] = []
        self._idempotency: dict[str, tuple[str, LedgerTransaction]] = {}
        # The registry lock is held only for metadata access/commit, while a per-key
        # lock serializes retries for one idempotency token. Disjoint account writes
        # may therefore validate and prepare concurrently.
        self._registry_lock = asyncio.Lock()
        self._key_locks: dict[str, asyncio.Lock] = {}

    def add_account(self, account: Account) -> None:
        """Register an account during setup; duplicate identifiers are rejected."""
        if account.account_id in self._accounts:
            raise DuplicateAccountError(f"account already exists: {account.account_id}")
        self._accounts[account.account_id] = account

    def has_account(self, account_id: str) -> bool:
        """Return whether an account identifier is registered."""
        return account_id in self._accounts

    def account_ids(self) -> tuple[str, ...]:
        """Return stable, sorted account identifiers."""
        return tuple(sorted(self._accounts))

    async def get_balance(self, account_id: str, currency: str) -> Decimal:
        """Read a single balance while holding its account lock."""
        account = self._get_account(account_id)
        code = currency_code(currency)
        async with account._lock:
            return account._balance(code)

    async def snapshot(self) -> dict[str, dict[str, Decimal]]:
        """Read all accounts using sorted lock acquisition for a consistent view."""
        accounts = [self._accounts[key] for key in sorted(self._accounts)]
        async with self._acquire_account_locks(accounts):
            return {account.account_id: dict(account._balances) for account in accounts}

    async def transactions(self) -> tuple[LedgerTransaction, ...]:
        """Return committed transactions in commit order."""
        async with self._registry_lock:
            return tuple(self._transactions)

    async def post_transaction(
        self,
        idempotency_key: str,
        postings: Iterable[Posting],
        *,
        reference: str,
        created_at: datetime | None = None,
    ) -> PostResult:
        """Atomically validate and commit one multi-currency balanced journal.

        Args:
            idempotency_key: Unique key for a logical write. Same-key/same-payload
                retries return the original transaction without reapplying balances.
            postings: Signed journal lines. Per-currency totals must equal zero.
            reference: Business reference for audit and correlation.
            created_at: Optional deterministic timestamp for replay/tests.

        Returns:
            ``PostResult`` with the immutable committed transaction and replay flag.

        Raises:
            LedgerInvariantError: For an unbalanced journal or forbidden overdraft.
            IdempotencyConflictError: For reuse of a key with a different payload.
            AccountNotFoundError: For any unregistered posting account.

        Complexity: O(P log P + A log A), with P postings and A distinct accounts.
        """
        key = identifier(idempotency_key, field="idempotency_key")
        ref = identifier(reference, field="reference")
        lines = tuple(postings)
        if not lines:
            raise LedgerInvariantError("a transaction must contain at least one posting")
        if any(not isinstance(line, Posting) for line in lines):
            raise ValidationError("all transaction lines must be Posting instances")
        totals: dict[str, Decimal] = defaultdict(Decimal)
        account_deltas: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        for line in lines:
            totals[line.currency] += line.amount
            account_deltas[(line.account_id, line.currency)] += line.amount
        unbalanced = {code: total for code, total in totals.items() if total != 0}
        if unbalanced:
            formatted = ", ".join(f"{code}={total}" for code, total in sorted(unbalanced.items()))
            raise LedgerInvariantError(f"journal is not balanced by currency: {formatted}")

        fingerprint = self._fingerprint(ref, lines)
        async with self._registry_lock:
            key_lock = self._key_locks.setdefault(key, asyncio.Lock())
        async with key_lock:
            async with self._registry_lock:
                prior = self._idempotency.get(key)
            if prior is not None:
                old_fingerprint, transaction = prior
                if old_fingerprint != fingerprint:
                    raise IdempotencyConflictError(
                        f"idempotency key reused with different payload: {key}"
                    )
                return PostResult(transaction=transaction, replayed=True)

            account_ids = sorted({line.account_id for line in lines})
            accounts = [self._get_account(account_id) for account_id in account_ids]
            async with self._acquire_account_locks(accounts):
                # Validate all resulting balances before mutating any account.
                for (account_id, code), delta in account_deltas.items():
                    account = self._accounts[account_id]
                    resulting = account._balance(code) + delta
                    if resulting < 0 and not account.allow_negative:
                        raise InsufficientFundsError(
                            f"posting would overdraw {account_id}/{code}: resulting balance {resulting}"
                        )

                instant = (
                    aware_datetime(created_at, field="created_at") if created_at else utc_now()
                )
                transaction_id = (
                    "txn-" + hashlib.sha256(f"{key}|{fingerprint}".encode()).hexdigest()[:24]
                )
                entries = tuple(
                    LedgerEntry(
                        transaction_id=transaction_id,
                        account_id=line.account_id,
                        currency=line.currency,
                        amount=line.amount,
                        reference=ref,
                        created_at=instant,
                    )
                    for line in lines
                )
                transaction = LedgerTransaction(
                    transaction_id=transaction_id,
                    idempotency_key=key,
                    reference=ref,
                    entries=entries,
                    created_at=instant,
                )
                # Briefly protect the registry, then mutate balances and journal index
                # without another await. Per-account locks remain held during commit.
                async with self._registry_lock:
                    # The per-key lock guarantees this key has no concurrent writer.
                    for (account_id, code), delta in account_deltas.items():
                        self._accounts[account_id]._apply(code, delta)
                    self._transactions.append(transaction)
                    self._idempotency[key] = (fingerprint, transaction)
                    return PostResult(transaction=transaction, replayed=False)

    def _get_account(self, account_id: str) -> Account:
        try:
            return self._accounts[account_id]
        except KeyError as exc:
            raise AccountNotFoundError(f"account not registered: {account_id}") from exc

    @staticmethod
    def _fingerprint(reference: str, postings: tuple[Posting, ...]) -> str:
        """Hash the normalized business payload independent of posting order."""
        normalized = sorted(
            (p.account_id, p.currency, format(p.amount.normalize(), "f")) for p in postings
        )
        canonical = reference + "\n" + "\n".join("|".join(line) for line in normalized)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    @asynccontextmanager
    async def _acquire_account_locks(accounts: list[Account]) -> AsyncIterator[None]:
        """Acquire unique locks in sorted account order and always release in reverse."""
        acquired: list[asyncio.Lock] = []
        try:
            for account in sorted(accounts, key=lambda item: item.account_id):
                await account._lock.acquire()
                acquired.append(account._lock)
            yield
        finally:
            for lock in reversed(acquired):
                lock.release()
