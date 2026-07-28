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

Raw cloud output remains under ignored `logs/` or LEAN `backtests/`. Normalized
working observations and aggregates remain under ignored `reports/`. Only a
separately reviewed sanitized record may become tracked evidence.
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

The repository operator defaults to a read-only plan. It can print exactly five
future commands but contains no cloud-run phase and never invokes LEAN, a
network, optimization, data download, Object Store, paper trading, live trading,
or a broker. No cloud backtest has been executed for this protocol. Separate
human authorization is required before any printed command may be run.
