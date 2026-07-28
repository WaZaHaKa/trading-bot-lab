# ADR 0004 - Share one event simulation core

## Status

Accepted.

## Context

The original local backtester coupled a moving-average class directly to a
close-only loop. It did not expose canonical order/fill/position contracts,
latched halts, complete accounting, or a safe path to simulated paper replay.
Building a separate replay engine would risk timing, risk, and PnL drift.

## Decision

Use one incremental `SimulationEngine` for batch backtests and historical paper
replay. Normalize data to immutable UTC `MarketBar` events. Strategies receive
only an immutable historical prefix and return target-allocation signals.
Signals generated after a close execute only at the next bar open. Every order
intent passes through the independent risk policy before a simulated fill.

Use explicit typed lifecycle, halt, warning, decision, position, fill, trade,
benchmark, and report contracts. Keep the active implementation standard-library
only. Preserve the original script entry point as a CLI delegate.

## Consequences

Positive:

- Backtest and replay timing/risk/accounting cannot drift independently.
- Future-row access is structurally limited.
- Circuit breakers latch and all decisions are auditable.
- Reports can use stable schemas.
- No Docker, paid service, network call, or new dependency is required.

Tradeoffs:

- Next-open execution requires a valid open on every simulation bar.
- A single-symbol float-based simulator remains less realistic than a mature engine.
- Adding multi-asset state requires a new reviewed portfolio model rather than
  extending single-symbol fields ad hoc.

## Rejected alternatives

- Continue close-only fills: timing was under-specified and easier to misuse.
- Build an independent paper engine: duplicated semantics would create reconciliation risk.
- Reactivate LEAN at that time: conflicted with the then-current no-cost MVP
  constraint. ADR 0007 later activates LEAN cloud while retaining this core as
  an independent oracle.
- Add pandas/backtesting frameworks: unnecessary for the small event/domain core.
