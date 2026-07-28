# ADR 0006: Content-bound replay and atomic reports

- Status: Accepted
- Date: 2026-07-18

## Context

Sprint 1 established next-bar execution, fail-closed risk checks, and shared
accounting. Sprint 2 needs reproducible historical replay and durable artifacts.
The earlier report contract stored caller paths, did not bind session identity
to market-data content, wrote destinations directly, used an uncosted benchmark,
and could not summarize a terminal replay before its first committed bar.

Those gaps could make two different inputs appear to be the same run, expose a
machine path, truncate a known-good report, or leave lifecycle failures without
a durable manifest.

## Decision

1. CSV ingestion reads exact bytes once, hashes those bytes with SHA-256, then
   decodes and parses the same content. A second canonical normalized-bar hash
   binds typed events to metadata; in-memory bars use that canonical hash.
2. Durable provenance stores a safe filename, never an absolute input path.
   Session identity includes the content hash and stable input shape/interval but
   excludes filename and machine path.
3. Report and paper-manifest schema deliberately advances from `1.1.0` to
   `1.2.0`. Every exporter uses a sibling temporary file, flush/close, and
   `os.replace`.
4. Cash remains a zero-cost/zero-exposure control. Buy-and-hold enters at the
   first open after configured warm-up, uses simulator buy slippage, fee/minimum
   fee, and quantity precision, retains nonnegative residual cash, marks closes,
   and does not fabricate a final sale.
5. Batch and paper modes continue to share `SimulationEngine`. Strategy history
   is immutable and bounded by `strategy_history_limit`; the engine retains its
   complete internal ledger separately.
6. Paper lifecycle is a typed transition table with `CREATED`, `VALIDATED`,
   `RUNNING`, `PAUSED`, `STOPPED`, `HALTED`, `COMPLETED`, and `FAILED`. Stop and
   kill can produce zero-bar summaries. Terminal controls expire pending signals.
7. Paper manifests record schema/session/mode, input hash, strategy/risk/cost
   configuration, execution timing, Python/package/engine versions, declared
   random seed, event range, terminal reasons, and artifact basenames.
8. Structured JSON-lines events use schema `1.0.0`, explicit event names, and a
   selected primitive-field allowlist. Sink failure is disclosed without
   changing committed financial state.
9. In-repository CLI artifacts are restricted to ignored `reports/` and `logs/`;
   local data is restricted to documented data roots. Ignored repository-root
   `.pytest-*` and `.pytest_*` scratch trees are an automated-test exception.
   Rotated log sidecars (`.1` and `.2` by default) are reserved outputs and are
   collision-checked with the input and every selected report before opening the
   sink. Checkpoint paths are reserved and ignored, but process restart remains
   out of scope.
10. The package advances from `0.2.0` to `0.3.0`. The low-level exported
    `SimulationEngine` now requires the complete validated bar sequence, and
    arbitrary caller-provided session IDs are removed from it and
    `run_backtest`. This deliberate API break prevents provisional or
    non-content-bound run/fill identities; CLI command names remain compatible.

## Consequences

- Identical content/configuration is reproducible after a file rename; one-byte
  content changes alter the session ID.
- A stopped partial replay retains full validated-input provenance while its
  result interval reports only processed events.
- Consumers must consciously migrate from schema 1.1 to 1.2.
- Low-level Python callers must pass `validated_bars` when constructing
  `SimulationEngine` and consume the derived content-bound `session_id`.
- Benchmark results are more realistic but still omit dividends, corporate
  actions, spreads, market impact, and exchange calendars.
- Atomic replacement depends on destination-filesystem semantics; Windows tests
  cover close-before-replace behavior.
- Replay remains synchronous, local, single-symbol, and non-restartable.

## Rejected alternatives

- Hash the input path or filename: machine-dependent and not content-bound.
- Re-read a file during report generation: creates a provenance race.
- Create a second paper accounting engine: risks divergence from backtests.
- Final-sell buy-and-hold: fabricates an execution and second cost not present in
  the strategy result.
- Log serialized domain objects wholesale: increases accidental path/secret
  disclosure risk.
- Add a database, queue, cloud logger, or daemon: unnecessary for local historical
  replay and outside Sprint 2.

## Rollback

The feature can be rolled back by reverting ADR 0006 and the Sprint 2 domain,
engine, report, paper, CLI, provenance, artifact, test, and documentation changes
together. Do not retain a `1.2.0` schema label with 1.1 behavior. Rollback must
not touch `lean/`, relax risk checks, introduce same-bar fills, or enable live
trading.
