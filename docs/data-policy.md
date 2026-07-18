# Data policy

## Do not commit data by default

Market data, logs, model artifacts, and reports can become large, sensitive, licensed, or strategy-revealing. This repo ignores data directories by default.

The only current exception is `data/sample/synthetic_spy_daily.csv`, which is
synthetic/demo data for smoke tests and not real market data.

User-provided real CSVs belong in `data/local/`. That folder exists via
`.gitkeep`, but its contents are ignored and must not be committed.

Generated report files belong under `reports/`, which is ignored except for
`reports/.gitkeep`.

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
- Keep generated reports out of Git.
- Normalize all timestamps to UTC at ingestion; never guess the timezone of a
  naïve timestamp.
- Do not silently sort, deduplicate, forward-fill, interpolate, or download data.
- Reject inconsistent OHLC values, NaN/infinity, and non-positive prices.
- Configure missing-volume and large-gap policy explicitly for each dataset.
- Record resolution/timeframe and whether prices are adjusted for corporate actions.

## Active CSV semantics

The loader accepts exactly one symbol and exactly one `timestamp` or `date`
column. `date` means midnight UTC; timestamp values require an explicit offset.
OHLC fields are positive and finite, volume is non-negative when present, high
must cover open/close, and low must cover open/close. Input order is preserved
and validated rather than repaired.

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
