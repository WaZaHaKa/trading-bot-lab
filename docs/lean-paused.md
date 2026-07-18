# LEAN paused

QuantConnect LEAN project folders remain in this repository for a future phase,
but LEAN CLI work is paused for now.

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

Revisit criteria:

- The owner explicitly approves LEAN spending or a verified free local LEAN path.
- Risk checks and local backtests are stable.
- Any LEAN reactivation remains backtest-only unless a later task explicitly
  approves paper trading.
