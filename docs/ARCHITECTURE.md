# Architecture and algorithm notes

## Layer boundaries

```text
Price feed / CLI
      │ PriceQuote
      ▼
MarketPipeline ── bounded asyncio.Queue ── worker tasks
      │
      ▼
OpportunityEngine ── SlidingWindowQuoteBook
      │                         │
      │ candidate               └── event-time expiration / sequence checks
      ▼
ArbitrageStrategy
      │ fee-adjusted opportunity
      ▼
ExecutionEngine
      │ signed Posting[]
      ▼
Ledger ── per-idempotency-key lock ── sorted account locks
      │
      └── immutable LedgerTransaction + LedgerEntry[]
```

### Domain

`PriceQuote`, `ArbitrageOpportunity`, `Posting`, `LedgerEntry`, `LedgerTransaction`, and `ExecutionResult` are typed data contracts. Financial inputs are `Decimal`, identifiers are restricted to a narrow safe ASCII format, currencies are normalized to three uppercase letters, and timestamps are required to be timezone-aware and normalized to UTC. ISO-code shape is checked here; authoritative currency/market enablement belongs in deployment configuration.

`ArbitrageStrategy` is a deterministic scoring function given explicit quotes and time. Its fee calculation is based on notional per leg. It rejects mismatched pairs, identical venues, stale quotes, nonpositive quantity, nonpositive spread, and candidates that fall below the minimum net-profit or edge thresholds.

### Sliding-window algorithm

`SlidingWindowSpread` stores samples in FIFO order and maintains monotonic min/max deques plus an exact running sum. When a sample falls before the cutoff, it is removed from the sample deque and from a min/max deque if it is the head. Each element enters and leaves each relevant deque at most once, producing O(1) amortized updates and O(min(W, C)) memory for W observations in the active time window and configured sample cap C.

`SlidingWindowQuoteBook` uses `(pair, venue)` streams. Per stream, sequence numbers must strictly increase and timestamps may not regress. A global event-time watermark allows modest cross-venue event disorder while dropping observations older than the active window. Each registered stream is capped by a maximum quote count, and the quote book rejects new stream identities after its configured stream cap is reached.

`OpportunityEngine` compares a new quote against current quotes at other venues for the same pair in both directions. For V venues it considers O(V) candidates per event; it does not rescan every possible venue pair after each quote.

### Atomic ledger and concurrency

A journal is validated before state mutation:

1. Validate idempotency key, reference, non-empty postings, and posting types.
2. Aggregate posting totals per currency; any non-zero currency total rejects the transaction.
3. Acquire the per-idempotency-key lock. Replays return the original transaction; payload conflicts fail.
4. Acquire affected account locks in sorted account-ID order, preventing lock-order cycles.
5. Calculate all resulting balances and reject a non-overdraft account if any currency would become negative.
6. Create immutable journal entries. In a no-await commit section guarded by account locks and a short registry lock, apply all deltas and store the transaction/idempotency record.

Only one transaction for a given idempotency key can commit. Transactions that touch disjoint accounts can validate concurrently; the registry lock is held only for metadata access and commit bookkeeping, not for the full validation period. Since Python asyncio cancellation is delivered at await points, the mutation section cannot be interrupted halfway through by task cancellation.

The lock boundary is one `Ledger` object in one Python process. The same algorithm must be re-expressed with durable DB transactions and unique constraints for multiple processes. Do not share this in-memory ledger as a substitute for a live financial ledger.

## Rounding and P&L

The quote currency is the unit of profit. `gross_profit = sell_notional - buy_notional`; `net_profit = gross_profit - buy_fee - sell_fee`. Notionals and fees are rounded using the quote currency's configured minor unit. Because the quote model's `bid` and `ask` are raw market prices and no simulated fill-slippage is currently applied, this is a price-difference/fee simulator rather than a full market impact model.

## Extension seams

- Replace `MemoryQuoteFeed` with a validated historical feed or a synthetic market generator.
- Add an interface adapter around `OpportunityEngine` for REST or streaming protocols; keep the domain free from transport objects.
- Replace `Ledger` storage with an ACID repository. Keep balanced posting calculation and domain validations above the storage layer.
- Add rate limiting, venue-specific precision/fee tables, latency distributions, partial-fill modeling, stale-book policies, risk caps, and currency exposure controls as explicit policies rather than hidden globals.
- Add durable outbox events if committed transactions must trigger downstream reporting.
