# trading-bot-lab

Private, research-first platform for deterministic stock and cryptocurrency
backtesting and fully local historical paper replay.

The active platform does not connect to a network, broker, exchange, or paid
data service. It has no live-trading mode. Backtests are hypothetical, synthetic
sample results are not meaningful market results, past performance does not
guarantee future results, and nothing in this repository is financial advice.

## Active architecture

The free local Python engine is authoritative:

```text
validated UTC OHLCV events
  -> read-only feature/strategy history
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

QuantConnect LEAN files remain under `lean/`, but LEAN is paused because the
attempted CLI workflow required a paid QuantConnect organization. LEAN is not
part of the active MVP and its files must not be removed. See
`docs/lean-paused.md`.

## Safety defaults

- Backtest mode unless the historical paper-replay command is explicitly selected.
- Paper replay is local CSV playback, not a broker sandbox.
- No live mode, broker/exchange adapter, API key, withdrawal path, or network call.
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
src/trading_bot_lab/cli.py          package CLI
scripts/                            compatible convenience entry points
tests/                              unit, integration, regression, and hygiene tests
data/sample/                        committed synthetic demo data only
data/local/                         ignored user-provided local data
reports/ and logs/                  ignored generated artifacts
lean/                               preserved, paused LEAN workspace
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
python scripts\run_paper_replay.py
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
python scripts/run_paper_replay.py
python -m pytest
python -m ruff check .
python -m ruff format --check .
python scripts/preflight_check.py
```

`make check` runs lint, format-check, tests, and preflight when Make is available.

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
  --log-jsonl logs\backtest.jsonl
```

Replay historical rows one at a time through the same risk and accounting core:

```powershell
python scripts\run_paper_replay.py
python scripts\run_paper_replay.py --replay-speed-seconds 0.1
python scripts\run_paper_replay.py --kill-switch-after-bars 8 `
  --export-json reports\paper-session.json `
  --log-jsonl logs\paper-session.jsonl
```

## Execution and accounting assumptions

- CSV `date` values normalize to midnight UTC. Timestamp values must include a timezone.
- The loader never silently sorts or deduplicates data.
- A signal generated after bar N closes may execute only at bar N+1 open.
- An actionable pending signal fails closed if the next bar has no open price.
- Fractional quantities are rounded to 8 decimal places; cash/accounting values to 8 decimals.
- The current money model uses bounded/rounded binary floats, not `Decimal`.
- Average cost uses simulated execution prices. Realized and unrealized PnL are gross of fees;
  fees are tracked separately. Slippage is embedded in execution PnL and also estimated separately.
- Open positions remain open at the end and are valued at the last close; forced liquidation is off.
- Buy-and-hold starts at the first open when present and is an uncosted comparison baseline.

See `docs/local-backtesting.md`, `docs/risk-policy.md`, and
`docs/report-schemas.md` for stable details.

## Data and artifact policy

Only clearly synthetic or redistributable tiny demonstration data may be committed.
Downloaded or user-provided market data belongs under ignored `data/local/`,
`data/raw/`, or `data/processed/` paths. Generated reports, logs, notebook
outputs, model files, package metadata, and caches stay ignored.

## Testing

The suite covers CSV validation, UTC normalization, OHLC consistency, gap and
volume policies, moving averages, signal timing, future-row protection, costs,
cash and average-cost accounting, realized/unrealized PnL, exposure, drawdown,
risk rejections, latched halts, kill-switch transitions, benchmarks, report
schemas, paper replay, CLI integration, ignore rules, and repository hygiene.

CI runs Python 3.11, Ruff, pytest, and preflight on Ubuntu. Local pytest uses a
repository-local ignored temp directory to avoid Windows temp ACL problems.

## Troubleshooting

- If `python` is missing, activate `.venv` or recreate it with `py -3.11 -m venv .venv`.
- If `.venv\Scripts\python.exe` names a removed installation, recreate the virtual environment;
  launchers are machine-local and should not be copied between machines.
- If a trade is due and `open` is absent, add valid OHLC data; the engine will not fall back to a
  same-bar close.
- Gap and missing-volume warnings can be configured at the CSV boundary. Invalid prices,
  duplicates, inconsistent OHLC, and unsorted timestamps are always fatal.
- Generated outputs should be placed only under ignored local artifact directories.

## Known limitations

- One symbol per simulation and fractional shares only.
- No dividends, splits, corporate actions, borrow, funding, spread, order-book liquidity,
  market impact, partial fills, or exchange calendars.
- No point-in-time universe or survivorship-bias correction.
- Daily loss uses the prior processed bar's closing equity as start-of-day equity.
- No advanced annualized statistics; the short synthetic sample cannot justify them.
- Historical replay is synchronous and local, with no external clock or broker reconciliation.
- No ML execution path, database, cloud deployment, dashboard, or live trading.

## Roadmap

The next safe milestone is broader offline historical-data testing, followed by
walk-forward validation and Monte Carlo trade-sequence analysis. Simulated
multi-asset portfolio support and shadow model interfaces remain later options.
Live trading is not a recommended next milestone.

## License

No open-source license is selected. Keep the repository private unless the owner
explicitly decides to publish a reviewed subset.
