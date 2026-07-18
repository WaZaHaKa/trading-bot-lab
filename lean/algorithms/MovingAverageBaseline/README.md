# MovingAverageBaseline

Experimental LEAN baseline for local backtesting only.

Purpose:

- smoke-test a deterministic strategy loop,
- exercise long-only position changes in LEAN,
- keep position size capped at 10% of portfolio value,
- include daily loss and drawdown halt placeholders,
- avoid AI, optimization, leverage, shorting, live trading, and performance claims.

Baseline rules:

- Universe: SPY only.
- Data resolution: daily bars.
- Signal: 20-day simple moving average above or below 50-day simple moving average.
- Exposure: 10% long when the fast average is above the slow average, otherwise cash.
- Risk halts: stop trading and liquidate if the 2% daily loss or 5% drawdown threshold is reached.

Backtest plan:

1. Run the no-trade `SkeletonBacktest` first.
2. Run this baseline over a short known window to verify LEAN wiring.
3. Add fees, slippage, and out-of-sample windows before treating any result as evidence.
4. Record observations in `docs/strategy-log.md`; do not commit generated LEAN result dumps.
