# Operations guide

## Runtime dependencies

- Python 3.11+
- No runtime PyPI packages required
- `pytest`, `pytest-asyncio`, and `pytest-cov` only for development/test

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m arbledger --workers 4 --queue-size 1000 --log-level INFO
```

`--workers` controls concurrent queue consumers. `--queue-size` bounds waiting quotes; producers block while full, applying backpressure. Increasing workers does not guarantee higher throughput because the opportunity engine uses mutable in-memory windows and Python async work is not CPU parallelism. Measure on target data before tuning.

## Monitoring

The pipeline exports a `PipelineMetrics` snapshot with submitted/processed quotes, candidate count, failures, queue depth, and lifecycle state. A production adapter can periodically publish this structure to its telemetry system. JSON logs include UTC timestamp, log level, logger, message, correlation ID, and selected fields such as venue/opportunity/worker when supplied.

Errors are captured in `MarketPipeline.errors` so a malformed message or failing handler does not silently terminate the pool. The embedding application must monitor the failure count and decide whether to alert, drop/retry, or stop intake. Do not blindly retry non-idempotent downstream side effects.

## Shutdown semantics

1. Stop accepting new submissions at the caller.
2. Call `await pipeline.stop()`.
3. The pipeline queues one sentinel per worker after existing messages, waits for queue acknowledgements, joins workers, and marks itself stopped.
4. Treat a shutdown exception as a failed drain and reconcile any downstream side effects before restart.

The pipeline is intentionally single-use; create a new instance to restart processing with a fresh lifecycle.

## Benchmark

```bash
PYTHONPATH=src python benchmarks/benchmark_simulation.py --quotes 100000 --workers 4
```

The benchmark uses generated quotes and the in-memory ledger-free detection path; it is not an execution throughput SLA and does not model network latency, persistence, CPU contention, or realistic market distributions. Record Python version, hardware, worker count, queue size, and workload when comparing results.

## Production migration checklist

- Use durable SQL transactions; enforce a unique idempotency key constraint.
- Use row-level locking or serializable isolation and a documented deadlock retry policy.
- Persist raw quotes, candidate decisions, rejected reasons, and ledger entries immutably.
- Add reconciliation across quote input, accepted opportunities, transactions, fees, and balances.
- Apply exposure, notional, loss, and per-venue limits before execution.
- Use trusted time synchronization and explicit event-time/receive-time policies.
- Add secret management, authentication, authorization, API quotas, and operational access controls before exposing any endpoint.
- Validate fees, increments, minimum notional, quantity precision, settlement calendars, and currency minor units against authoritative venue/currency metadata.
- Add integration contracts and failure-mode tests before connecting any external service.
