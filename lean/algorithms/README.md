# LEAN algorithms

These are preserved pre-activation drafts. Active projects live under
`../../lean-workspace/Strategies/`. Do not delete or mark this tree superseded
until both cloud backtests complete and migration is reviewed.

Start with `SkeletonBacktest`, which is intentionally a no-trade algorithm.
Use `MovingAverageBaseline` only after the skeleton runs locally.

Historical verification plan:

1. Confirm the skeleton backtest runs.
2. Run the simple long-only baseline strategy.
3. Add fees, slippage, risk checks, and reporting.
4. Stop after backtest/parity verification; ML and live trading are not approved.

`MovingAverageBaseline` is experimental and exists only to smoke-test the
backtesting workflow. It is not optimized and makes no performance claim.
