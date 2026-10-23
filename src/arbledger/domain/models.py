"""Immutable value objects for quote, opportunity, trade, and journal boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .exceptions import ValidationError
from .money import quantize_currency
from .validation import aware_datetime, currency_code, decimal_value, identifier


@dataclass(frozen=True, slots=True)
class PriceQuote:
    """A two-sided quote emitted by one venue for ``base/quote``.

    ``bid`` is the quote-currency price at which the venue buys one base unit;
    ``ask`` is the price at which it sells one base unit. ``sequence`` must increase
    within each venue/pair stream. All timestamps are normalized to UTC.
    """

    venue: str
    base_currency: str
    quote_currency: str
    bid: Decimal
    ask: Decimal
    timestamp: datetime
    sequence: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "venue", identifier(self.venue, field="venue"))
        base = currency_code(self.base_currency)
        quote = currency_code(self.quote_currency)
        if base == quote:
            raise ValidationError("base and quote currencies must differ")
        object.__setattr__(self, "base_currency", base)
        object.__setattr__(self, "quote_currency", quote)
        bid = decimal_value(self.bid, field="bid")
        ask = decimal_value(self.ask, field="ask")
        if bid <= 0 or ask <= 0:
            raise ValidationError("bid and ask must be greater than zero")
        if bid > ask:
            raise ValidationError("bid cannot exceed ask for a non-crossed quote")
        object.__setattr__(self, "bid", bid)
        object.__setattr__(self, "ask", ask)
        object.__setattr__(self, "timestamp", aware_datetime(self.timestamp))
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 0
        ):
            raise ValidationError("sequence must be a non-negative integer")

    @property
    def pair(self) -> str:
        """Canonical market pair, for example ``EUR/USD``."""
        return f"{self.base_currency}/{self.quote_currency}"


@dataclass(frozen=True, slots=True)
class ArbitrageOpportunity:
    """A time-bounded buy-low/sell-high candidate with estimated costs."""

    opportunity_id: str
    pair: str
    buy_quote: PriceQuote
    sell_quote: PriceQuote
    quantity: Decimal
    buy_notional: Decimal
    sell_notional: Decimal
    buy_fee: Decimal
    sell_fee: Decimal
    fee_bps: Decimal
    gross_profit: Decimal
    net_profit: Decimal
    edge_bps: Decimal
    observed_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "opportunity_id", identifier(self.opportunity_id, field="opportunity_id")
        )
        object.__setattr__(self, "quantity", decimal_value(self.quantity, field="quantity"))
        if self.quantity <= 0:
            raise ValidationError("quantity must be greater than zero")
        for name in (
            "buy_notional",
            "sell_notional",
            "buy_fee",
            "sell_fee",
            "fee_bps",
            "gross_profit",
            "net_profit",
            "edge_bps",
        ):
            object.__setattr__(self, name, decimal_value(getattr(self, name), field=name))
        object.__setattr__(
            self, "observed_at", aware_datetime(self.observed_at, field="observed_at")
        )
        object.__setattr__(self, "expires_at", aware_datetime(self.expires_at, field="expires_at"))
        if self.expires_at < self.observed_at:
            raise ValidationError("expires_at cannot be earlier than observed_at")
        if self.buy_quote.pair != self.pair or self.sell_quote.pair != self.pair:
            raise ValidationError("opportunity quotes must match the requested pair")
        if self.buy_quote.venue == self.sell_quote.venue:
            raise ValidationError("buy and sell venues must differ")
        if self.buy_quote.ask >= self.sell_quote.bid:
            raise ValidationError("opportunity must buy below the sell bid")
        if self.buy_notional <= 0 or self.sell_notional <= 0:
            raise ValidationError("opportunity notionals must be greater than zero")
        if self.buy_notional != quantize_currency(
            self.buy_quote.ask * self.quantity, self.buy_quote.quote_currency
        ):
            raise ValidationError("buy_notional is inconsistent with ask price and quantity")
        if self.sell_notional != quantize_currency(
            self.sell_quote.bid * self.quantity, self.sell_quote.quote_currency
        ):
            raise ValidationError("sell_notional is inconsistent with bid price and quantity")
        if self.fee_bps < 0 or self.buy_fee < 0 or self.sell_fee < 0:
            raise ValidationError("fee basis points and amounts must be non-negative")
        fee_rate = self.fee_bps / Decimal(10000)
        expected_buy_fee = quantize_currency(
            self.buy_notional * fee_rate, self.buy_quote.quote_currency
        )
        expected_sell_fee = quantize_currency(
            self.sell_notional * fee_rate, self.sell_quote.quote_currency
        )
        if self.buy_fee != expected_buy_fee or self.sell_fee != expected_sell_fee:
            raise ValidationError("fee amounts are inconsistent with fee_bps and notionals")
        if self.gross_profit != self.sell_notional - self.buy_notional:
            raise ValidationError("gross_profit must equal sell_notional minus buy_notional")
        if self.net_profit != self.gross_profit - self.buy_fee - self.sell_fee:
            raise ValidationError("net_profit must equal gross profit minus both fees")
        expected_edge = self.net_profit / self.buy_notional * Decimal(10000)
        if self.edge_bps != expected_edge:
            raise ValidationError("edge_bps is inconsistent with net_profit and buy_notional")


@dataclass(frozen=True, slots=True)
class Posting:
    """One signed journal line; positive credits an account, negative debits it."""

    account_id: str
    currency: str
    amount: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_id", identifier(self.account_id, field="account_id"))
        object.__setattr__(self, "currency", currency_code(self.currency))
        amount = decimal_value(self.amount, field="posting amount")
        if amount == 0:
            raise ValidationError("zero-value postings are not allowed")
        object.__setattr__(self, "amount", amount)


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """Persistable journal line created by a balanced transaction."""

    transaction_id: str
    account_id: str
    currency: str
    amount: Decimal
    reference: str
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "transaction_id", identifier(self.transaction_id, field="transaction_id")
        )
        object.__setattr__(self, "account_id", identifier(self.account_id, field="account_id"))
        object.__setattr__(self, "currency", currency_code(self.currency))
        object.__setattr__(self, "amount", decimal_value(self.amount, field="entry amount"))
        object.__setattr__(self, "reference", identifier(self.reference, field="reference"))
        object.__setattr__(self, "created_at", aware_datetime(self.created_at, field="created_at"))


@dataclass(frozen=True, slots=True)
class LedgerTransaction:
    """Immutable committed journal transaction."""

    transaction_id: str
    idempotency_key: str
    reference: str
    entries: tuple[LedgerEntry, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "transaction_id", identifier(self.transaction_id, field="transaction_id")
        )
        object.__setattr__(
            self, "idempotency_key", identifier(self.idempotency_key, field="idempotency_key")
        )
        object.__setattr__(self, "reference", identifier(self.reference, field="reference"))
        object.__setattr__(self, "created_at", aware_datetime(self.created_at, field="created_at"))
        if not self.entries:
            raise ValidationError("a ledger transaction must contain entries")

    def totals_by_currency(self) -> Mapping[str, Decimal]:
        """Return journal totals by currency; a balanced transaction sums to zero.

        Complexity: O(n), where n is the number of journal entries.
        """
        totals: dict[str, Decimal] = {}
        for entry in self.entries:
            totals[entry.currency] = totals.get(entry.currency, Decimal(0)) + entry.amount
        return totals


@dataclass(frozen=True, slots=True)
class PostResult:
    """Ledger write result, including whether this was an idempotent replay."""

    transaction: LedgerTransaction
    replayed: bool


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Result of an arbitrage paper-execution attempt."""

    opportunity_id: str
    transaction: LedgerTransaction
    quantity: Decimal
    gross_profit: Decimal
    fees_paid: Decimal
    net_profit: Decimal
    idempotent_replay: bool
