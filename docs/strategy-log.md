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

No profitability hypothesis is being tested. This sprint verifies LEAN cloud
wiring, long-only risk controls, completed-bar/next-open timing, and an
independent synthetic parity contract.

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

Cash account, one-times leverage, 10% per-symbol and 30% total exposure caps,
2% completed-daily loss and 5% peak-drawdown halts, explicit fee/slippage
models, and an initialization-time live-mode rejection remain mandatory.

### Validation status

- Repository and migration audit completed.
- LEAN CLI 1.0.227 located in the repository virtual environment.
- `lean whoami` returned `You are not logged in`; no identity or token was printed.
- Cloud project identifiers/results: pending interactive authentication and
  successful scoped cloud runs. Do not interpret this entry as a completed
  LEAN backtest.
- Native verification after the final review: 246 pytest tests passed; Ruff
  lint and format checks passed; repository preflight passed; `git diff
  --check` returned success with Windows line-ending conversion warnings.
- The local v1 oracle trace processed 8 synthetic weekday bars and recorded one
  buy plus one sell, positive explicit fees/slippage, no risk rejection, and no
  final-bar intent/fill. Its comparator fixture is explicitly not a LEAN
  observation, so cross-engine parity remains unproven.
- Docker Desktop's Linux engine was not available. The offline converter for
  ignored LEAN-format synthetic data is unit-tested, but no local LEAN fixture
  run or normalized LEAN trace was produced.

### Decision

LEAN is active by architecture policy for cloud research/backtesting; live
trading remains prohibited. The old `lean/` tree remains preserved and is not
yet marked superseded.

### Notes

The next safe milestone is walk-forward validation using LEAN cloud backtests
and local parity checks. No optimization or live-trading milestone is approved.
