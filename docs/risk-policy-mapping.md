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

## Fixed walk-forward v1 mapping

The dedicated walk-forward project preserves the numerical baseline while
closing its operator boundary:

| Control | `WalkForwardMovingAverageV1` |
|---|---|
| Live/optimization | Initialization rejects live mode, requires `optimization-mode=false`, and rejects every name outside the exact two-parameter allowlist |
| Fold/date selection | Only five closed fold IDs; public dates cannot be overridden |
| Account/leverage | Cash brokerage model; leverage fixed to one |
| Direction | Long or flat only; short targets rejected |
| Position/gross exposure | 10% position/target cap and 30% total gross cap |
| Daily loss | 2% inclusive threshold using completed daily information |
| Peak drawdown | 5% inclusive threshold across visible open/close marks |
| Timing | Completed-close signal; earliest fill at next market open |
| Warmup | 50 preceding completed bars; no orders, trades, or evaluation metrics |
| Halt | Latched; pending orders cancelled; later orders blocked; no liquidation |
| Costs | 1 bp fee, USD 1 minimum, and 2 bp adverse slippage |
| Data | Adjusted daily SPY, fill-forward disabled, precise close timestamps |

The no-liquidation row is mission-specific. It matches the local oracle's hold
and valuation policy but deliberately differs from the historical
`MovingAverageBaseline`, which schedules a conservative next-open flattening
order. Neither older behavior nor historical parity evidence is reinterpreted.

All values are identical across the five predeclared folds and cannot be tuned
from results. Daily data still cannot prove an intraday stop. A reported halt or
completed aggregate is research-contract evidence, not a profitability,
paper-readiness, or live-readiness decision.
