# Data policy

## Do not commit data by default

Market data, logs, model artifacts, and reports can become large, sensitive, licensed, or strategy-revealing. This repo ignores data directories by default.

Tracked exceptions are limited to clearly labelled, tiny synthetic fixtures.
The sole parity CSV source is
`tests/fixtures/parity/v1/synthetic_weekdays.csv`, exact-byte SHA-256
`a68bcf7fc30d2593b32e5a98852c4f8e0190ed99865640485b344515d9f1f78a`.
Its fixture and `1.0.0` scenario files require LF bytes. Do not commit another
copy under a LEAN project. CRLF conversion, mutation, truncation, extra rows,
NaN, infinity, unsorted or duplicate timestamps, and invalid OHLC relationships
must fail rather than be normalized or repaired. These fixtures test semantics,
not market-data quality or a strategy edge.

User-provided real CSVs belong in `data/local/`. That folder exists via
`.gitkeep`, but its contents are ignored and must not be committed.

Generated report files belong under `reports/`, which is ignored except for
`reports/.gitkeep`. Local-oracle traces and extracted LEAN observations remain
ignored there until their contents are explicitly sanitized and reviewed.

Raw LEAN and structured event logs belong under ignored `logs/`. They must not
be committed or treated as a normalized observation. Reserved replay checkpoints
belong under ignored `checkpoints/`; checkpoint/restart behavior is not yet
implemented. CLI-selected artifacts inside the repository are rejected unless
they use the documented ignored roots.

## LEAN workspace and cloud data

All `lean-workspace/data/`, `storage/`/Object Store content, backtest results,
optimizations, live output, logs, caches, and notebooks are generated/local and
ignored. Do not copy global `.lean/` state into the repository.

The offline parity preparer validates the authoritative fixture and atomically
copies those exact LF bytes only to
`lean-workspace/data/custom/parity/v1/synthetic_weekdays.csv`. It rejects
symlinks, unsafe destinations, and differing existing output. It never converts,
downloads, uploads, or repairs data.

The pinned local operator writes runtime state and raw CLI logs only under
ignored `logs/parity/`, normalized observations and audits only under ignored
`reports/parity/`, and raw engine results only under the dedicated project's
ignored `backtests/`. Its temporary project copy and credential-free HOME are
removed only when the current-run sentinel is present. The operator never
stages, repairs, sorts, or transforms fixture bytes, and the engine container
has no network path from which it could obtain other data.

`ParityFixtureV1` defaults to that local file. A later cloud run may explicitly
select Object Store only when an operator has manually placed the same exact
bytes at the fixed key
`trading-bot-lab/parity/v1/synthetic_weekdays.csv`. The repository provides no
Object Store write, automatic upload, automatic download, remote URL, or network
fallback. The key may not contain account, organization, project, or machine
identity.

Cloud backtests may read datasets available to the paid organization. They do
not authorize committing or locally downloading that data. `lean data
download`, `--download-data`, a local QuantConnect historical data provider,
and automatic data purchasing are prohibited. Local data downloads may incur
separate QCC costs; project policy disables them regardless of available credit.
No such data operation was performed for the parity implementation.

See `qcc-guardrails.md`.

## Local data layout

```text
data/
  local/        # ignored user-provided CSVs; only .gitkeep is committed
  sample/       # tiny committed synthetic/demo CSV only
  raw/          # vendor/exchange downloads, immutable when possible
  processed/    # normalized bars/features for local experiments
lean/data/      # preserved legacy LEAN-compatible local data
lean-workspace/data/custom/parity/v1/  # ignored exact parity staging
```

## Rules

- Preserve raw data when possible.
- Normalize timestamps to UTC.
- Document all data sources.
- Track whether data is adjusted or unadjusted.
- Never mix training and holdout periods by accident.
- Avoid survivorship bias in equity universes.
- Respect vendor licenses.
- Keep committed data synthetic/demo only.
- Validate required CSV columns before running local backtests.
- Reject blank, whitespace-padded, or duplicate headers and extra row cells.
- Keep generated reports out of Git.
- Keep logs, manifests, replay artifacts, and checkpoints out of Git.
- Keep LEAN data, Object Store content, raw results, and normalized parity
  observations ignored until a separately reviewed sanitization step.
- Normalize all timestamps to UTC at ingestion; never guess the timezone of a
  naive timestamp.
- Do not silently sort, deduplicate, forward-fill, interpolate, or download data.
- Reject inconsistent OHLC values, NaN/infinity, and non-positive prices.
- Configure missing-volume and large-gap policy explicitly for each dataset.
- Record resolution/timeframe and whether prices are adjusted for corporate actions.

## Active CSV semantics

The loader accepts exactly one symbol and exactly one `timestamp` or `date`
column. `date` means midnight UTC; timestamp values require an explicit offset.
OHLC fields are positive and finite, volume is non-negative when present, high
must cover open/close, and low must cover open/close. Input order is preserved
and validated rather than repaired. Declared OHLC columns cannot contain blank
values, and `high`/`low` must be declared together. A simulation requires a
valid `open` on every bar and never substitutes close.

CSV ingestion reads and hashes exact bytes once, then parses that same content.
Metadata stores lowercase exact-byte and normalized-bar SHA-256 values plus the
safe filename only. Absolute input paths are never durable report provenance.
Symbols use a report-safe uppercase
domain of letters, digits, dot, underscore, colon, and hyphen.

The parity preparation and LEAN custom-data boundaries apply the same principle:
they verify the fixture's exact bytes and LF policy before parsing and bind both
the fixture and normalized bars in the observation. Local-file versus Object
Store transport cannot change financial semantics. A missing source or wrong
runtime hash fails before trading logic begins.

The committed synthetic file is only a deterministic fixture. It provides no
survivorship-bias, corporate-action, delisting, exchange-calendar, or market
microstructure coverage. Any equity-universe study must later use point-in-time
membership and delisted assets. Any crypto study must record venue, quote asset,
and continuous-market gap expectations.

## Future data catalog fields

Each dataset should eventually have:

- source,
- license/terms,
- symbols,
- venue,
- resolution,
- date range,
- timezone,
- adjusted/unadjusted status,
- known gaps,
- ingestion script,
- checksum or version,
- and intended usage.

## Fixed walk-forward v1 data boundary

The completed walk-forward cloud backtests read only QuantConnect's
cloud-available adjusted daily SPY data. Repository tooling did not download,
upload, copy, transform, buy, or cache those market-data bytes and did not use
Object Store. All five official 2021-2025 result files remain outside the
repository and must not be rerun. This evidence phase executes no cloud
backtest or data operation.

The public folds remain the inclusive calendar intervals `spy-2021` through
`spy-2025` defined in `contracts/walk-forward/v1/protocol.json`. Each project run
passes those dates unchanged to LEAN and sets `daily_precise_end_time = True` so
daily evaluation bars arrive at market close. The 50-bar warmup may read
completed adjusted bars before a fold start only to seed trailing history. It
cannot submit an order or contribute a trade or metric, and no later fold may
influence fixed parameters.

Raw CLI/engine logs and full Download Results JSON belong only under ignored
local artifact roots or other private untracked storage. Normalized per-fold
observations and the generated working aggregate remain ignored. The separately
reviewed sanitized aggregate is tracked at
`contracts/walk-forward/v1/2026-07-29-result-aggregate.json`. It contains only
closed-schema fields and content hashes and excludes raw logs/results, private
paths, URLs, hostnames, emails, account/organization/project/cloud/backtest
identity, billing data, credentials, tokens, licenses, and raw order IDs.

Canonical extraction uses bounded physical-line reading and requires one
canonical payload. Result-JSON extraction uses a bounded regular UTF-8 JSON
file, rejects duplicate keys/non-finite values, validates completed state,
fixed parameters/dates/configuration, SPY orders/events, final position, and an
unambiguous Benchmark chart, then strips all private metadata. Each aggregate
requires exactly five observations of one explicit source type. Sanitized
evidence remains a content-bound research record, not proof of market-data
quality or strategy performance.
