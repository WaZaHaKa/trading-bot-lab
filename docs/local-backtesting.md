# Local backtesting

This is the active free engine while LEAN remains paused.

## Input contract

A CSV requires:

- exactly one of `timestamp` or `date`,
- `symbol`,
- `close`.

`open`, `high`, `low`, and `volume` are supported. A `date` becomes midnight
UTC. A `timestamp` must include an offset and is normalized to UTC. The engine's
default next-bar-open model requires `open` whenever a pending signal can fill
or a held position must be marked at the next open.

Validation rejects empty input, missing columns, multiple symbols, symbol
mismatch, unsorted or duplicated timestamps, naïve timestamps, non-positive or
non-finite OHLC prices, negative/non-finite volume, and inconsistent high/low.
Missing volume is configurable as allow/warn/reject. Large gaps are configurable
as warn/reject and retain their timestamp in a typed warning. Input is never
silently sorted or deduplicated.

## Timing model

At each event:

1. Mark the existing portfolio at bar N open and evaluate circuit breakers.
2. Translate the target produced after bar N-1 close into an order intent.
3. Run the intent through deterministic risk checks.
4. Fill an approved intent at bar N open plus/minus configured slippage.
5. Value cash and positions at bar N close.
6. Evaluate closing-equity circuit breakers and latch any halt.
7. Give the strategy only the immutable history through bar N.
8. Store the resulting target for bar N+1.

Same-bar close generation and same-close execution are not supported. A missing
next open fails closed rather than falling back to close.

## Costs and accounting

Configuration includes starting cash, fee basis points, minimum fee, slippage
basis points, per-asset/order/total exposure limits, daily loss, drawdown,
maximum open positions, warm-up bars, freshness age, and precision.

Approved fills update cash, fractional quantity, weighted average cost, gross
realized PnL, unrealized PnL, cumulative fees, estimated slippage, equity,
exposure, peak equity, and drawdown. Cash and quantity cannot become negative.
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
- uncosted buy-and-hold beginning at the first open when present.

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
  --export-rejections-csv reports\rejections.csv
```

Linux/macOS uses the same arguments with `/` path separators.

## Result metrics

The stable summary includes ending equity, total return, maximum drawdown,
trade count, turnover, fees, estimated slippage, average/maximum exposure,
realized/unrealized PnL, halt status, rejected-intent count, and warning count.

Annualized return, volatility, Sharpe, Sortino, and Calmar are intentionally not
reported. Their assumptions would not be meaningful for the tiny synthetic
fixture and must be added only with documented calendars, sampling, formulas,
minimum sample requirements, and edge-case tests.
