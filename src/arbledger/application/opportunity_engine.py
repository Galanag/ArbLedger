"""Combine the sliding quote book with a fee-aware arbitrage strategy."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from arbledger.domain.exceptions import ValidationError
from arbledger.domain.models import ArbitrageOpportunity, PriceQuote
from arbledger.domain.strategy import ArbitrageStrategy
from arbledger.domain.validation import decimal_value

from .sliding_window import SlidingWindowQuoteBook


class OpportunityEngine:
    """Incrementally discover executable buy/sell opportunities on new quotes.

    Each incoming quote is compared with the latest quote from every other venue for
    that pair, in both trade directions. With V active venues, one update costs O(V)
    candidate evaluations rather than a full O(V²) rescan.
    """

    def __init__(
        self,
        quote_book: SlidingWindowQuoteBook,
        strategy: ArbitrageStrategy,
        quantities: Mapping[str, Decimal | str | int],
    ) -> None:
        self.quote_book = quote_book
        self.strategy = strategy
        self.quantities = {
            pair: decimal_value(qty, field=f"quantity for {pair}")
            for pair, qty in quantities.items()
        }
        for pair, quantity in self.quantities.items():
            pieces = pair.split("/")
            if len(pieces) != 2 or any(
                len(piece) != 3 or not piece.isalpha() or piece.upper() != piece for piece in pieces
            ):
                raise ValidationError(f"invalid configured currency pair: {pair!r}")
            if pieces[0] == pieces[1] or quantity <= 0:
                raise ValidationError(
                    "configured pair quantities must be positive and pair currencies must differ"
                )

    def on_quote(self, quote: PriceQuote) -> tuple[ArbitrageOpportunity, ...]:
        """Consume one quote and return all newly discoverable candidates."""
        quantity = self.quantities.get(quote.pair)
        self.quote_book.ingest(quote)
        if quantity is None:
            return ()
        opportunities: list[ArbitrageOpportunity] = []
        for other in self.quote_book.latest_quotes(quote.pair):
            if other.venue == quote.venue:
                continue
            observed_now = max(quote.timestamp, other.timestamp)
            for buy_quote, sell_quote in ((quote, other), (other, quote)):
                candidate = self.strategy.evaluate(
                    buy_quote, sell_quote, quantity, now=observed_now
                )
                if candidate is not None:
                    opportunities.append(candidate)
        return tuple(opportunities)
