# Report schemas

Generated reports are local artifacts. Paths inside this repository must be
under ignored `reports/`; event logs must be under ignored `logs/`. Absolute
paths outside the repository are also accepted by the CLI because Git cannot
track them.

Sprint 2 deliberately evolves both JSON contracts from `1.1.0` to `1.2.0`.
Version 1.2 adds content-bound, path-safe provenance; costed benchmark details;
auditable trade state; atomic writes; risk-event CSV; and the paper-session
reproducibility manifest. Consumers must not treat 1.2 as byte-compatible with
1.1.

The Python package/engine version advances from `0.2.0` to `0.3.0`. Low-level
callers that constructed `SimulationEngine` must now supply the full
`validated_bars` sequence, and callers may no longer override `session_id` on
the engine or `run_backtest`. This makes run and fill identities content-bound;
the existing CLI backtest commands remain compatible.

## JSON backtest summary (`1.2.0`)

Top-level fields include:

- schema, engine, and package versions;
- UTC generation timestamp, session ID, and `mode=backtest`;
- strategy name and stable primitive parameters;
- safe input filename, exact-byte SHA-256, normalized-bar SHA-256, symbol, row
  count, UTC interval, timeframe, and timezone;
- typed data-validation and simulation/observability warnings;
- resolved assumptions, effective risk configuration, and
  `execution_timing=next_bar_open`;
- result summary, cash baseline, costed buy-and-hold baseline, and directed
  comparison fields;
- complete risk decisions, rejection counts, halt state/reasons, and start/end
  event timestamps;
- a hypothetical-results and no-advice disclaimer.

Absolute input paths, credentials, account identifiers, and broker
configuration are excluded. Given the same result and an explicitly supplied
generation timestamp, JSON output is byte-stable. JSON rejects NaN and infinity.

## Benchmark records

Both cash and buy-and-hold report starting cash, ending equity, total return,
maximum drawdown, fees, estimated slippage, average exposure, and maximum
exposure.

Cash never trades, so its costs, exposure, return, and drawdown are zero.
Buy-and-hold buys at the open of bar index `warmup_bars`, if that bar exists,
using adverse buy slippage, the configured fee/minimum fee, and the configured
quantity precision. It selects the largest quantity that leaves nonnegative
residual cash, marks each visible close, and does not fabricate a final sale.
The report discloses entry time/prices/quantity, fractional-quantity support,
methodology, and whether ending equity contains an open position.

These are descriptive controls. Outperformance is not evidence of a profitable
or investable strategy.

## Equity CSV

Columns, in stable order:

```text
timestamp,close,cash,quantity,average_cost,position_market_value,equity,
start_of_day_equity,daily_pnl,peak_equity,exposure,realized_pnl,unrealized_pnl,
cumulative_fees,cumulative_slippage,drawdown,halt_state
```

## Trades CSV

Columns, in stable order:

```text
intent_id,fill_id,intent_timestamp,fill_timestamp,execution_phase,symbol,side,
quantity,reference_price,fill_price,notional,fee,slippage_cost,
average_cost_after,realized_pnl_delta,resulting_cash,resulting_quantity,target_weight
```

Each row is an approved simulated fill. Intent and fill timestamps are distinct
so next-bar timing can be audited directly.

## Rejected-intents and risk-events CSV

Rejected intents contain timestamp, intent ID, symbol, side, quantity,
estimated notional, and pipe-delimited typed reasons. The optional risk-events
CSV includes every order and portfolio risk decision with stable JSON metrics,
including portfolio-only halt checks.

## Paper-session manifest (`1.2.0`)

The historical replay manifest records schema/session/mode, exact input hash,
safe filename, strategy parameters, effective risk configuration, fee/slippage
and execution assumptions, Python/package/engine versions, declared random seed,
processed event interval, transitions, final state, halt/failure reason,
benchmarks, rejection summary, and artifact basenames. It supports terminal
sessions with zero processed bars; their financial result fields are `null`.

Only basenames appear in `artifact_filenames`. The manifest is written
atomically and can be made byte-stable by supplying its generation timestamp.

## Atomicity and compatibility

Every JSON and CSV exporter writes a sibling temporary file, flushes it, closes
it, and uses `os.replace`. Failure raises to the caller, removes the temporary
file where possible, and does not report success. A pre-existing destination is
preserved if replacement fails.

Future schema changes require tests and documentation plus either a compatible
optional-field addition or another explicit version increment.

## Fixed walk-forward v1 evidence (`1.0.0`)

`contracts/walk-forward/v1/` contains a canonical protocol manifest plus closed
schemas for the protocol, one fold observation, and the exact-five aggregate.
The manifest binds the ordered fold IDs/dates, project/source and public-config
hashes, schema hashes, fixed strategy/risk/cost/data/execution settings,
benchmark, engine provenance, permitted result fields, prohibited identity
fields, and a 16,384-byte observation limit.

LEAN emits exactly one compact, sorted JSON object after
`TRADING_BOT_LAB_LEAN_WALK_FORWARD_V1:`. The extractor accepts only supported
line wrapping and writes normalized sorted UTF-8 JSON atomically with LF and a
final newline. It rejects zero/multiple observations, duplicate keys,
NaN/infinity, non-regular files, symlink/reparse paths, source/configuration
drift, unsafe identity text, and any field outside the closed schema. Total raw
log, normalized-observation, and aggregate-record reads are capped at 8 MiB,
64 KiB, and 512 KiB. Raw logs remain ignored, and writes remain confined to the
ignored walk-forward report root.

Each observation repeats the fixed strategy, risk, costs, data, and execution
contract and records:

- fold ID and inclusive evaluation dates;
- dotted LEAN runtime version and content identities;
- starting/ending equity, return, adjusted-close benchmark values/return,
  excess return, drawdown, fees, estimated slippage, and order/fill/rejection
  counts; and
- completion, warmup, proof that the final eligible exchange close was seen,
  first/last evaluation timestamps, halt reasons, and final cash-or-long
  position state.

The price-only benchmark is the first adjusted SPY evaluation close through the
last adjusted evaluation close and excludes trading costs. Warmup trades are
impossible and warmup data contributes no observation metric.

Aggregation requires all five unique predeclared observations and retains every
fold in protocol order before the summary. It derives completed, positive, and
benchmark-beating fold counts; median strategy/benchmark/excess returns; worst
return and drawdown; total orders/fees; and halt count. It also reports the
unique runtime versions and whether they are consistent.

`contract_status` describes only whether all five folds completed their closed
contract (`walk_forward_contract_complete` or
`walk_forward_contract_failed`; the schema reserves `incomplete`). Runtime
drift is reported rather than hidden and does not create a performance gate.
There is no profitable/robust/paper/live-ready status or arbitrary promotion
threshold. At this implementation stage no fold observation or aggregate result
exists.
