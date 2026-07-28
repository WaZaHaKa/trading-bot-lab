# ADR 0005 - Fix execution timing and accounting invariants

## Status

Accepted for Sprint 1.

## Context

The shared simulator already delayed close-generated targets to a later bar, but
the complete contract was not encoded in one place. A missing next open could
fall back to close for a very small target, daily-loss state was effectively
restarted on every bar, an intrabar opening peak could be lost, and reports
could be given configuration objects that differed from the completed run.
Those ambiguities can create lookahead, understate drawdown, or hide the policy
that actually governed a result.

## Decision

Use only the `next_bar_open` execution model:

1. A bar timestamp labels that completed bar in UTC.
2. At the labelled bar's open phase, the engine may process only the target
   produced after the preceding bar closed.
3. After the labelled bar closes, it is appended to the immutable historical
   prefix and becomes visible to the strategy.
4. Indicators and signals may use only that prefix. Rolling calculations are
   trailing-only; centered windows, negative shifts, future rows, and
   whole-dataset feature normalization are outside the strategy boundary.
5. A target produced from bar T cannot fill at T's close. Its earliest normal
   execution is the next input bar's open.
6. Every bar in a batch or historical replay must have a valid positive finite
   `open` before simulation state is mutated. There is no close fallback and no
   alternate next-close mode.
7. A target produced by the final bar expires because no later executable bar
   exists. It creates no intent or fill.

`OrderIntent` and `Fill` record `execution_phase="open"`. Their timestamp is the
UTC timestamp label of the execution bar; the phase removes any implication
that the fill occurred at that bar's close.

The strategy receives immutable `MarketBar` values and no cash, position, fill,
or mutation interface. The engine snapshots and restores its protected state
around a strategy call and rejects detected mutation. This is an in-process
guard for trusted local Python strategy code, not a security sandbox.

Translate a target weight into the greatest quantity representable at the
configured precision that does not exceed that target after projected fee and
adverse slippage erosion. This is deterministic target translation, not a risk
limit resize. A stricter independent risk limit rejects the intent rather than
silently resizing it. The risk layer independently derives projected cash,
post-cost equity, quantity, symbol exposure, total gross exposure, and whether
a sell really reduces risk.

Peak equity includes opening and closing marks. Daily starting equity is fixed
to the preceding processed close for one UTC calendar date, so all intraday
losses accumulate; it resets only when the UTC date changes. Portfolio checks
run before a pending fill, immediately after an accepted fill, and after the
bar close. Circuit breakers remain latched.

Completed results embed the exact `BacktestConfig` and effective (stricter)
risk configuration used by the engine. Report exporters reject mismatched
supplied assumptions. Backtest report schema 1.1 adds explicit execution phase
and daily/peak accounting fields.

Structured event delivery is best-effort observability rather than simulation
state. A local sink exception cannot roll back an already-evaluated strategy or
split a paper cursor from engine history; the run continues and records one
`event_sink_failure` warning so exported results disclose that events may be
missing.

An exception while validating or processing a bar restores engine-owned state
and makes that engine terminal. A paper replay enters `failed` and cannot retry
the bar. Arbitrary state inside trusted strategy objects is intentionally not
snapshotted, so treating such a failure as retryable would be nondeterministic.

Python floats remain the money and quantity representation, rounded at explicit
configurable precision. A reviewed fixed-point or `Decimal` migration remains
future work before any settlement, multi-currency, or real reconciliation use.

## Consequences

Positive:

- Same-bar close fills and missing-open fallbacks are structurally prohibited.
- Batch and replay inputs fail before partial portfolio mutation.
- Intraday daily loss, opening peaks, post-cost exposure, and report assumptions
  are directly testable and auditable.
- Risk evaluation errors become typed rejections or typed halts.

Tradeoffs:

- Close-only CSV files are valid for inspection but incompatible with simulation.
- Cost-aware target translation may produce a quantity slightly below the raw
  pre-cost target.
- The single-symbol float simulator remains a research tool rather than a market
  or broker emulator.

## Safety scope

Results are hypothetical. The committed sample is synthetic. Same-bar close
execution is prohibited. Live trading is not implemented. No profitability is
claimed, and this project is not financial advice.
