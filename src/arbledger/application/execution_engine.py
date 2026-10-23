"""Atomic paper-execution service that converts opportunities into ledger postings."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from arbledger.domain.exceptions import OpportunityExpiredError, ValidationError
from arbledger.domain.ledger import Account, Ledger
from arbledger.domain.models import ArbitrageOpportunity, ExecutionResult, Posting
from arbledger.domain.money import quantize_currency
from arbledger.domain.validation import aware_datetime, decimal_value, identifier, utc_now


class ExecutionEngine:
    """Settle both arbitrage legs in one balanced multi-currency journal transaction.

    ``wallet:<venue>`` accounts are non-overdraft trading wallets;
    ``clearing:<venue>`` accounts are market counterparties; and ``fees:<currency>``
    accounts collect the currency-denominated fees. Register all venues and fee
    currencies before starting concurrent execution tasks.
    """

    def __init__(
        self, ledger: Ledger, *, max_execution_age_seconds: Decimal | str | int = "2"
    ) -> None:
        age = decimal_value(max_execution_age_seconds, field="max_execution_age_seconds")
        if age <= 0:
            raise ValidationError("max_execution_age_seconds must be finite and positive")
        self.ledger = ledger
        self.max_execution_age_seconds = age

    def register_venue(self, venue: str, opening_balances: dict[str, Decimal | str | int]) -> None:
        """Register a wallet and overdraft-enabled clearing account for a venue."""
        venue = identifier(venue, field="venue")
        self.ledger.add_account(Account(f"wallet:{venue}", opening_balances))
        self.ledger.add_account(Account(f"clearing:{venue}", allow_negative=True))

    def register_fee_currency(self, currency: str) -> None:
        """Register the fee revenue account for a quote currency."""
        self.ledger.add_account(Account(f"fees:{currency.upper()}", allow_negative=True))

    async def execute(
        self,
        opportunity: ArbitrageOpportunity,
        *,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> ExecutionResult:
        """Execute a viable opportunity as a single all-or-nothing ledger transaction.

        The wallet at the buy venue must hold enough quote currency for notional plus
        fees; the sell venue must hold the base quantity. The journal is checked for
        per-currency balance and both wallet balances before any balance is changed.
        """
        if not isinstance(opportunity, ArbitrageOpportunity):
            raise ValidationError("opportunity must be an ArbitrageOpportunity")
        key = identifier(idempotency_key, field="idempotency_key")
        instant = aware_datetime(now, field="now") if now else utc_now()
        age_limit = float(self.max_execution_age_seconds)
        if (
            instant > opportunity.expires_at
            or (instant - opportunity.observed_at).total_seconds() > age_limit
        ):
            raise OpportunityExpiredError(f"opportunity expired: {opportunity.opportunity_id}")

        buy = opportunity.buy_quote
        sell = opportunity.sell_quote
        quote_currency = buy.quote_currency
        base_currency = buy.base_currency
        buy_cost = quantize_currency(buy.ask * opportunity.quantity, quote_currency)
        sell_proceeds = quantize_currency(sell.bid * opportunity.quantity, quote_currency)
        # The strategy's rounded fee estimates and notional amounts are deliberately
        # reused, preventing the executor from scoring one amount and posting another.
        buy_fee = opportunity.buy_fee
        sell_fee = opportunity.sell_fee
        posting_values = (
            (f"wallet:{buy.venue}", base_currency, opportunity.quantity),
            (f"wallet:{buy.venue}", quote_currency, -(buy_cost + buy_fee)),
            (f"clearing:{buy.venue}", base_currency, -opportunity.quantity),
            (f"clearing:{buy.venue}", quote_currency, buy_cost),
            (f"fees:{quote_currency}", quote_currency, buy_fee),
            (f"wallet:{sell.venue}", base_currency, -opportunity.quantity),
            (f"wallet:{sell.venue}", quote_currency, sell_proceeds - sell_fee),
            (f"clearing:{sell.venue}", base_currency, opportunity.quantity),
            (f"clearing:{sell.venue}", quote_currency, -sell_proceeds),
            (f"fees:{quote_currency}", quote_currency, sell_fee),
        )
        postings = tuple(
            Posting(account_id, currency, amount)
            for account_id, currency, amount in posting_values
            if amount != 0
        )
        posted = await self.ledger.post_transaction(
            key,
            postings,
            reference=opportunity.opportunity_id,
            created_at=instant,
        )
        fees_paid = buy_fee + sell_fee
        net_profit = sell_proceeds - buy_cost - fees_paid
        return ExecutionResult(
            opportunity_id=opportunity.opportunity_id,
            transaction=posted.transaction,
            quantity=opportunity.quantity,
            gross_profit=sell_proceeds - buy_cost,
            fees_paid=fees_paid,
            net_profit=net_profit,
            idempotent_replay=posted.replayed,
        )
