# Verification report

**Verification date:** 2026-10-23  
**Runtime used for this report:** Python 3.13.5, pytest 9.0.2  
**Project scope:** local paper-trading simulator only; no live exchange or bank endpoints

## Automated checks

| Check | Result |
|---|---|
| `python -m compileall -q src tests benchmarks` | Passed |
| `pytest --cov=arbledger --cov-branch --cov-report=term-missing --cov-fail-under=95` | **88 passed** |
| Statement + branch-aware package coverage | **99.71%** (above configured 95% gate) |
| `python -m arbledger --workers 2 --queue-size 8` | Passed; 1 paper opportunity executed, 2 quotes processed, 0 pipeline failures |
| `PYTHONPATH=src python benchmarks/benchmark_simulation.py --quotes 2000 --workers 4` | Completed; 2,000 quotes, 1,999 detected opportunities, 0 failures |
| Editable package build/install | Passed with `python -m pip install -e . --no-build-isolation --no-deps` |

The benchmark reported approximately **18,473.8 quotes/second** in the tested container at verification time. This is a single synthetic smoke run, subject to scheduler and machine variation; it is not a production SLA or a comparison with exchange-grade HFT systems.

## Concurrency and financial invariants exercised

- Concurrent withdrawal load: 50 attempts against a $100 wallet at $3 each; 33 committed, 17 rejected, final wallet balance remained $1 rather than going negative.
- Idempotency load: 20 concurrent writes with the same key and same payload; exactly 1 transaction committed and 19 callers received replay results.
- Competing arbitrage execution: two commands competing for the same sell inventory could not both execute; the loser left no partial journal changes.
- Multi-currency ledger entries balanced independently per currency.
- Invalid, stale, replayed, non-monotonic, and out-of-window quote paths were tested.
- Queue shutdown, bounded diagnostic history, malformed messages, and handler failures were exercised.

## Environment note

An initial editable install with normal build isolation attempted to download build requirements and could not resolve package indexes in the isolated environment. The project was then installed and run successfully without build isolation using the already installed setuptools; direct `PYTHONPATH=src` execution also passed. This is an environment/network limitation, not a runtime dependency: ArbLedger's application code uses only the Python standard library.

## Scope limits

This report verifies the in-memory simulation. It does not certify exchange connectivity, risk policies, legal or compliance status, market profitability, real-time latency, durability, cross-process locking, or production deployment. Those require deployment-specific adapters and independent review.
