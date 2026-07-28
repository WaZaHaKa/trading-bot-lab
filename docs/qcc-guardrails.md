# QuantConnect data-cost and QCC guardrails

Cloud research access does not authorize open-ended spend.

## Prohibited

- `lean data download`
- `lean backtest --download-data`
- a local QuantConnect historical data provider
- automatic dataset purchases
- optimization or parameter sweeps
- unscoped or forced cloud pushes
- cloud commands in CI, Make defaults, scheduled jobs, or unattended scripts
- Object Store uploads containing secrets, licensed market data, or account data

Local data acquisition can consume QuantConnect Credit independently of a
subscription. No local data purchase is approved by this sprint. The
repository's projects use cloud-available SPY data for two named backtests and
do not request alternative datasets.

The parity-data preparer is an offline format conversion of the committed
synthetic CSV. It does not call LEAN, Docker, QuantConnect, or a network and has
no QCC cost. Its ignored output is not licensed market data.

## Required manual gate

Before each cloud backtest:

1. Run repository preflight.
2. Confirm the project path and diff.
3. Confirm the command has no live, data, optimization, or force option.
4. Confirm only one named backtest is being started.
5. Record the result ID/link and any platform warning.
6. Stop after the two sprint backtests.

Cloud and local backtests are hypothetical. They place no real orders, make no
profitability claim, and are not financial advice.

## Future fixed walk-forward gate

The two-project gate above is retained as completed historical activation
procedure. It does not authorize the new walk-forward plan.

`python scripts/run_walk_forward_v1.py print-cloud-commands` is print-only and
network-free. It outputs exactly five future backtests named
`wf-v1-spy-2021` through `wf-v1-spy-2025`, all scoped to
`Strategies/WalkForwardMovingAverageV1` with one closed `fold-id`. The helper
has no executable cloud-run phase and is never called by CI or `make check` to
start work.

A later human authorization must cover exactly those five printed commands.
Before any command, re-run preflight, review the source/configuration hashes and
private project mapping, verify the one fold/name pair, and confirm the command
contains no force, verbose, open, optimization, live, data-download, or Object
Store option. Stop if the printer output differs or the project is not safely
linked.

That future authorization would permit only the named cloud backtests. It would
not authorize `lean data`, `--download-data`, dataset purchases, Object Store
reads/writes, arbitrary pushes/projects/dates, optimization, a broker/exchange
connection, paper trading, or live trading. Raw output must remain ignored;
only separately reviewed sanitized content-bound evidence may be tracked.

No walk-forward command has been executed and no QCC has been intentionally
consumed by this workflow. No fold result, profitability conclusion, or
paper/live approval exists.
