# Test strategy

## Automated gates

```bash
pytest --cov=arbledger --cov-branch --cov-report=term-missing --cov-fail-under=95
```

The coverage gate is configured in `pyproject.toml`. Branch coverage is enabled and reports missing lines. The CLI module is exercised by an end-to-end test; `__main__.py` is excluded from coverage because it is only the process entry shim.

## Test families

| Test module | Primary guarantees |
|---|---|
| `test_validation_and_models.py` | exact decimal parsing, float rejection, currency/identifier shape, aware time, quote/transaction contract validation |
| `test_strategy_and_window.py` | fee math, stale quotes, edge thresholds, monotonic min/max queues, window expiry, stream order/caps |
| `test_ledger.py` | per-currency balance, all-or-nothing commit, insufficient funds, idempotent retry and conflict, concurrent withdrawals |
| `test_execution_engine.py` | two-leg settlement, P&L consistency, account funding, expired commands, zero-fee handling, inventory races |
| `test_pipeline_and_adapters.py` | queue backpressure/lifecycle, graceful drain, handler/error isolation, structured logging, CLI integration |

## Concurrency stress coverage

`test_concurrent_withdrawals_never_overdraw_account` launches 50 concurrent withdrawals against a $100 account, each for $3. Exactly 33 transactions can commit; remaining requests must fail without making the balance negative. The same-key retry test launches 20 concurrent posts with one idempotency key and asserts one commit plus 19 replays. The execution race test validates that competing arbitrage commands cannot both spend the same pre-positioned sell inventory.

## Manual extensions

For serious financial model validation, add property-based generators for random postings and currency precision; fuzz malformed serialized quote input; run high-volume soak tests; test process cancellation during waits; add invariant checks against an independent reference ledger; and validate deployment-specific storage adapters against concurrent transactions in a real database.
