# LEAN pause decision history

## Status

The original pause was superseded by ADR 0007 on 2026-07-28, when the owner
authorized the activation, parity, and fixed walk-forward research phases.
Those phases are now complete.

Current Phase 0 status, recorded 2026-08-02:

```text
LEAN_FIXED_WALK_FORWARD_V1_COMPLETE
LIVE_STOCK_DEPLOYMENT_DEFERRED
REASON: CAPITAL_AND_INFRASTRUCTURE_ECONOMICS
ACTIVE_DEVELOPMENT_TARGET: SEPARATE_FREQTRADE_CRYPTO_SPOT_PROJECT
```

The completed LEAN implementation and evidence remain preserved and frozen as
the stock-research reference. No fixed fold rerun or live stock deployment is
authorized. See `project-status.md`.

This document records why LEAN work was previously paused. It is retained as
decision history; the Phase 0 record above is the current operating status.

Original reason:

- The current local LEAN CLI path requires a paid QuantConnect organization for
  the workflow the owner tried to use.
- The owner does not want to spend money on this milestone.

Original decision:

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
