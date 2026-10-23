# Security and safety notes

ArbLedger is a local paper-trading simulator. It provides no authentication server, API, broker connectivity, exchange credentials, or remote order-placement capability.

## Implemented controls

- Strict decimal input parsing; binary floating-point values are rejected at financial boundaries.
- Narrow safe identifier syntax and validated currency tokens.
- Positive price and quantity checks, quote ordering/age checks, and expiry policy.
- Per-currency double-entry journal balancing.
- Non-overdraft wallet account checks before any state mutation.
- Sorted multi-account lock acquisition and idempotency-key conflict handling.
- Bounded input queue with backpressure and message-level error isolation.
- JSON logs and correlation scopes without recording secrets.

## Known limitations

- The in-memory ledger does not persist across restart and cannot coordinate multiple OS processes.
- There is no authentication, authorization, rate limiting, durable audit store, or database-backed idempotency constraint.
- The currency precision table is illustrative and must be replaced with approved metadata before production financial use.
- The strategy models quoted price differences and percentage fees; it does not model partial fills, depth, latency, market impact, transfer/settlement costs, counterparty default, or venue-specific restrictions.
- Market feed and prices are synthetic; the code should not be interpreted as investment advice or a profitability guarantee.

## Before any external integration

Perform a threat model, security review, risk/legal/compliance review, dependency and supply-chain scan, load/failure testing, independent financial reconciliation, and a controlled paper-trading period. Do not place API secrets in source control or `.env` files committed to the repository. Add an external connector only with explicit authentication, permission scoping, kill switches, per-venue caps, a durable ledger, and audited approval flows.
