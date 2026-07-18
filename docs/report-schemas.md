# Report schemas

All generated reports are local artifacts and belong under ignored `reports/`
paths. Schema version `1.0.0` is the initial stable contract.

## JSON backtest summary

Top-level fields include:

- schema and engine versions,
- UTC generation timestamp and session ID,
- mode and strategy name,
- input source, symbol, row count, UTC range, timeframe, and timezone,
- typed data warnings,
- resolved backtest assumptions and risk configuration,
- result summary and cash/buy-and-hold benchmarks,
- halt state and rejection counts by typed reason,
- start/end timestamps,
- the hypothetical-results and no-advice disclaimer.

Order-level risk decisions are included for auditability. Reports must not
contain API keys, credentials, account identifiers, or brokerage configuration.

## Equity CSV

Columns, in order:

```text
timestamp,close,cash,quantity,average_cost,position_market_value,equity,
exposure,realized_pnl,unrealized_pnl,cumulative_fees,
cumulative_slippage,drawdown,halt_state
```

Timestamps are ISO-8601 UTC. Exposure and drawdown are fractions, not percentages.

## Trades CSV

Each row is an approved fill with intent ID, timestamp, symbol, side, quantity,
reference/execution price, executed notional, fee, estimated slippage, average
cost after the fill, realized PnL delta, and target weight.

## Rejected intents CSV

Each row contains timestamp, intent ID, symbol, side, quantity, estimated
notional, and pipe-delimited typed rejection reasons. Portfolio-only halt checks
are represented in JSON risk decisions rather than as rejected order intents.

## Paper-session JSON

Paper replay uses schema `1.0.0` and records mode, session ID, final lifecycle
status, processed/total bars, replay speed, strategy and engine versions, every
state transition, result summary, halt state, and a local-simulation disclaimer.

Schema changes require tests, documentation, and either backward-compatible
optional fields or a schema-version increment.
