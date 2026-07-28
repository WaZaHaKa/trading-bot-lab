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
