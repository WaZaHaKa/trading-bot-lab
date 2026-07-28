# ParityFixtureV1

This public project is the deterministic LEAN side of the identical-data parity
contract. It is backtest-only, consumes only the committed synthetic `PARITY`
fixture, and emits one compact normalized v1 observation. It has not yet been
executed in LEAN, so its presence does not establish cross-engine parity.

## Fixed scenario

- Scenario: `weekday_ma_next_open_v1`, manifest version `1.0.0`.
- Fixture: `synthetic_weekdays.csv` with SHA-256
  `a68bcf7fc30d2593b32e5a98852c4f8e0190ed99865640485b344515d9f1f78a`.
- One synthetic symbol, daily bars, USD 100,000 initial cash, 2/3 trailing
  moving averages, and a 10% long-or-flat target.
- Cash account, leverage fixed at one, no shorting, no external data, no live
  mode, no optimizer, and no broker or exchange integration.

Financial and risk settings are constants bound to the versioned scenario, not
operator-tunable parameters. Only the transport can be selected.

## Transport

`data-transport=local-file` is the offline default. It reads exactly:

`Globals.data_folder/custom/parity/v1/synthetic_weekdays.csv`

The optional Object Store mode requires both `data-transport=object-store` and
the exact public parameter
`object-store-key=trading-bot-lab/parity/v1/synthetic_weekdays.csv`. It reads
only that fixed key:

`trading-bot-lab/parity/v1/synthetic_weekdays.csv`

Local-file mode requires `object-store-key` to remain empty. The operator must
place the exact fixture at the fixed key in a separately
authorized step. The algorithm never uploads, writes, downloads, discovers, or
falls back to another source. Remote URL, REST, and streaming transports are
not implemented. Both modes read and validate all bytes, the exact SHA-256,
LF-only line endings, rows, dates, and OHLCV relationships before the data
subscription or strategy is created.

## Timing and accounting boundary

A signal is calculated only after bar N's close. The pending target is examined
only when row N+1 arrives. Because a custom-data security does not provide the
equity exchange-calendar semantics needed for a native market-on-open order,
the project explicitly marks the security to row N+1's open, submits one
synchronous LEAN market order through a custom adverse-slippage fill model,
observes the fill and fee callbacks, and then marks the security to that row's
close. LEAN remains responsible for the order event, holdings, cash, fees, and
portfolio accounting. The final row's signal expires without an intent or
fabricated fill.

This explicit next-row-open adapter is part of the first-run validation scope;
it must not be represented as proven parity until an actual LEAN trace passes
the offline comparator.

At algorithm end, the project emits exactly one line beginning with
`TRADING_BOT_LAB_LEAN_PARITY_V1:` followed by compact canonical JSON. The line
contains no transport source, Object Store key, path, URL, account metadata, or
cloud identifier.
