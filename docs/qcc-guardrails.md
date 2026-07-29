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

## Historical two-backtest manual gate

The following gate governed the completed two-backtest activation sprint. It
does not authorize another execution:

1. Run repository preflight.
2. Confirm the project path and diff.
3. Confirm the command has no live, data, optimization, or force option.
4. Confirm only one named backtest is being started.
5. Record the result ID/link and any platform warning.
6. Stop after the two sprint backtests.

Cloud and local backtests are hypothetical. They place no real orders, make no
profitability claim, and are not financial advice.

## Completed fixed walk-forward gate

The five authorized backtests named `wf-v1-spy-2021` through
`wf-v1-spy-2025` completed in a separate operator phase after the private
project source and public configuration matched merged repository state. They
must not be rerun.

`python scripts/run_walk_forward_v1.py print-cloud-commands` remains a
network-free print-only representation of that closed command set. It has no
cloud-run phase, never uses `--push`, and is not called by `make check` or CI to
start work.

No further cloud execution is authorized by this evidence phase. Data transfer
or purchase, Object Store, optimization, arbitrary project changes,
broker/exchange connections, paper trading, and live trading remain prohibited.
This offline phase executes no cloud command and consumes no QCC.

All five full Download Results JSON files, cloud IDs, URLs, and account metadata
remain private and outside the repository. Normalized per-fold working records
and the generated working aggregate stay ignored. Only the separately reviewed
sanitized aggregate is tracked at
`contracts/walk-forward/v1/2026-07-29-result-aggregate.json`.

The completed aggregate is descriptive research evidence, not a profitability,
robustness, paper-readiness, or live-readiness approval.
