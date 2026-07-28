# MovingAverageBaseline

This is a deterministic cloud-backtest baseline, not a profitability claim or
a live-trading candidate.

## Contract

- The reviewed/default cloud run uses adjusted SPY daily bars from 2020-01-01
  through 2021-01-01 with USD 100,000.
- Cash brokerage model, leverage fixed at 1, long-only targets.
- A 20-day trailing average above a 50-day trailing average requests 10% SPY;
  otherwise it requests cash.
- The reviewed cloud configuration warms up with 50 completed daily bars. No
  order is submitted during warm-up or before the slow window is ready. The
  validated `warmup-bars=0` override exists only for a synthetic fixture that
  has no pre-start history; trailing-window readiness still requires 3 closes.
- Fill-forward is disabled, and any unexpected fill-forward bar is ignored
  before it can update the signal or create an order.
- A completed close creates a target. Execution uses only a market-on-open order
  for the next session; there is no same-bar close fill.
- Position exposure is capped at 10% and total gross exposure at 30%.
- Fees are `max(abs(order value) * 1 bps, USD 1)` per non-zero order.
- Constant 2 bps slippage moves each simulated fill adversely through LEAN's
  fill model.
- The 2% daily-loss and 5% peak-drawdown guards are inclusive and latch for the
  rest of the run. Checks occur on completed daily closes and after fills.
- Before creating a signal order, the exchange calendar must expose a next
  market open within the configured end date. A final-close target otherwise
  expires without an intent or fabricated terminal fill. Any unrelated pending
  order is still canceled at algorithm end.

## Component boundary

- `MovingAverageSignalModel`: trailing completed-close signal only.
- `LongOnlyPortfolioModel`: validates non-negative targets against independent
  position and gross-exposure caps.
- `LatchedRiskModel`: daily-loss and peak-drawdown state.
- `NextOpenExecutionModel`: the only component allowed to create orders, and it
  creates market-on-open orders only.

The algorithm uses these Framework-style components through a small explicit
orchestrator instead of LEAN's default immediate framework execution. This
hybrid is deliberate: it preserves the strict completed-close signal followed
by next-session MOO execution.

`SPY` is the reviewed/default cloud symbol. The validated `symbol` parameter is
present only so an offline local LEAN parity run can select the committed
synthetic `PARITY` ticker after its derived LEAN-format data is prepared; the
two required cloud commands do not override it.

## Intentional oracle difference

The local Python oracle latches a circuit breaker without automatically
liquidating. This LEAN baseline is more conservative operationally: after a
halt, it cancels pending orders and requests liquidation at the next market
open. If there is no next session in the configured window, the pending
liquidation is canceled at shutdown and no fill is invented. Parity analysis
must classify this as an expected policy difference.

Cloud data calendars, adjusted prices, integer equity lot behavior, LEAN cash
settlement, and its fill engine can also differ from the local CSV simulator.
Compare signal dates, order types, fill timestamps, exposure, and risk events
before comparing headline return.
