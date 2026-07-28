# LEAN pause decision (superseded)

## Status

Superseded by ADR 0007 on 2026-07-28.

Current status: **LEAN active for cloud research/backtesting; live trading remains prohibited.**

This document records why LEAN work was previously paused. It is retained as
decision history and is not the current operating policy.

Reason:

- The current local LEAN CLI path requires a paid QuantConnect organization for
  the workflow the owner tried to use.
- The owner does not want to spend money on this milestone.

Decision:

- Do not delete `lean/`.
- Do not add QuantConnect credentials, organization IDs, cloud tokens, broker
  credentials, exchange keys, or paid data-vendor keys.
- Do not continue LEAN CLI setup during the free local MVP.
- Use the local CSV backtesting harness under `src/trading_bot_lab/backtesting/`
  for the next milestone.

The revisit criteria were satisfied when the owner obtained a Quant Researcher
subscription and approved this activation/parity sprint. The old `lean/` files
remain preserved until cloud migration is verified.

Historical revisit criteria:

- The owner explicitly approves LEAN spending or a verified free local LEAN path.
- Risk checks and local backtests are stable.
- Any LEAN reactivation remains backtest-only unless a later task explicitly
  approves paper trading.
