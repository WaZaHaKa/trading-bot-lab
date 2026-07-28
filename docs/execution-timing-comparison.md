# Execution-timing comparison

## Shared intended sequence

1. Bar N completes.
2. Only closes through bar N are visible to the moving-average signal.
3. A long-or-flat target is produced after readiness.
4. Risk reviews the target.
5. The earliest eligible order execution is bar N+1 market open.
6. A target produced from the final completed bar is canceled/expired and never
   receives a fabricated fill.

## Local oracle

The local input row labels a completed UTC bar. A pending target fills exactly
at the next input row's `open`, after configured adverse slippage. All opens are
validated before the run. There are no partial fills, exchange calendars, or
cash settlement delays. Quantity and money use configured decimal-place
rounding over Python floats.

## LEAN

Daily SPY bars follow the US equity exchange calendar and corporate-action data
normalization. The execution component submits a market-on-open order only
after a completed daily slice. LEAN's equity fill, buying-power, lot-size, fee,
slippage, and cash-settlement models remain authoritative for the cloud run.

A market-on-open order targets the next official opening auction. Before a
signal order is created, the exchange calendar must show that this open falls
within the configured backtest end date; otherwise the final target expires
without an intent. An order can still be rejected or remain unfilled, and the
comparator must not silently invent a fill. Any other open order is canceled at
algorithm end; no end-of-backtest liquidation is created merely to close the
ledger.

## Expected differences

| Area | Local oracle | LEAN cloud |
|---|---|---|
| Calendar | Explicit input rows, UTC labels | US exchange sessions/time zone |
| Quantity | Configurable fractional precision | Equity lot/buying-power model |
| Fill | Deterministic next row open | Official next opening auction model |
| Settlement | Immediate simulated cash | Cash-account settlement model |
| Corporate actions | Not modeled | LEAN data normalization/events |
| Fee notional | Adverse-slippage execution price | Cached security price exposed to LEAN's fee hook |
| Final position | Marked at final close | Marked by LEAN; pending order canceled |

These differences require named tolerances or exclusions in a parity result.
They are not permission to ignore unexplained signal, direction, or timing
divergence.
