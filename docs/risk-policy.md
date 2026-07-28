# Risk policy

Risk is independent of strategy logic and fails closed. Every translated order
intent must receive a typed deterministic decision before the simulator can
apply a fill.

This document defines the local oracle. LEAN applies the same policy intent
through independent framework components; exact mappings and intentional
differences are recorded in `risk-policy-mapping.md`.

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
| Daily-loss circuit breaker | 2% of UTC start-of-day equity |
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
- Internally consistent current/resulting quantity, notional, cash, and exposure projections.
- Non-negative projected quantity and exposure; no shorting.
- Authoritative portfolio cash including simulated execution notional and fees.
- Maximum order notional, post-cost asset weight, and post-cost total gross exposure.
- No leverage.
- Maximum open-position count.
- Duplicate/repeated intent ID.
- Daily loss and maximum drawdown.

The policy derives whether a sell reduces risk from its consistent current and
resulting state; a caller-provided `reduces_risk` flag is not authoritative.
Proven risk-reducing sells may reduce an already overweight position without
being blocked by the exposure/notional caps. They still cannot bypass
invalid/stale data, cash/quantity validity, shorting, a kill switch, or a
latched halt. The risk layer never silently resizes an intent. Target allocation
translation may choose a precision-safe quantity that remains under the target
after projected costs, as documented in ADR 0005.

## Portfolio circuit breakers

The engine evaluates daily loss and peak drawdown before a pending next-open
fill, immediately after an accepted fill, and after close valuation. Daily
starting equity is the preceding processed close and remains fixed for all bars
with the same UTC date. It resets only at a UTC date change. Peak equity includes
opening and closing marks. Thresholds are inclusive: reaching 2% or 5% triggers
the breaker. Trading-disabled and kill-switch states also halt.

`HaltState` records timestamp and typed reasons. It is latched for the rest of
the run. Price recovery cannot resume order processing. Backtests continue
valuation after a halt; paper replay moves to its explicit `halted` lifecycle
state. Automatic liquidation is disabled to avoid an unreviewed implicit order.

## Failure behavior

Invalid portfolio/order/policy numbers produce typed rejection reasons. The
engine records all order intents, approvals, rejections, fills, and halt checks.
If a supposedly approved fill would make cash or quantity negative, the engine
raises an invariant error instead of mutating state.
An exception from pre-trade risk evaluation becomes a typed rejected intent;
an exception from portfolio risk evaluation becomes a typed latched halt. Risk
therefore fails closed. Historical `data_age_seconds` is an explicit simulation
assumption; the current local engine has no network clock or vendor freshness feed.

## Future multi-asset work

The policy already carries maximum open-position count and total exposure, but
the current engine is single-symbol. Multi-asset support must add atomic
portfolio snapshots, cross-symbol gross exposure, a reviewed cash reservation
model, and deterministic ordering tests before changing the default scope.

## LEAN cloud boundary

The LEAN baseline is backtest-only, rejects `live_mode`, uses a cash account and
one-times leverage, emits only long/flat targets, caps one symbol at 10% and
gross exposure at 30%, and latches 2% daily-loss or 5% peak-drawdown halts.
Only its execution component may create an order.

LEAN's daily callbacks cannot observe an intraday breach that fully recovers by
the close. Its conservative halt target liquidates at the next open, while the
local oracle halts without liquidation. These are documented model differences,
not claims of exact risk parity.
