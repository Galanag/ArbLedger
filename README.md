# ArbLedger — High-Frequency Arbitrage & Ledger Simulator

**Version:** 1.0.0  
**Implementation:** Python 3.11+ · `asyncio` · `Decimal` · standard library runtime  
**Execution mode:** paper-trading simulation only

ArbLedger is an extensible, concurrency-aware simulator for cross-venue multi-currency arbitrage. It combines a bounded asynchronous event pipeline, time-window quote tracking, a fee-aware opportunity scorer, and an in-memory double-entry journal with per-account locks and idempotent posting.

> **Important boundary:** this repository does not connect to an exchange, broker, bank, or payment network and cannot submit real orders. It is designed for simulation, backtesting, algorithm validation, and engineering exercises. Before adapting the pattern to live money, add exchange-specific compliance and risk controls, durable transactional storage, independent reconciliation, secrets management, and appropriate legal review.

## Capabilities

- **Exact financial arithmetic:** boundary values use `Decimal`; binary floats are rejected by validation helpers.
- **Quote validation:** currency shape, positive and non-crossed bid/ask, aware UTC timestamps, safe identifiers, and monotonic per-stream sequence/time rules.
- **Sliding-window analytics:** amortized O(1) running minimum, maximum, and mean for spread samples; quote deques expire old observations and are bounded by per-stream quote, registered-stream, and spread-sample caps.
- **Cross-venue opportunity scan:** each quote is evaluated against current quotes for other venues in both trade directions, O(V) candidate comparisons per update for V active venues.
- **Fee-aware strategy:** configurable fee basis points, minimum net-profit threshold, minimum edge threshold, quote-age limit, and opportunity TTL.
- **Async worker pool:** bounded `asyncio.Queue`, configurable workers, natural producer backpressure, graceful drain/stop, per-message exception isolation, and metrics.
- **Double-entry journal:** each committed transaction must sum to zero separately for every currency; account balance updates are atomic within the process.
- **Race protection:** accounts are locked in stable sorted order, every balance is validated before mutation, and same-key retries are serialized by a per-idempotency-key lock.
- **Idempotency:** same key + same normalized business payload returns the existing immutable transaction; same key + different payload fails closed.
- **Auditability:** immutable transactions/entries, business references, UTC timestamps, JSON logs, and optional correlation context.
- **Tests and benchmark:** boundary, strategy, queue lifecycle, exact-once retries, overdraft races, integration, and CLI demonstration tests.

## Repository layout

```text
arbledger-simulator/
├── pyproject.toml
├── README.md
├── SECURITY.md
├── Makefile
├── Dockerfile
├── .dockerignore
├── .gitignore
├── src/arbledger/
│   ├── __init__.py
│   ├── __main__.py
│   ├── domain/
│   │   ├── exceptions.py       # typed, expected domain failures
│   │   ├── ledger.py           # account state, locking, idempotency, journal
│   │   ├── models.py           # immutable value objects
│   │   ├── money.py            # minor-unit rounding
│   │   ├── strategy.py         # arbitrage scoring and fee estimation
│   │   └── validation.py       # exact-decimal and boundary validation
│   ├── application/
│   │   ├── execution_engine.py # atomic two-leg paper settlement
│   │   ├── opportunity_engine.py
│   │   ├── pipeline.py         # bounded async worker pool
│   │   └── sliding_window.py   # quote window and running spread statistics
│   ├── infrastructure/
│   │   ├── logging.py          # JSON logs and correlation scope
│   │   └── memory_market.py    # deterministic async quote feed
│   └── interfaces/
│       └── cli.py              # end-to-end demo command
├── tests/
│   ├── conftest.py
│   ├── test_execution_engine.py
│   ├── test_ledger.py
│   ├── test_pipeline_and_adapters.py
│   ├── test_strategy_and_window.py
│   └── test_validation_and_models.py
├── benchmarks/benchmark_simulation.py
└── docs/
    ├── ARCHITECTURE.md
    ├── OPERATIONS.md
    └── TESTING.md
```

## Quick start

Python 3.11 or newer is required. Runtime code has no third-party dependencies.

```bash
# From the repository root
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e '.[test]'

# Run the end-to-end paper-trading demo
arbledger
# or
python -m arbledger --workers 2 --queue-size 8

# Run unit/integration tests and enforce the 95% coverage gate
pytest --cov=arbledger --cov-branch --cov-report=term-missing --cov-fail-under=95

# Run a local throughput benchmark (not an SLA)
PYTHONPATH=src python benchmarks/benchmark_simulation.py --quotes 10000 --workers 4
```

The demo seeds two paper venues, streams EUR/USD quotes, discovers a candidate, posts the buy and sell legs as one balanced journal transaction, then prints JSON execution/metrics events. No network calls are made.

## Core usage

```python
from decimal import Decimal

from arbledger.application.execution_engine import ExecutionEngine
from arbledger.application.opportunity_engine import OpportunityEngine
from arbledger.application.sliding_window import SlidingWindowQuoteBook
from arbledger.domain.ledger import Ledger
from arbledger.domain.strategy import ArbitrageStrategy, StrategyConfig

ledger = Ledger()
executor = ExecutionEngine(ledger)
executor.register_venue("venue_a", {"USD": Decimal("100000"), "EUR": Decimal("0")})
executor.register_venue("venue_b", {"EUR": Decimal("10000"), "USD": Decimal("0")})
executor.register_fee_currency("USD")

opportunity_engine = OpportunityEngine(
    SlidingWindowQuoteBook(window_seconds=3, max_quotes_per_stream=500, max_streams=100),
    ArbitrageStrategy(StrategyConfig(fee_bps=Decimal("2"), min_net_profit=Decimal("0.05"))),
    quantities={"EUR/USD": Decimal("100")},
)
# Submit PriceQuote values to the engine or attach it to MarketPipeline.
# Execute a returned opportunity via:
# result = await executor.execute(candidate, idempotency_key="order-intent-0001")
```

Call `register_venue` and `register_fee_currency` during setup, before starting concurrent workers. Use unique, stable idempotency keys per logical execution request. Same-key retries intentionally do not post a second time.

## Financial and concurrency semantics

- A quote for `EUR/USD` expresses USD per EUR. `bid` is where the venue buys EUR; `ask` is where it sells EUR.
- An opportunity buys the base currency at one venue's ask and sells the same base quantity at another venue's bid.
- Fees are estimated per leg in the quote currency, rounded to configured minor units (defaults to 2 decimals; override entries include common 0-, 3-, and 8-decimal units).
- Wallet accounts reject negative resulting balances. Clearing accounts are permitted to go negative because they represent counterparties; fee accounts record earned fees.
- Every journal posting set balances by currency, not by converting unlike currency values into one scalar.
- `Ledger` is an in-memory, **single-process** implementation. Its locks protect coroutines sharing one instance; they do not coordinate separate processes or survive a crash. A durable multi-process implementation must use an ACID database transaction, account row locks or serializable isolation, a unique idempotency constraint, and an outbox/reconciliation strategy.
- Opening balances are configured seed positions. All subsequent changes go through a balanced journal.

## Quality policy

The tests exercise valid and invalid input classes, malformed currencies/identifiers, timestamp ordering, quote aging, window expiry, fee rounding, balanced posting requirements, atomic overdraft rejection, concurrent withdrawals, idempotency collisions, execution inventory races, queue shutdown, handler isolation, and CLI operation. The `pytest` configuration fails the run below 95% statement/branch coverage.

See [architecture](docs/ARCHITECTURE.md), [operations](docs/OPERATIONS.md), [testing](docs/TESTING.md), and [security notes](SECURITY.md) for implementation and deployment boundaries.
