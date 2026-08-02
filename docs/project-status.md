# Project status

Recorded: 2026-08-02

## Current decision

```text
LEAN_FIXED_WALK_FORWARD_V1_COMPLETE
LIVE_STOCK_DEPLOYMENT_DEFERRED
REASON: CAPITAL_AND_INFRASTRUCTURE_ECONOMICS
ACTIVE_DEVELOPMENT_TARGET: SEPARATE_FREQTRADE_CRYPTO_SPOT_PROJECT
```

The five fixed, predeclared 2021-2025 SPY folds are complete. Their official
QuantConnect Download Results were imported and validated offline, and the
reviewed sanitized aggregate is preserved at
`contracts/walk-forward/v1/2026-07-29-result-aggregate.json` with exact-byte
SHA-256
`f8ad1fa47b03862835d032edadcb1ce684ec9d695dcc72b03bd27fdd15ba933e`.
Pull request #10 is the completed evidence baseline.

`walk_forward_result_contract_complete` means the declared evidence contract
completed. It does not establish profitability, statistical robustness,
paper-readiness, or live-readiness.

## Frozen stock-research boundary

Preserve the existing LEAN/QuantConnect code, schemas, tests, public sanitized
records, historical failed evidence, and successful corrected evidence. In
particular:

- do not delete, rewrite, regenerate, or replace completed evidence;
- do not rerun any of the five fixed walk-forward folds;
- do not reinterpret SPY evidence as evidence for crypto or another strategy;
- do not add broker credentials or enable live stock execution;
- keep all five private Download Results and normalized working observations
  outside Git; and
- treat future maintenance as a separately reviewed change that preserves the
  frozen evidence hashes.

The repository remains available as a reproducible stock-research reference
and governance source. This Phase 0 change is a status, preservation, privacy,
and transition checkpoint; it does not remove or mechanically convert the
completed implementation.

## Why live stock deployment is deferred

The intended experimental capital is too small for the current strategy and
infrastructure economics. Paying QuantConnect infrastructure costs comparable
to the available capital would dominate any plausible experimental return,
while market risk remains inherent and returns are not guaranteed. Fractional
stock execution or a different brokerage would also change validated execution
assumptions and require new research rather than a direct deployment.

This is an economic and risk decision, not a claim that the code failed. A
future stock-deployment proposal requires explicit human approval, a reviewed
capital and recurring-cost budget, a broker/execution design, fresh validation
for every changed assumption, paper/shadow evidence, and a tested rollback and
kill switch.

## Active development pivot

Active development moves to a separate sibling Freqtrade crypto-spot project.
Phase 0 does not add Freqtrade, exchange connectivity, credentials, or crypto
strategy code to this repository.

The separate project may reuse governance patterns such as fixed test periods,
deterministic configuration snapshots, immutable sanitized evidence, CI,
privacy checks, fail-closed risk controls, dry-run gates, audit logs, and human
live authorization. It must not reuse unchanged:

- SPY symbols, data, results, or benchmark conclusions;
- US-equity calendars or market-on-open assumptions;
- whole-share sizing or the stock fee model; or
- the existing strategy's allocation and five-fold conclusions.

Crypto spot requires its own venue availability review, pair and quote-currency
selection, fractional precision, minimum-order constraints, 24/7 data-gap
policy, fee/slippage model, walk-forward protocol, dry-run evidence, and live
canary authorization. Spot-only, long-or-cash, no leverage, one exchange, and
dry-run-first remain the intended starting boundary.

## Private backup and review

The five official Download Results must have a restricted, hash-manifested
backup outside the repository. The authoritative procedure, permission checks,
optional encrypted-archive steps, and restore verification are in
`docs/data-policy.md`. The backup itself is operator-owned private evidence and
must never be added to Git or attached to a public pull request.
