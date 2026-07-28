# WalkForwardMovingAverageV1

This dedicated LEAN project implements the fixed-parameter rolling
walk-forward v1 contract. It is research-only and backtest-only. It is not an
optimization, profitability claim, robustness claim, or paper/live trading
candidate.

## Closed protocol

The operator must provide exactly one `fold-id`. A bare project run retains the
fail-closed `__required__` sentinel and fails initialization.

| Fold | Public evaluation interval |
|---|---|
| `spy-2021` | 2021-01-01 through 2021-12-31 |
| `spy-2022` | 2022-01-01 through 2022-12-31 |
| `spy-2023` | 2023-01-01 through 2023-12-31 |
| `spy-2024` | 2024-01-01 through 2024-12-31 |
| `spy-2025` | 2025-01-01 through 2025-12-31 |

The internal boundary mapping passes each public date unchanged to LEAN's
`set_start_date` and `set_end_date`. The protocol treats both as inclusive
calendar-date boundaries; only actual SPY exchange sessions inside that closed
interval produce evaluation bars. Warmup may read completed bars preceding the
public start date but cannot change either public endpoint.

Arbitrary start and end dates are not accepted. All folds use adjusted daily
SPY data, USD 100,000, a 20/50 completed-close moving-average signal, 50 daily
warmup bars, a 10% long-or-flat target, a cash account, and leverage fixed at
one. Position and gross-exposure caps are 10% and 30%. Fees are one basis point
with a USD 1 minimum per non-zero order, and constant slippage is two basis
points adverse to the order side. Daily-loss and peak-drawdown halt thresholds
are 2% and 5% and are inclusive.

Warmup closes may seed the trailing windows, but warmup cannot submit an order
or contribute a trade or metric. The first eligible evaluation signal is
created only after an evaluation bar completes. Every order is market-on-open
for the next eligible session. A final signal with no next market open inside
the public evaluation interval expires without an intent or fabricated fill.

## Risk-halt behavior

The mission explicitly forbids automatic liquidation. A risk breach therefore
latches for the rest of the fold, cancels pending orders, blocks all later
orders, and leaves any existing long position open and marked through the end
of the evaluation interval. This deliberately differs from the existing
`MovingAverageBaseline`, whose conservative simulated halt path requests a
next-open liquidation. No shared baseline or parity source is modified.

## Observation and evidence boundary

At a normal completed end, the project emits exactly one compact canonical JSON
line prefixed with `TRADING_BOT_LAB_LEAN_WALK_FORWARD_V1:`. Completion requires
that the final eligible exchange close in the public interval was processed;
an early stop or trailing data outage emits no completed observation. The line
contains only the closed observation-schema fields and no account,
organization, cloud project, backtest, URL, path, token, credential, billing,
or raw order identity.

The benchmark is the price-only return from the first adjusted SPY close to the
last adjusted SPY close inside the public evaluation interval. Observation
metrics begin only with the first non-warmup evaluation bar.

`optimization-mode` must remain exactly `false`. Fixed strategy, risk, cost,
data, and accounting settings are constants. Initialization enumerates the
complete LEAN parameter dictionary and rejects every name outside the exact
`fold-id`/`optimization-mode` allowlist before any setup side effect. LEAN
exposes no optimization-job signal validated by this repository, so the
separate print-only operator and CI command guard are also required to
prohibit optimization commands.

The source identity is SHA-256 over exact UTF-8/LF `main.py` bytes after zeroing
the single `PROJECT_SOURCE_SHA256` value. The public configuration identity is
SHA-256 over exact UTF-8/LF `config.json` bytes. Extraction must recompute both.

No walk-forward cloud backtest has been executed. There are no fold results and
no strategy-quality conclusion. The only permitted next gate is separate human
authorization of the exact five bounded cloud backtests.
