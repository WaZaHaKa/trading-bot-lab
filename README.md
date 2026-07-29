# trading-bot-lab

Public, research-first platform for deterministic stock and cryptocurrency
backtesting, QuantConnect LEAN cloud research, and fully local historical paper
replay.

LEAN cloud backtests are the primary cross-asset research path. The repository's
Python runtime does not connect to a network, broker, exchange, or paid data
service and remains the independent regression/accounting oracle. No component
has a live-trading mode. Backtests are hypothetical, synthetic sample results
are not meaningful market results, past performance does not guarantee future
results, and nothing in this repository is financial advice.

## Active architecture

LEAN is the primary strategy engine for cloud research/backtesting. The free
local Python engine remains authoritative for its own deterministic timing,
risk, and accounting contract:

```text
validated UTC OHLCV events
  -> bounded read-only feature/strategy history
  -> target-allocation signal
  -> order intent
  -> deterministic risk decision
  -> simulated next-bar-open fill
  -> position/cash/PnL accounting
  -> benchmarks, reports, and structured local logs
```

The same incremental engine powers batch backtests and historical paper replay.
Strategies cannot mutate portfolio state or submit fills. Every order intent
passes through `trading_bot_lab.risk`.

The active LEAN projects live under `lean-workspace/Strategies/`. The dedicated
`WalkForwardMovingAverageV1` project and `contracts/walk-forward/v1/` bind a
fixed-parameter rolling evaluation to five calendar-year SPY folds that were
declared before any fold result was observed. The workflow is not optimization:
all folds retain the same strategy, risk, cost, account, data, and execution
settings. Its implementation and offline validation include separate canonical-log
and QuantConnect Download Results import paths. Valid private 2021 and 2022
results exist outside the repository and must not be rerun; they are not
tracked evidence.

The pre-existing `lean/` tree remains preserved. See
`docs/lean-integration.md` and ADR 0007.

## Safety defaults

- Backtest mode unless the local historical paper-replay command is explicitly selected.
- LEAN cloud research/backtesting is allowed only through named, manually run projects.
- Paper replay is local CSV playback, not a broker sandbox.
- No live mode, broker/exchange adapter, committed API key, or withdrawal path.
- Long-only, no leverage, no margin, no derivatives, no shorting, and no market making.
- 10% maximum pre-trade asset weight and 30% maximum total gross exposure.
- 2% daily-loss and 5% peak-to-equity drawdown circuit breakers.
- Circuit breakers latch for the rest of a simulation; safe liquidation is not automatic.
- Models are not used in the active path and can never submit or approve orders.

## Repository layout

```text
src/trading_bot_lab/domain.py       canonical immutable domain contracts
src/trading_bot_lab/backtesting/   CSV boundary, strategies, engine, reports
src/trading_bot_lab/risk/          independent fail-closed risk policy
src/trading_bot_lab/paper.py       local historical replay lifecycle
src/trading_bot_lab/observability.py bounded JSON-lines logging
src/trading_bot_lab/artifacts.py    atomic local artifact writes
src/trading_bot_lab/provenance.py   path-safe content provenance
src/trading_bot_lab/cli.py          package CLI
src/trading_bot_lab/walk_forward/   fixed v1 contract and offline operator
scripts/                            compatible convenience entry points
tests/                              unit, integration, regression, and hygiene tests
data/sample/                        committed synthetic demo data only
data/local/                         ignored user-provided local data
reports/ and logs/                  ignored generated artifacts
lean-workspace/                     active LEAN cloud project workspace
lean/                               preserved pre-activation LEAN files
contracts/parity/                   versioned cross-engine comparison contract
contracts/walk-forward/v1/          fixed five-fold protocol and evidence schemas
docs/                               policies, workflows, schemas, and ADRs
```

## Setup

Python 3.11 or newer is supported. Python 3.11 is preferred for parity with CI.

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m trading_bot_lab show-config
python -m trading_bot_lab validate-csv
python scripts\run_local_backtest.py
python scripts\run_historical_paper_replay.py
python -m pytest
python -m ruff check .
python -m ruff format --check .
python scripts\preflight_check.py
```

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
python -m trading_bot_lab show-config
python -m trading_bot_lab validate-csv
python scripts/run_local_backtest.py
python scripts/run_historical_paper_replay.py
python -m pytest
python -m ruff check .
python -m ruff format --check .
python scripts/preflight_check.py
```

`make check` runs lint, format-check, tests, and preflight when Make is available.

LEAN setup and the already completed two-project activation history are
documented in `docs/windows-lean-setup.md` and `docs/lean-integration.md`. The
walk-forward helper can print a separate five-command future plan, but cannot
execute it. Cloud commands are never part of `make check` or CI.

## CLI examples

Validate a CSV:

```powershell
python -m trading_bot_lab validate-csv --csv-path data\local\SPY_daily.csv --symbol SPY
```

Run the synthetic smoke test or an ignored local file:

```powershell
python scripts\run_local_backtest.py
python scripts\run_local_backtest.py --csv-path data\local\SPY_daily.csv
python scripts\run_local_backtest.py --fee-bps 1 --slippage-bps 2
```

Export ignored local reports:

```powershell
python scripts\run_local_backtest.py `
  --export-json reports\summary.json `
  --export-csv reports\equity.csv `
  --export-trades-csv reports\trades.csv `
  --export-rejections-csv reports\rejected.csv `
  --export-risk-events-csv reports\risk-events.csv `
  --log-jsonl logs\backtest.jsonl
```

Replay historical rows one at a time through the same risk and accounting core:

```powershell
python scripts\run_historical_paper_replay.py
python scripts\run_historical_paper_replay.py --speed 0.1
python scripts\run_historical_paper_replay.py --kill-switch-after-bars 8 `
  --export-manifest reports\paper-session.json `
  --export-equity-csv reports\paper-equity.csv `
  --export-trades-csv reports\paper-trades.csv `
  --export-rejections-csv reports\paper-rejections.csv `
  --export-risk-events-csv reports\paper-risk-events.csv `
  --log-jsonl logs\paper-session.jsonl
```

## Fixed walk-forward v1 operator

The operator defaults to a read-only, network-free plan. `validate` checks the
versioned protocol, schemas, project source, and public configuration;
`print-cloud-commands` prints exactly five named commands without running them:

```powershell
python scripts\run_walk_forward_v1.py
python scripts\run_walk_forward_v1.py validate
python scripts\run_walk_forward_v1.py print-cloud-commands
```

The other local phases process already-existing ignored evidence after a future
authorized session: `extract` normalizes one raw log, `aggregate` requires all
five exact fold observations, and `evidence` recomputes and displays the
aggregate. They do not invoke LEAN or a network. Raw logs and cloud output stay
ignored; only separately reviewed, sanitized, content-bound evidence may be
tracked.

Actual execution of the five printed commands requires separate human
authorization for exactly that bounded set. No authorization exists in this
implementation phase, and the helper deliberately has no cloud-run phase. No
data or Object Store operation, broker/exchange connection, paper trade, or
live trade is part of this workflow.

## Execution and accounting assumptions

- CSV `date` values normalize to midnight UTC. Timestamp values must include a timezone.
- The loader never silently sorts or deduplicates data.
- A signal generated after bar N closes may execute only at bar N+1 open.
- Every simulation bar must have a valid open before the run starts; close fallback is prohibited.
- A final-bar signal expires without creating an intent or fill.
- Fill timestamps label the execution bar and `execution_phase=open` identifies the phase.
- Fractional quantities are rounded to 8 decimal places; cash/accounting values to 8 decimals.
- The current money model uses bounded/rounded binary floats, not `Decimal`.
- Target translation accounts for projected fees and adverse slippage so accepted exposure does
  not cross the requested or configured post-cost weight.
- Average cost uses simulated execution prices. Realized and unrealized PnL are gross of fees;
  fees are tracked separately. Slippage is embedded in execution PnL and also estimated separately.
- Open positions remain open at the end and are valued at the last close; forced liquidation is off.
- Daily starting equity is the preceding close and resets only at a UTC date boundary.
- Buy-and-hold enters after configured warm-up, uses the same buy slippage,
  fee/minimum-fee and quantity precision, keeps residual cash nonnegative, and
  remains open at the final close; reports disclose the full methodology.
- Results embed the exact backtest assumptions and effective stricter risk configuration.
- Input provenance uses an exact-byte SHA-256 and safe filename without absolute paths.
- Strategy history is immutable and bounded by `--strategy-history-bars`.

See `docs/local-backtesting.md`, `docs/risk-policy.md`, and
`docs/report-schemas.md` for stable details.

## Data and artifact policy

Only clearly synthetic or redistributable tiny demonstration data may be committed.
Downloaded or user-provided market data belongs under ignored `data/local/`,
`data/raw/`, or `data/processed/` paths. Generated reports, logs, notebook
outputs, session checkpoints, model files, package metadata, and caches stay ignored.
LEAN workspace data, Object Store content, backtests, optimizations, live output,
and caches are also ignored. `lean-workspace/lean.json` is operator-local linkage
state: keep it ignored, preserve any needed backup outside the repository, and
never force-add it.
The CLI accepts in-repository reports only under `reports/` and logs only under
`logs/`; absolute output paths outside the repository are allowed. Ignored
repository-root `.pytest-*` and `.pytest_*` trees are reserved as automated-test
scratch space, not normal report locations. Structured logs also reserve their
configured rotation sidecars so they cannot alias an input or selected report.

## Testing

The suite covers CSV validation, UTC normalization, OHLC consistency, gap and
volume policies, moving averages, final-bar and next-open timing, future-row and
strategy-state protection, costs, cash and average-cost accounting,
realized/unrealized PnL, post-cost exposure, UTC daily loss, opening-peak
drawdown, typed risk failures, latched halts, kill-switch transitions,
costed benchmarks, atomic report schemas, content-bound session identity,
bounded paper replay, zero-bar lifecycle outcomes, structured logs, CLI
integration, ignore rules, repository hygiene, and preservation of the existing
sixteen-dimension parity evidence.

Walk-forward tests close the five-fold manifest, fixed parameters and dates,
warmup isolation, trailing-only signals, next-open timing, no-liquidation halt
behavior, source/configuration hashes, bounded single-observation extraction,
identity rejection, exact-five aggregation, runtime-drift reporting, atomic
writes, raw-output ignores, and the print-only command boundary.

CI runs Python 3.11, Ruff, pytest, preflight, LEAN source/config static checks,
parity-contract tests, and walk-forward contract/static tests on Ubuntu and
Windows. It never authenticates to QuantConnect or invokes a cloud, data,
Object Store, optimization, broker, paper, or live command. Local pytest uses a
repository-local ignored temp directory to avoid Windows temp ACL problems.

## Troubleshooting

- If `python` is missing, activate `.venv` or recreate it with `py -3.11 -m venv .venv`.
- If `.venv\Scripts\python.exe` names a removed installation, recreate the virtual environment;
  launchers are machine-local and should not be copied between machines.
- If any simulation bar lacks `open`, add valid OHLC data; the engine validates the entire sequence
  before mutation and never falls back to a same-bar close.
- Gap and missing-volume warnings can be configured at the CSV boundary. Invalid prices,
  duplicates, inconsistent OHLC, and unsorted timestamps are always fatal.
- Generated outputs should be placed only under ignored local artifact directories.

## Known limitations

- One symbol per simulation; fractional quantities are supported at configurable precision.
- No dividends, splits, corporate actions, borrow, funding, spread, order-book liquidity,
  market impact, partial fills, or exchange calendars.
- No point-in-time universe or survivorship-bias correction.
- UTC calendar dates define daily-loss boundaries; exchange-session calendars are not modeled.
- No advanced annualized statistics; the short synthetic sample cannot justify them.
- Historical replay is synchronous and local, with no restart checkpoint,
  external clock, or broker reconciliation.
- No ML execution path, database, cloud deployment, dashboard, or live trading.

Additional LEAN and cross-engine limitations are maintained in
`docs/known-limitations.md`.

## Roadmap

Fixed walk-forward v1 is implemented and locally testable, with the five folds
and all parameters frozen before results. Only a later, separate authorization
may start exactly five bounded LEAN cloud backtests; evidence review then occurs
offline. There is currently no walk-forward result, profitability or robustness
conclusion, or paper/live readiness. Live trading is not a recommended next
milestone.

## License

This repository is public, but no open-source license is selected. Public
visibility does not by itself grant permission to copy, modify, or redistribute
the code. See `LICENSE_NOT_SELECTED.md`.
