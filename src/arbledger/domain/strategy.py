"""Arbitrage candidate scoring and fee-aware opportunity construction."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from .exceptions import ValidationError
from .models import ArbitrageOpportunity, PriceQuote
from .money import quantize_currency
from .validation import aware_datetime, decimal_value, utc_now


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    """Threshold and timing controls for a simple cross-venue strategy.

    ``fee_bps`` is the fee on each notional, in basis points (1 bps = 0.01%).
    ``min_net_profit`` is expressed in the pair's quote currency. A candidate is
    emitted only when its fee-adjusted net profit clears this threshold.
    """

    fee_bps: Decimal = Decimal("5")
    min_net_profit: Decimal = Decimal("0.01")
    min_edge_bps: Decimal = Decimal("0")
    max_quote_age_seconds: Decimal = Decimal("2")
    opportunity_ttl_seconds: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        for field in ("fee_bps", "min_net_profit", "min_edge_bps", "max_quote_age_seconds", "opportunity_ttl_seconds"):
            value = decimal_value(getattr(self, field), field=field)
            object.__setattr__(self, field, value)
        if self.fee_bps < 0 or self.min_net_profit < 0 or self.min_edge_bps < 0:
            raise ValidationError("fee and minimum thresholds must be non-negative")
        if self.max_quote_age_seconds <= 0 or self.opportunity_ttl_seconds <= 0:
            raise ValidationError("quote age and opportunity TTL must be greater than zero")


class ArbitrageStrategy:
    """Score a buy/sell quote pair and return only economically viable opportunities.

    The scorer is pure apart from reading the supplied clock, which makes it simple
    to replay deterministically in unit tests. Candidate generation is O(1) per quote
    pair; cross-venue pair enumeration is handled by :class:`OpportunityEngine`.
    """

    def __init__(self, config: StrategyConfig | None = None) -> None:
        self.config = config or StrategyConfig()

    def evaluate(
        self,
        buy_quote: PriceQuote,
        sell_quote: PriceQuote,
        quantity: Decimal,
        *,
        now: datetime | None = None,
    ) -> ArbitrageOpportunity | None:
        """Return a viable opportunity, or ``None`` when any strategy gate fails."""
        qty = decimal_value(quantity, field="quantity")
        if qty <= 0:
            raise ValidationError("quantity must be greater than zero")
        if buy_quote.pair != sell_quote.pair:
            raise ValidationError("cannot compare quotes for different currency pairs")
        if buy_quote.venue == sell_quote.venue:
            return None

        observed_now = aware_datetime(now, field="now") if now else utc_now()
        # Both event-time observations must remain within the same validity envelope.
        if abs((observed_now - buy_quote.timestamp).total_seconds()) > float(self.config.max_quote_age_seconds):
            return None
        if abs((observed_now - sell_quote.timestamp).total_seconds()) > float(self.config.max_quote_age_seconds):
            return None
        if buy_quote.ask >= sell_quote.bid:
            return None

        quote_currency = buy_quote.quote_currency
        buy_notional = quantize_currency(buy_quote.ask * qty, quote_currency)
        sell_notional = quantize_currency(sell_quote.bid * qty, quote_currency)
        fee_rate = self.config.fee_bps / Decimal("10000")
        buy_fee = quantize_currency(buy_notional * fee_rate, quote_currency)
        sell_fee = quantize_currency(sell_notional * fee_rate, quote_currency)
        gross_profit = sell_notional - buy_notional
        net_profit = gross_profit - buy_fee - sell_fee
        edge_bps = (net_profit / buy_notional * Decimal("10000")) if buy_notional else Decimal("0")
        if net_profit < self.config.min_net_profit or edge_bps < self.config.min_edge_bps:
            return None

        # A deterministic identity makes deduplication repeatable across replays. It is
        # not used as the ledger idempotency key; callers still supply one per command.
        identity_text = "|".join((
            buy_quote.pair, buy_quote.venue, str(buy_quote.sequence),
            sell_quote.venue, str(sell_quote.sequence), str(qty),
        ))
        opportunity_id = "opp-" + hashlib.sha256(identity_text.encode("utf-8")).hexdigest()[:24]
        observed_at = max(buy_quote.timestamp, sell_quote.timestamp)
        return ArbitrageOpportunity(
            opportunity_id=opportunity_id,
            pair=buy_quote.pair,
            buy_quote=buy_quote,
            sell_quote=sell_quote,
            quantity=qty,
            buy_notional=buy_notional,
            sell_notional=sell_notional,
            buy_fee=buy_fee,
            sell_fee=sell_fee,
            fee_bps=self.config.fee_bps,
            gross_profit=gross_profit,
            net_profit=net_profit,
            edge_bps=edge_bps,
            observed_at=observed_at,
            expires_at=observed_at + timedelta(seconds=float(self.config.opportunity_ttl_seconds)),
        )
