# Risk-policy mapping

| Control | Local Python oracle | LEAN baseline |
|---|---|---|
| Live trading | Not implemented; live intents rejected | `live_mode` raises during initialization |
| Shorting | Negative target/quantity rejected | Signal and portfolio layers emit only long/flat targets |
| Leverage/margin | Rejected by independent policy | Cash brokerage model, security leverage set to 1 |
| Symbol allocation | 10% maximum | Portfolio target validated and capped at 10% |
| Gross exposure | 30% maximum | Risk layer rejects targets above 30% |
| Order notional | 10% maximum | Single-symbol target is at most 10% |
| Daily loss | 2%, UTC previous-close baseline | 2%, prior completed daily portfolio value |
| Peak drawdown | 5%, opening and closing marks | 5%, values visible at daily framework callbacks |
| Halt | Latched; no liquidation | Latched; next-open flat target |
| Freshness | Explicit simulated age | Fill-forward disabled; unexpected fill-forward bars are ignored before signal/order creation. Wall-clock age is not meaningful in a historical daily backtest |
| Kill switch | Explicit local state | No operator/live path; initialization live guard |
| Risk failures | Typed fail-closed decisions | Logged rejection/halt and no target to execution |

The LEAN signal cannot place orders. Portfolio construction cannot bypass the
risk model. Only the execution component creates orders, and it accepts only
risk-adjusted targets.

Daily bars cannot observe a drawdown that occurs and fully recovers intraday.
The LEAN guard is therefore a completed-daily-information control, not a claim
of intraday stop-loss coverage. Cloud backtests remain hypothetical.
