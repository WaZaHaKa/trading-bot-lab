# Data policy

## Do not commit data by default

Market data, logs, model artifacts, and reports can become large, sensitive, licensed, or strategy-revealing. This repo ignores data directories by default.

Tracked exceptions are limited to clearly labeled, tiny synthetic fixtures such
as `data/sample/synthetic_spy_daily.csv` and the versioned parity fixture under
`tests/fixtures/parity/`. They are for semantic smoke tests, not market research.

User-provided real CSVs belong in `data/local/`. That folder exists via
`.gitkeep`, but its contents are ignored and must not be committed.

Generated report files belong under `reports/`, which is ignored except for
`reports/.gitkeep`.

Structured event logs belong under ignored `logs/`. Reserved replay checkpoints
belong under ignored `checkpoints/`; checkpoint/restart behavior is not yet
implemented. CLI-selected artifacts inside the repository are rejected unless
they use the documented ignored roots.

## LEAN workspace and cloud data

All `lean-workspace/data/`, `storage/`/Object Store content, backtest results,
optimizations, live output, logs, caches, and notebooks are generated/local and
ignored. Do not copy global `.lean/` state into the repository.

Cloud backtests may read datasets available to the paid organization. They do
not authorize committing or locally downloading that data. `lean data
download`, `--download-data`, a local QuantConnect historical data provider,
and automatic data purchasing are prohibited. Local data downloads may incur
separate QCC costs; project policy disables them regardless of available credit.

See `qcc-guardrails.md`.

## Local data layout

```text
data/
  local/        # ignored user-provided CSVs; only .gitkeep is committed
  sample/       # tiny committed synthetic/demo CSV only
  raw/          # vendor/exchange downloads, immutable when possible
  processed/    # normalized bars/features for local experiments
lean/data/      # LEAN-compatible local data
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
