# Strategy log

Use this file to record every strategy idea, experiment, and promotion decision.

## Template

```markdown
## YYYY-MM-DD — Strategy name

### Hypothesis

What market behavior is this strategy trying to exploit?

### Asset universe

Symbols, exchanges, and timeframes.

### Data used

Data source, date range, resolution, corporate-action treatment, fees, and slippage assumptions.

### Signal logic

Plain-English description. Include indicator windows and timing assumptions.

### Risk rules

Position sizing, exposure caps, stop-loss, drawdown halt, and kill-switch behavior.

### Backtest results

Total return, benchmark return, max drawdown, Sharpe/Sortino, win rate, turnover, number of trades.

### Validation

Out-of-sample windows, walk-forward windows, ablations, Monte Carlo, and lookahead checks.

### Decision

Rejected / research only / paper candidate / approved for paper.

### Notes

What changed, what failed, and what to try next.
```

## 2026-07-08 — Initial skeleton

### Hypothesis

No trading hypothesis yet.

### Decision

Repository scaffold only. No strategy implemented.

## 2026-07-08 - Milestone 2 LEAN local workflow verification

### Hypothesis

No performance hypothesis tested. This was a local workflow verification attempt only.

### Asset universe

`SkeletonBacktest` and `MovingAverageBaseline` project folders only.

### Data used

No market data loaded. LEAN backtests did not start.

### Signal logic

No signal results evaluated.

### Risk rules

Backtest-only policy remained in force. No live trading, credentials, leverage, shorting,
AI, or brokerage configuration were added.

### Backtest results

No backtest results. Both requested commands failed before LEAN startup because the
`lean` command is not installed or not on PATH:

```powershell
lean backtest "lean/algorithms/SkeletonBacktest"
lean backtest "lean/algorithms/MovingAverageBaseline"
```

Docker is installed, but the Docker Desktop Linux engine was not running when checked.

### Validation

Repository-only checks passed:

- `lean/algorithms` bytecode compilation
- `scripts/preflight_check.py`

### Decision

Blocked by local tooling. Install/enable local dependencies before rerunning the two
backtest commands.

### Notes

Safe fix:

1. Install or expose Python 3.11+ on PATH, or create `.venv` with a known Python executable.
2. Install the QuantConnect LEAN CLI into that virtual environment.
3. Start Docker Desktop and wait for the Linux engine to report healthy.
4. Rerun only the two local backtest commands above.

## 2026-07-08 - Local CSV realism and safety pass

### Hypothesis

No profitability hypothesis tested. This was a harness safety and realism upgrade.

### Asset universe

Synthetic/demo SPY-shaped daily CSV only by default. User-provided CSVs are
allowed only from ignored local paths such as `data/local/`.

### Data used

`data/sample/synthetic_spy_daily.csv`, which is synthetic/demo data and not real
market data.

### Signal logic

Deterministic moving-average target from historical closes. Signals from bar N
remain eligible only for simulated execution on bar N+1.

### Risk rules

Backtest-only. Simulated orders continue to route through the risk policy.
Daily loss and max drawdown halt checks run before new simulated orders. Max
position, max total exposure, stale data, invalid CSVs, fees, and slippage are
now explicit assumptions.

### Backtest results

Smoke-test output only. No profitability claim.

### Validation

Unit tests cover typed summaries, fee/slippage accounting, exposure rejections,
stale data rejection, invalid CSV validation, daily loss halt, and max drawdown
halt.

### Decision

Research-only local MVP remains active. Not a paper-trading candidate.

### Notes

Next work should add benchmark reporting, richer missing-data checks, and an
ignored local report exporter.

## 2026-07-08 - Local benchmark and report export pass

### Hypothesis

No profitability hypothesis tested. This was a validation and reporting upgrade.

### Asset universe

Synthetic/demo SPY-shaped daily CSV by default. Optional user CSVs remain local
under ignored `data/local/` paths.

### Data used

`data/sample/synthetic_spy_daily.csv`, which is synthetic/demo data and not real
market data.

### Signal logic

Unchanged deterministic moving-average target. Signals remain delayed to the
next bar for simulated execution.

### Risk rules

Existing risk checks remain in force. The pass added stricter CSV validation,
same-CSV buy-and-hold and cash/no-trade baselines, and optional ignored local
JSON/CSV report exports.

### Backtest results

Smoke-test output only. No profitability claim.

### Validation

Tests cover benchmark calculations, JSON report structure, equity CSV export,
Git ignore behavior for generated reports, and bad CSV validation failures.

### Decision

Research-only local MVP remains active. Not a paper-trading candidate.

### Notes

Next work should add benchmark charts or static report rendering only if outputs
remain local artifacts under ignored paths.

## 2026-07-10 - Shared deterministic engine and historical replay

### Hypothesis

No market or profitability hypothesis. This milestone validates platform timing,
risk, accounting, reporting, and local replay behavior.

### Asset universe and data

Single-symbol synthetic SPY-shaped daily OHLCV fixture by default. Local user
data remains ignored. Dates normalize to UTC and the loader rejects unsorted,
duplicated, invalid, or inconsistent data.

### Signal and execution

The moving-average smoke test produces long/flat target allocations from an
immutable historical prefix. A close-generated signal can fill only at the next
bar open. There is no same-close execution or future-row access.

### Risk and accounting

Every intent goes through the typed independent risk engine. Live trading,
shorting, and leverage cannot be enabled. Circuit breakers latch. The shared
engine tracks cash, quantity, average cost, realized/unrealized PnL, fees,
slippage, exposure, equity, and drawdown. Automatic liquidation remains off.

### Validation

Unit and integration tests cover data validation, timing, future-access attempts,
known PnL outcomes, costs, exposure/cash invariants, halts, kill switch, reports,
CLI flows, structured logs, paper lifecycle, and ignored artifacts.

### Decision

Research-only backtesting and local historical paper replay. Not a broker paper
candidate and not a live-trading candidate.

### Notes

Next safe milestone: broader offline historical-data testing. Do not infer a
strategy edge from the synthetic fixture.

## 2026-07-18 - Sprint 1 execution and risk hardening

### Hypothesis

No market or profitability hypothesis was tested. This sprint hardened the
research harness against timing ambiguity, accounting drift, malformed local
CSV data, and caller-controlled risk projections.

### Asset universe and data

Single-symbol synthetic SPY-shaped fixtures only. The committed sample is
synthetic/demo data; no real market data, network call, download, credential,
or paid service was introduced.

### Signal and execution

Strategies still receive only a trailing immutable historical prefix. A signal
from completed bar T can execute no earlier than the next input bar's open.
Same-bar close execution and close fallback are prohibited, every simulation
bar requires a valid open, and a final-bar target expires without a fill.

### Risk and accounting

Order projections are cross-checked against quantity, cash, exposure, and
post-cost equity. Risk-reducing status is derived rather than trusted. Daily
loss accumulates across bars sharing a UTC date, opening peaks persist for
drawdown, and portfolio checks run immediately after fills. Risk errors fail
closed. Live trading, leverage, margin, and shorting remain unavailable.

### Validation

Deterministic synthetic tests cover fee/slippage hand calculations, post-cost
exposure, intraday daily loss, UTC rollover, opening-peak drawdown, final-bar
expiry, risk exceptions, malformed projections, and attempted strategy state
mutation in addition to the existing regression suite.

### Decision

Research-only backtesting and local historical replay. These results are
hypothetical, are not financial advice, and make no profitability claim. Live
trading is not implemented, and this is not a broker-paper candidate.

### Notes

Python floats remain in use with explicit rounding. A reviewed Decimal or
fixed-point migration remains future work before multi-currency settlement or
reconciliation. LEAN stays preserved and paused.

## 2026-07-18 - Sprint 2 reporting and historical replay hardening

### Hypothesis

No market or profitability hypothesis was tested. This sprint validates
reproducibility, benchmark methodology, durable local artifacts, and replay
lifecycle behavior only.

### Asset universe and data

Single-symbol synthetic SPY-shaped fixtures remain the only committed market
data. CSV provenance now hashes the exact bytes parsed and exposes only a safe
filename. In-memory tests use canonical content hashes. No download, network
client, credential, vendor, or paid service was introduced.

### Signal and execution

Batch and historical replay still share next-bar-open execution and one
accounting engine. Strategy input is an immutable bounded visible history. Pause,
resume, stop, kill, completion, and failure use a typed lifecycle. Terminal
controls expire pending signals; no final fill or automatic liquidation is
invented.

### Benchmarks, reports, and logging

Cash remains a zero-cost control. Buy-and-hold applies configured warm-up,
slippage, fee/minimum fee, and quantity precision; keeps nonnegative residual
cash; marks closes; and discloses its open final position. JSON/CSV schema 1.2
adds path-safe content provenance, complete benchmark metrics, trade-state audit
fields, risk events, and atomic replacement. Paper manifests record versions,
seed, event range, terminal reason, and artifact basenames. Structured event
schema 1.0 records selected lifecycle and financial fields locally.

### Validation

Deterministic/adversarial tests cover content-changing session IDs, file-rename
stability, costed benchmark arithmetic, warm-up, open positions, atomic replace
failure, byte-stable controlled exports, zero-bar stop/kill/failure summaries,
idempotent controls, bounded history, replay-speed invariance, event taxonomy,
manifest safety, CLI artifact roots, and spreadsheet-safe symbols. Ubuntu and
Windows CI run Ruff, pytest, and preflight.

### Decision

Research-only backtesting and local historical replay remain active. This is not
a broker-paper or live-trading candidate. Results are hypothetical, no strategy
edge is claimed, and nothing here is financial advice.

### Notes

Stop after local historical replay. Process restart/checkpoint recovery,
multi-asset support, databases, optimization, ML/AI/RL, brokers, exchanges, and
live trading remain out of scope. LEAN remains preserved and paused.

## 2026-07-28 - LEAN activation and parity sprint

### Hypothesis

No profitability hypothesis is being tested. This sprint validates LEAN cloud
wiring and the public long-only project configuration while preserving a
separate synthetic contract for future execution-timing and accounting parity.

### Asset universe and data

The required cloud projects use daily SPY data available to the QuantConnect
research organization. The parity contract uses only a committed synthetic
fixture. No local market data was downloaded or purchased.

### Signal and execution

`SkeletonBacktest` is a no-order engine smoke test. `MovingAverageBaseline`
uses validated trailing fast/slow windows, emits only long/flat targets after
readiness, and routes risk-adjusted targets through a controlled market-on-open
execution component. No final-bar fill is fabricated.

### Risk and costs

Cash account, one-times leverage (no borrowing), 10% per-symbol and 30% total
exposure caps, 2% completed-daily loss and 5% peak-drawdown halts, explicit
fee/slippage models, and an initialization-time live-mode rejection remain mandatory.

### Validation status

- Repository and migration audit completed.
- LEAN CLI 1.0.227 located in the repository virtual environment.
- `lean whoami` returned `You are not logged in`; no identity or token was printed.
- Explicit non-verbose pushes succeeded for both cloud projects. Both projects
  compiled, initialized, and completed backtests under LEAN `2.5.0.0.17942`;
  neither used a live deployment.
- `SkeletonBacktest` observed zero orders, zero holdings, zero fees, and
  unchanged $100,000 starting/ending equity.
- `MovingAverageBaseline` observed 40 orders, $40 simulated fees, 1.3% reported
  maximum drawdown, and $102,118.20 ending equity from $100,000 starting equity.
  These are validation observations, not a profitability or strategy-quality claim.
- Both compiles emitted only the non-fatal stable warning category
  `discouraged_exception_handling`.
- The schema-versioned sanitized record binds source, public configuration,
  and ignored local push/validation logs by SHA-256. It contains no account
  metadata, cloud/backtest IDs, URLs, absolute paths, warning text, or raw logs.
- The hardened schema binds each canonical project to its public parameters,
  finite metric ranges, coherent lifecycle, and derived validation status. The
  typed normalizer additionally enforces same-date UTC timestamps.
- Existing output symlinks are rejected before atomic record writes. The local
  workspace linkage file is ignored but preserved in place, and preflight reads
  staged project configs so index/worktree divergence cannot conceal linkage
  metadata.
- Cloud engine, project synchronization, and source/configuration validation
  are `passed`. Execution-timing and numerical-accounting parity remain
  `pending_identical_data_execution` because the cloud runs used QuantConnect
  SPY data rather than the committed synthetic fixture.
- The local v1 oracle trace processed 8 synthetic weekday bars and recorded one
  buy plus one sell, positive explicit fees/slippage, no risk rejection, and no
  final-bar intent/fill. Its comparator fixture is explicitly not a LEAN
  observation, so cross-engine parity remains unproven.
- The implementation host's Docker Linux engine was not available. The offline
  converter for ignored LEAN-format synthetic data is unit-tested, but no local
  LEAN fixture run or normalized LEAN trace was produced.

### Decision

LEAN is active by architecture policy for cloud research/backtesting; live
trading remains prohibited. The old `lean/` tree remains preserved and is not
yet marked superseded.

### Notes

The next safe milestone is an actual normalized LEAN trace over the committed
synthetic fixture and a content-bound comparison. Walk-forward, optimization,
and live-trading milestones are not approved by this result.

## 2026-07-28 - Identical-data LEAN parity preparation

### Hypothesis

No profitability hypothesis is being tested. This sprint prepares a deterministic
way for LEAN and the independent local Python oracle to process the same
synthetic fixture and compare timing and accounting observations.

### Asset universe and data

One synthetic `PARITY` symbol over the eight committed weekday rows in
`tests/fixtures/parity/v1/synthetic_weekdays.csv`, exact-byte SHA-256
`a68bcf7fc30d2593b32e5a98852c4f8e0190ed99865640485b344515d9f1f78a`.
The `1.0.0` scenario and fixture require LF bytes. Preparation copies only those
validated bytes to ignored LEAN local data; it never downloads, purchases,
normalizes, sorts, or repairs data.

### Signal and execution

`ParityFixtureV1` is fixed to and regression-tested against the existing
versioned scenario; its financial values are not operator-tunable transport
parameters. Trailing-only 2/3 moving-average logic requests a long or
flat 10% target after a completed row. A row-N signal can execute only at row
N+1 open. Future-row access, same-close execution, and a fabricated final-row
intent or fill remain prohibited.

### Risk and costs

The scenario's one-basis-point fee, two-basis-point adverse slippage, integer
quantity, eight-place money precision, nonnegative cash and quantity, 10% asset
and order caps, 30% gross-exposure cap, 2% daily-loss halt, 5% drawdown halt,
and deterministic long-only/no-leverage policy remain unchanged. Live mode,
shorting, brokerage integration, and optimization are rejected.

### Transport and observations

Local file is the offline default. A future cloud run must explicitly select
Object Store and the fixed non-identifying key
`trading-bot-lab/parity/v1/synthetic_weekdays.csv`. Both modes use the same
parser and financial logic; there is no remote URL, upload/download automation,
or network fallback.

A completed run is expected to emit one bounded canonical JSON line prefixed
`TRADING_BOT_LAB_LEAN_PARITY_V1:`. The strict extractor rejects malformed,
duplicate, non-finite, unversioned, identity-bearing, path-bearing, or
credential-bearing observations before the existing comparator can use them.

### Validation status

Deterministic synthetic tests cover fixture identity and LF policy, malformed
data, safe staging, both transport selections, observation extraction and
privacy checks, next-row-open/final-row behavior, stable serialization, and
dimension-specific comparison failures. They do not execute or impersonate
LEAN.

No local LEAN backtest, cloud command, Object Store operation, network request,
or paid-data operation occurred. No `lean_engine_observation` has been produced.
Execution-timing and numerical-accounting parity therefore remain
`pending_identical_data_execution`.

### Decision

Research-only parity workflow prepared for review. Parity is not established,
and the project is not a paper- or live-trading candidate.

### Notes

The next separately authorized step is one manual `ParityFixtureV1` LEAN run,
strict extraction of its ignored prefixed observation, and comparison against a
fresh local-oracle trace. Walk-forward validation, optimization, Object Store
automation, broker paper trading, and live trading remain out of scope.
