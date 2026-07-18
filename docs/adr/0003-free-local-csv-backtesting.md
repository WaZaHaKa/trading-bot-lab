# ADR 0003 - Use free local CSV backtesting for the MVP

## Status

Accepted for current MVP.

## Context

The project needs a no-cost, local, backtest-only path. The attempted
QuantConnect LEAN CLI workflow requires a paid QuantConnect organization, and
the owner does not want to spend money for this milestone.

## Decision

Use a small local Python CSV backtesting harness as the active MVP path.
Preserve the existing `lean/` folders for later, but do not continue LEAN CLI
setup during this milestone.

The local MVP must remain:

- backtest-only,
- dependency-light,
- synthetic/demo-data friendly,
- long-only,
- no leverage,
- no shorting,
- no paid services,
- no broker or exchange credentials,
- no AI, ML, neural networks, or reinforcement learning.

## Consequences

Positive:

- No paid services are required.
- The harness is easy to test.
- Risk policy integration stays visible and deterministic.
- Synthetic/demo data can be committed safely for smoke tests.

Negative:

- The engine remains a single-symbol research simulator rather than a market or
  broker emulator.
- Dividends, corporate actions, point-in-time universes, exchange calendars,
  spread, market impact, and partial fills remain future work.
- LEAN parity is deferred.

## Follow-up

ADR 0004 adds the shared event core, realistic configurable costs, stable
reports, latched risk state, and local historical paper replay while preserving
this no-cost decision.
