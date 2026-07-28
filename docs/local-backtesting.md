# Local backtesting

This is the free, deterministic local oracle retained alongside the primary
LEAN cloud research engine. It remains fully usable for regression, accounting,
synthetic parity, and ignored user-CSV backtests; it is not replaced by LEAN.

## Input contract

A CSV requires:

- exactly one of `timestamp` or `date`,
- `symbol`,
- `close`.

`open`, `high`, `low`, and `volume` are supported. A `date` becomes midnight
UTC. A `timestamp` must include an offset and is normalized to UTC. Every bar in
a batch backtest or historical replay must provide `open`; the whole sequence
is validated before portfolio state can change. Close-only CSV can be inspected
by the loader but is incompatible with simulation.

Validation rejects empty input, missing/blank/duplicated/whitespace headers,
extra row values, multiple symbols, symbol mismatch, unsorted or duplicated
timestamps, naive timestamps, blank declared OHLC values, non-positive or
non-finite OHLC prices, negative/non-finite volume, and inconsistent high/low.
`high` and `low` must be declared together.
Missing volume is configurable as allow/warn/reject. Large gaps are configurable
as warn/reject and retain their timestamp in a typed warning. Input is never
silently sorted or deduplicated.

The loader reads the file once, hashes the exact bytes, and parses that same
content. Durable metadata and reports contain only the input filename, never an
absolute machine path. Symbols use a restricted uppercase domain that cannot be
interpreted as a spreadsheet formula.

## Timing model

At each event:

1. Mark the existing portfolio at bar N open and evaluate circuit breakers.
2. Translate the target produced after bar N-1 close into an order intent.
3. Run the intent through deterministic risk checks.
4. Fill an approved intent at bar N open plus/minus configured slippage.
5. Recheck portfolio circuit breakers immediately after the fill.
6. Value cash and positions at bar N close.
7. Evaluate closing-equity circuit breakers and latch any halt.
8. Give the strategy only the configured bounded immutable history through bar N.
9. Store the resulting target for bar N+1.

Same-bar close generation and same-close execution are not supported. A missing
open anywhere in the simulation fails before mutation rather than falling back
to close. The final bar's target expires without an intent or fill. Fill records
use the execution-bar timestamp plus `execution_phase=open`.

## Costs and accounting

Configuration includes starting cash, fee basis points, minimum fee, slippage
basis points, per-asset/order/total exposure limits, daily loss, drawdown,
maximum open positions, warm-up bars, freshness age, and precision.

Approved fills update cash, fractional quantity, weighted average cost, gross
realized PnL, unrealized PnL, cumulative fees, estimated slippage, equity,
exposure, peak equity, UTC start-of-day equity, daily PnL, and drawdown. Cash and
quantity cannot become negative. Target translation selects the greatest
configured-precision quantity that remains at or below the requested allocation
after projected fee and adverse-slippage erosion. Independent risk limits may
still reject that intent; they do not resize it.
The final bar values an open position at close; there is no automatic final or
risk-halt liquidation.

The float/rounding policy and future Decimal migration are documented in
`architecture.md`.

## Strategies and benchmarks

The moving-average strategy is a deliberately simple smoke test. It uses only
trailing closes, produces a long/flat target, has configurable fast/slow windows,
and performs no optimization. `NoTradeStrategy` is a cash control.

Reports compare the strategy with:

- cash/no-trade,
- costed buy-and-hold at the open of bar index `warmup_bars`, if present.

Cash has zero trades, costs, exposure, return, and drawdown. Buy-and-hold uses
the simulator's adverse buy-slippage convention, configured proportional and
minimum fee, and quantity precision. It selects the largest affordable quantity,
keeps residual cash nonnegative, marks every visible close, and leaves the
position open at the end rather than fabricating a sale. Reports expose entry
details, costs, average/maximum exposure, methodology, fractional support, and
the open-position state.

Benchmarks are comparisons, not strategy or profitability claims.

## Commands

Windows PowerShell:

```powershell
python -m trading_bot_lab validate-csv
python scripts\run_local_backtest.py
python scripts\run_local_backtest.py --csv-path data\local\SPY_daily.csv
python scripts\run_local_backtest.py --fee-bps 1 --minimum-fee 1 --slippage-bps 2
python scripts\run_local_backtest.py --warmup-bars 20
python scripts\run_local_backtest.py `
  --export-json reports\summary.json `
  --export-csv reports\equity.csv `
  --export-trades-csv reports\trades.csv `
  --export-rejections-csv reports\rejections.csv `
  --export-risk-events-csv reports\risk-events.csv
```

Linux/macOS uses the same arguments with `/` path separators.

## Result metrics

The stable summary includes ending equity, total return, maximum drawdown,
trade count, turnover, fees, estimated slippage, average/maximum exposure,
realized/unrealized PnL, halt status, rejected-intent count, and warning count.
The completed result and JSON report also embed the exact backtest assumptions
and effective stricter risk configuration used for the run. Schema `1.2.0`
adds exact input content hash, safe filename, explicit execution timing, and
cost/exposure-complete benchmark records. JSON and CSV exports use same-directory
temporary files plus atomic replacement.

Annualized return, volatility, Sharpe, Sortino, and Calmar are intentionally not
reported. Their assumptions would not be meaningful for the tiny synthetic
fixture and must be added only with documented calendars, sampling, formulas,
minimum sample requirements, and edge-case tests.

All results are hypothetical. The committed sample is synthetic. Live trading
is not implemented. No profitability is claimed, and this is not financial advice.
