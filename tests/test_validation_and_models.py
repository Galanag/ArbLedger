"""Boundary validation and immutable model tests."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from arbledger.domain.exceptions import ValidationError
from arbledger.domain.models import LedgerEntry, LedgerTransaction, Posting, PriceQuote
from arbledger.domain.validation import aware_datetime, currency_code, decimal_value, identifier
from conftest import BASE_TIME, make_quote


@pytest.mark.parametrize("value, expected", [("1.2300", Decimal("1.2300")), (7, Decimal("7")), (Decimal("0.10"), Decimal("0.10"))])
def test_decimal_value_accepts_exact_types(value, expected):
    assert decimal_value(value) == expected


@pytest.mark.parametrize("value", [1.2, True, False, float("nan"), "NaN", "Infinity", object(), "bad"])
def test_decimal_value_rejects_unsafe_values(value):
    with pytest.raises(ValidationError):
        decimal_value(value)


@pytest.mark.parametrize(("value", "expected"), [(" usd ", "USD"), ("eur", "EUR"), ("ETB", "ETB")])
def test_currency_normalizes(value, expected):
    assert currency_code(value) == expected


@pytest.mark.parametrize("value", ["US", "USDD", "U$D", "12A", "", None])
def test_currency_rejects_bad_codes(value):
    with pytest.raises(ValidationError):
        currency_code(value)


@pytest.mark.parametrize("value", ["a", "wallet:venue_A", "opportunity-123", "id.with.dot", "x" * 64])
def test_identifier_accepts_safe_tokens(value):
    assert identifier(value) == value


@pytest.mark.parametrize("value", ["", "has space", "/etc/passwd", "x" * 65, "é"])
def test_identifier_rejects_unsafe_tokens(value):
    with pytest.raises(ValidationError):
        identifier(value)


def test_aware_datetime_converts_to_utc_and_rejects_naive():
    local = datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc).astimezone(timezone.utc)
    assert aware_datetime(local).utcoffset().total_seconds() == 0
    with pytest.raises(ValidationError, match="timezone"):
        aware_datetime(datetime(2026, 1, 1))


def test_price_quote_normalizes_pair_and_timestamp():
    quote = PriceQuote("venue_a", "eur", "usd", Decimal("1.1"), Decimal("1.2"), BASE_TIME, 0)
    assert quote.pair == "EUR/USD"
    assert quote.base_currency == "EUR"
    assert quote.timestamp.tzinfo is not None


@pytest.mark.parametrize(
    "overrides",
    [
        {"base_currency": "EUR", "quote_currency": "EUR"},
        {"bid": Decimal("0"), "ask": Decimal("1")},
        {"bid": Decimal("1.2"), "ask": Decimal("1.1")},
        {"sequence": -1},
        {"sequence": True},
        {"timestamp": datetime(2026, 1, 1)},
        {"venue": "not a venue"},
        {"ask": 1.12},
    ],
)
def test_price_quote_rejects_invalid_fields(overrides):
    payload = dict(venue="venue_a", base_currency="EUR", quote_currency="USD", bid=Decimal("1.1"), ask=Decimal("1.2"), timestamp=BASE_TIME, sequence=1)
    payload.update(overrides)
    with pytest.raises(ValidationError):
        PriceQuote(**payload)


def test_posting_is_nonzero_and_normalizes_currency():
    assert Posting("wallet:one", "usd", Decimal("-1")).currency == "USD"
    with pytest.raises(ValidationError, match="zero-value"):
        Posting("wallet:one", "USD", Decimal("0"))


def test_ledger_entry_and_transaction_total_by_currency():
    entry = LedgerEntry("txn-1", "wallet:one", "USD", Decimal("-3"), "ref-1", BASE_TIME)
    other = LedgerEntry("txn-1", "wallet:two", "USD", Decimal("3"), "ref-1", BASE_TIME)
    transaction = LedgerTransaction("txn-1", "key-1", "ref-1", (entry, other), BASE_TIME)
    assert transaction.totals_by_currency() == {"USD": Decimal("0")}
    with pytest.raises(ValidationError, match="must contain entries"):
        LedgerTransaction("txn-2", "key-2", "ref-2", (), BASE_TIME)


def test_quote_factory_produces_expected_sequence():
    assert make_quote("venue_z", second=10, sequence=9).venue == "venue_z"


def test_arbitrage_opportunity_rejects_each_inconsistent_invariant():
    from dataclasses import replace
    from datetime import timedelta

    from arbledger.domain.models import ArbitrageOpportunity
    from conftest import make_opportunity as create_opportunity

    opportunity = create_opportunity(now=BASE_TIME)
    invalid_changes = [
        {"quantity": Decimal("0")},
        {"expires_at": opportunity.observed_at - timedelta(seconds=1)},
        {"pair": "GBP/USD"},
        {"sell_quote": opportunity.buy_quote},
        {"sell_quote": make_quote("venue_b", bid="1.08", ask="1.09")},
        {"buy_notional": Decimal("0")},
        {"buy_fee": Decimal("-1")},
        {"gross_profit": Decimal("0")},
        {"net_profit": Decimal("0")},
        {"edge_bps": Decimal("0")},
    ]
    assert isinstance(opportunity, ArbitrageOpportunity)
    for changes in invalid_changes:
        with pytest.raises(ValidationError):
            replace(opportunity, **changes)


def test_ledger_entry_rejects_invalid_reference_and_timestamp():
    with pytest.raises(ValidationError):
        LedgerEntry("txn-1", "wallet:one", "USD", Decimal("1"), "has space", BASE_TIME)
    with pytest.raises(ValidationError):
        LedgerEntry("txn-1", "wallet:one", "USD", Decimal("1"), "ref-1", datetime(2026, 1, 1))


def test_opportunity_notional_and_fee_metadata_must_match_quotes():
    from dataclasses import replace
    from conftest import make_opportunity as create_opportunity

    opportunity = create_opportunity(now=BASE_TIME)
    invalid_changes = [
        {"buy_notional": opportunity.buy_notional + Decimal("1")},
        {"sell_notional": opportunity.sell_notional + Decimal("1")},
        {"fee_bps": Decimal("-1")},
        {"buy_fee": opportunity.buy_fee + Decimal("1")},
    ]
    for changes in invalid_changes:
        with pytest.raises(ValidationError):
            replace(opportunity, **changes)


def test_money_quantization_rejects_out_of_range_decimal():
    from arbledger.domain.money import quantize_currency

    assert quantize_currency(Decimal("12.345"), "USD") == Decimal("12.34")
    assert quantize_currency(Decimal("12.345"), "KWD") == Decimal("12.345")
    with pytest.raises(ValidationError, match="precision"):
        quantize_currency(Decimal("1E+1000"), "USD")
    with pytest.raises(ValidationError):
        quantize_currency(Decimal("1"), "US")
