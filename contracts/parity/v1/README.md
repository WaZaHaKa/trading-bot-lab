# Cross-engine parity contract v1

This contract compares QuantConnect LEAN backtest observations with the independent local
Python CSV oracle. It is synthetic, backtest-only, and contains no network, brokerage,
credential, live-trading, leverage, or shorting path.

Both engines consume the same versioned scenario but implement strategy, sizing, fees,
slippage, risk, fills, and accounting independently. Sharing those implementations would
turn parity into a self-comparison and is prohibited.

## Numeric and timing rules

- Every numeric trace value is a finite canonical decimal string; JSON numbers are not valid.
- A close-generated target can execute only at the next input bar's open.
- Fees are one basis point of execution notional. Slippage is two basis points adverse to
  the order side: buys execute above and sells below the reference open.
- Quantity is exact for the v1 scenario because `quantity_precision` is zero.
- Price tolerance is `0.00000001`, money tolerance is `0.01`, and ratio tolerance is
  `0.0000001`. Structural fields, timestamps, directions, counts, risk reasons, and
  final-bar behavior are exact.
- The one-cent money tolerance and derived-ratio tolerance cover the named fee-model
  difference: LEAN's fee hook observes the security's cached pre-fill price, while the local
  oracle charges its notional-bps fee on the adverse-slippage execution price. They do not
  permit a different fill direction, quantity, timing, price, or unexplained cent-level drift.
- The final bar can generate a target but cannot create an intent or fill without a later bar.

## Provenance

An actual local trace uses `local_python_oracle_observation`. An actual LEAN trace uses
`lean_engine_observation`. Test-only candidate traces must use
`contract_fixture_not_engine_observation`; that label explicitly means the trace was not
observed from LEAN and must never be presented as cloud verification.

All traces bind the contract, schemas, scenario manifest, exact fixture bytes, and normalized
bars by SHA-256. Cloud/account identifiers and absolute paths are outside the schema.

The comparator is offline. It accepts already-produced JSON traces and never invokes LEAN,
QuantConnect, a network client, or a data download.

`scripts/prepare_lean_parity_data.py` deterministically converts the committed CSV into
ignored LEAN daily-equity, map, and factor files. It never downloads data or spends QCC,
and refuses to overwrite differing files. This gives a local LEAN engine the exact
synthetic bars once the normal workspace support databases and Docker engine already
exist. The converter is not itself a LEAN observation or trace producer.
