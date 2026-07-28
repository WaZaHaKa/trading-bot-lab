# SkeletonBacktest

This project validates the smallest safe QuantConnect Cloud path. It subscribes
to adjusted SPY daily bars, sets a cash account with leverage fixed at 1, and
submits no orders.

Contract:

- Backtests from 2023-01-01 through 2023-03-31 with USD 100,000 initial cash.
- Uses SPY as the benchmark and the only subscription.
- Raises immediately if LEAN starts it in live mode.
- Exposes and validates a maximum allocation of 5%, although no code path uses
  the allocation or submits an order.
- Does not use local data, credentials, optimization, research notebooks,
  broker adapters, crypto, derivatives, margin, shorting, or live configuration.

Acceptance evidence is a successful cloud compile/backtest with zero orders and
at least one completed SPY bar. Do not infer strategy performance from this
connectivity smoke test.

