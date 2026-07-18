# LEAN workspace

This directory is reserved for QuantConnect LEAN projects.

## Current status

LEAN CLI work is paused. The owner hit a paid QuantConnect organization
requirement and does not want to spend money for the current milestone.

Keep these folders for later, but do not add QuantConnect credentials, broker
credentials, exchange keys, paid data-vendor keys, or live-trading config.

The active free MVP path is documented in `../docs/local-backtesting.md`.
See `../docs/lean-paused.md` for the pause decision.

## Initial goal

When LEAN is reactivated later, run a no-trade skeleton backtest locally before
evaluating any trading logic. Then run the experimental long-only
moving-average baseline as a smoke test.

## Expected local workflow later

```bash
lean init
lean backtest "lean/algorithms/SkeletonBacktest"
lean backtest "lean/algorithms/MovingAverageBaseline"
```

Exact commands may differ depending on how you install and configure LEAN CLI.
See `../docs/lean-getting-started.md` for the full local guide.

## Safety

- Do not add live brokerage credentials.
- Do not add QuantConnect cloud credentials to Git.
- Do not commit downloaded LEAN market data.
- Do not commit LEAN backtest results, logs, or generated reports.
- Do not implement live trading in this starter phase.
