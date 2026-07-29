# Fixed-parameter rolling walk-forward contract v1

This directory defines the closed `1.0.0` protocol for five predeclared SPY
evaluation folds covering calendar years 2021 through 2025. Every fold uses the
same 20/50 moving-average strategy, 50-bar pre-evaluation warmup, cash account,
one-times leverage, long-only target, risk caps, costs, adjusted daily data, and
next-market-open execution. Fold results never select or change a parameter.

`protocol.json` is the authoritative manifest. Its typed loader also hard-checks
the v1 constants, exact ordered fold set, schemas, project identity, and public
configuration so editing the manifest cannot silently redefine this version.
Changing a fold, parameter, safeguard, cost, result field, or identity rule
requires a future protocol version.

## Two distinct evidence formats

The canonical algorithm-log path remains authoritative for algorithm-only state.
`observation.schema.json` and `aggregate-record.schema.json` cover that format;
`extract`, `aggregate`, and `evidence` preserve their existing behavior.

QuantConnect Download Results JSON cannot supply every algorithm-only field, so
it has a separate closed contract in `result-observation.schema.json` and
`result-aggregate-record.schema.json`. `extract-result`, `aggregate-result`, and
`evidence-result` validate that format offline. A result observation records
`source_format=quantconnect_result_json`; an aggregate requires that same source
format for all five folds and cannot mix it with `canonical_algorithm_log`.

Result import validates completed state, exact names and parameters, fixed UTC
calendar dates, cash/USD configuration, zero out-of-sample days, official performance fields,
SPY-only orders, final-position consistency, and an unambiguous Benchmark chart
spanning the fold. A populated bounded UTC `outOfSampleMaxEndDate` is metadata,
not evidence of an out-of-sample period when `outOfSampleDays` is zero. Benchmark
points may use either official `[unix_seconds, value]` arrays or exact `x`/`y`
objects and normalize identically. For non-fee metrics it uses precise
`totalPerformance` fields in preference to display strings. Display ratios may
differ by at most `0.0005` and currency values by at most USD `0.01`, solely
to accommodate QuantConnect's published rounding. Total return is recomputed
from start/end equity.

`totalPerformance.tradeStatistics.totalFees` is a closed-trade analysis fee
sum, not whole-backtest transaction-fee evidence. When `orderEvents` exists,
the importer sums actual event fees and makes that sum authoritative; rounded
`statistics.Total Fees` and `runtimeStatistics.Fees` displays must each
reconcile within USD `0.01`. When the official download omits `orderEvents`,
the absolute Overview/runtime display values must agree within USD `0.01`;
the Overview value becomes the cent-rounded normalized `total_fees_usd`.
The trade-analysis fee remains required, bounded, and nonnegative but is
discarded from normalized public evidence.

Normalized records bind `fee_validation_source` as `order_events` or
`overview_runtime_rounded`, `fee_precision` as
`order_event_amount_precision` or `rounded_to_cent`, and
`order_event_fee_evidence_available` as a boolean. The independent
`order_validation_source` records whether fills came from `orderEvents` or
completed orders. Without events, every order must be filled, both official
order counts must agree, and the importer derives a chronologically non-short
position from `lastFillTime`. Missing event detail is explicitly unavailable
and never fabricated.

## Identities and canonical JSON

- Schema and public configuration hashes cover their exact checked-out UTF-8
  LF bytes, including the final newline. Every schema is itself sorted
  deterministic pretty JSON, and the loader rejects noncanonical schema bytes.
- The project source hash covers exact UTF-8 LF `main.py` bytes after replacing
  the one 64-hex value assigned to `PROJECT_SOURCE_SHA256` with 64 ASCII zeroes.
  The loader requires exactly one such slot and separately requires its literal
  value to equal the manifest hash. This documented normalization avoids an
  otherwise impossible self-referential digest.
- The protocol-manifest hash covers its exact deterministic pretty JSON bytes.
- Machine-readable LEAN observations use sorted compact JSON, UTF-8, finite
  canonical decimal strings, no duplicate keys, and the unique prefix
  `TRADING_BOT_LAB_LEAN_WALK_FORWARD_V1:`.
- Normalized observations and aggregate records use sorted pretty JSON with LF
  and a final newline. Writes use a same-directory temporary file and atomic
  replacement after symlink/reparse checks.

Raw cloud output remains under ignored `logs/`, LEAN `backtests/`, or other
private untracked storage. Full Download Results JSON is never tracked.
Normalized working observations and aggregates remain under ignored `reports/`.
The separately reviewed sanitized exact-five result aggregate is tracked at
`contracts/walk-forward/v1/2026-07-29-result-aggregate.json`; no raw or working
artifact is tracked.
Operator writes are confined to `reports/walk-forward/v1`, and aggregate output
may not alias a fold input. Inputs must be regular files. Total raw-log,
normalized-observation, and aggregate-record reads are capped at 8 MiB, 64 KiB,
and 512 KiB respectively, in addition to the 16,384-byte canonical observation
payload limit.

## Interpretation and execution gate

Aggregate summaries are descriptive. Contract status means only that the exact
five-fold evidence contract completed or failed; returns and benchmark results
never create a promotion threshold. Runtime-version drift is reported without
being reclassified as strategy quality.

The repository operator defaults to a read-only plan. It can still print the
exact five closed commands using the private environment placeholder
`$LEAN_WALK_FORWARD_PROJECT_ID`, no `--push`, and the exact `fold-id` plus
`optimization-mode=false` parameters. It contains no cloud-run phase and never
invokes LEAN, a network, optimization, data download, Object Store, paper
trading, live trading, or a broker.

The five authorized cloud runs are complete and must not be rerun.

All five private `wf-v1-spy-2021` through `wf-v1-spy-2025` Download Results
JSON files remain outside this repository and must not be rerun. Each raw file
and every normalized per-fold working observation stays private and untracked;
the offline import pattern is:

```bash
python scripts/run_walk_forward_v1.py extract-result \
  --input-result <quantconnect-result.json> \
  --output reports/walk-forward/v1/<fold-id>.json
python scripts/run_walk_forward_v1.py validate \
  --result-observation reports/walk-forward/v1/<fold-id>.json
```

Download Results JSON supports official completion/configuration, performance,
order/fill, and benchmark claims. It cannot prove engine version, algorithm risk
halt state, estimated slippage, or rejected-order count; those remain explicitly
unavailable rather than fabricated. This evidence phase authorizes no rerun or
cloud, project, data, Object Store, optimization, paper, or live action.
