# Risk policy

Risk is independent of strategy logic and fails closed. Every translated order
intent must receive a typed deterministic decision before the simulator can
apply a fill.

## Safe defaults

| Control | Default |
|---|---:|
| Live trading | Not implemented |
| Historical paper replay | Disabled unless command selected |
| Leverage / shorting | Disabled and not configurable |
| Maximum asset weight | 10% of equity |
| Maximum order notional | 10% of equity |
| Maximum total gross exposure | 30% of equity |
| Maximum open positions | 1 |
| Daily-loss circuit breaker | 2% of prior close/start-of-day equity |
| Peak drawdown circuit breaker | 5% |
| Default freshness threshold | 300 seconds |
| Automatic halt liquidation | Disabled |

The engine combines `BacktestConfig` and `RiskPolicy` by choosing the stricter
value for overlapping limits.

## Pre-trade checks

- Trading-enabled and latched-halt state.
- Manual kill switch.
- Live-order marker (always rejected).
- Non-empty allowlisted symbol.
- Valid data and non-negative freshness age.
- Staleness threshold.
- Positive finite quantity, reference price, execution price, and notional.
- Non-negative projected quantity and exposure; no shorting.
- Available cash including simulated execution notional and fees.
- Maximum order notional, asset weight, and total gross exposure.
- No leverage.
- Maximum open-position count.
- Duplicate/repeated intent ID.
- Daily loss and maximum drawdown.

Risk-reducing sells may reduce an already overweight position without being
blocked by the exposure/notional caps. They still cannot bypass invalid/stale
data, cash/quantity validity, shorting, a kill switch, or a latched halt.
Orders are never silently resized.

## Portfolio circuit breakers

The engine evaluates daily loss and peak drawdown before a pending next-open
fill and after close valuation. Thresholds are inclusive: reaching 2% or 5%
triggers the breaker. Trading-disabled and kill-switch states also halt.

`HaltState` records timestamp and typed reasons. It is latched for the rest of
the run. Price recovery cannot resume order processing. Backtests continue
valuation after a halt; paper replay moves to its explicit `halted` lifecycle
state. Automatic liquidation is disabled to avoid an unreviewed implicit order.

## Failure behavior

Invalid portfolio/order/policy numbers produce typed rejection reasons. The
engine records all order intents, approvals, rejections, fills, and halt checks.
If a supposedly approved fill would make cash or quantity negative, the engine
raises an invariant error instead of mutating state.

## Future multi-asset work

The policy already carries maximum open-position count and total exposure, but
the current engine is single-symbol. Multi-asset support must add atomic
portfolio snapshots, cross-symbol gross exposure, a reviewed cash reservation
model, and deterministic ordering tests before changing the default scope.
