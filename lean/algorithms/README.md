# LEAN algorithms

LEAN CLI work is paused for the current no-cost milestone. Keep these algorithm
folders for later, but use `../../docs/local-backtesting.md` for the active
free MVP.

Start with `SkeletonBacktest`, which is intentionally a no-trade algorithm.
Use `MovingAverageBaseline` only after the skeleton runs locally.

Next step after LEAN is installed locally:

1. Confirm the skeleton backtest runs.
2. Run the simple long-only baseline strategy.
3. Add fees, slippage, risk checks, and reporting.
4. Only then consider ML.

`MovingAverageBaseline` is experimental and exists only to smoke-test the
backtesting workflow. It is not optimized and makes no performance claim.
