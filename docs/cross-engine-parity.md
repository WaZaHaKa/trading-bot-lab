# Cross-engine parity methodology

Parity checks model semantics, not strategy profitability.

## Versioned contract

The `contracts/parity/v1/` schema binds:

- a committed synthetic OHLCV fixture and its SHA-256,
- signal windows and target,
- cost, precision, execution, and risk assumptions,
- engine and trace provenance,
- per-bar targets and position/accounting state,
- order intents, risk decisions, fills, and final-bar status,
- field-specific absolute tolerances and named model exceptions.

The local exporter calls the existing Python engine. LEAN must independently
produce a normalized trace; it must not import or reuse local sizing,
accounting, fill, or risk code.

## Compared fields

- bars visible when a signal is made,
- signal timestamp and trailing-only readiness,
- next eligible execution timestamp/phase,
- trade count and long/flat direction,
- resulting position state,
- fee and adverse-slippage assumptions,
- ending cash and equity within declared tolerance,
- risk rejection/halt events,
- absence of an invented final-bar fill.

Raw LEAN headline statistics are not treated as parity evidence because their
trade-count, turnover, benchmark, and P&L definitions differ from the local
report contract.

The v1 money tolerance is one cent and the derived-ratio tolerance is
`0.0000001`. This narrowly covers the documented fee-hook difference: LEAN's
custom fee model receives the security's cached price, while the local oracle
charges on its adverse-slippage execution price. Price, quantity, timing,
direction, count, risk-reason, and final-bar fields remain strict.

## Provenance states

- `local_python_oracle_observation`: produced by the repository's local engine.
- `lean_engine_observation`: produced by an actual LEAN run and accompanied by
  the LEAN version/backtest identifier.
- `contract_fixture_not_engine_observation`: sanitized unit-test data used only
  to test the comparator. It must never be reported as a LEAN result.

Only two engine-observation traces can establish cross-engine parity. Unit
tests over a contract fixture prove comparator behavior, not LEAN execution.

## Offline LEAN fixture preparation

After the reviewed one-time workspace bootstrap, derive LEAN-format data from
the committed fixture without a network request or paid-data command:

```powershell
python scripts\prepare_lean_parity_data.py
```

The default cloud baseline remains SPY. A local LEAN run may override its
validated parameters to `symbol=PARITY`, dates `2024-01-02` through
`2024-01-11`, windows `2/3`, target `0.1`, warm-up `0`, and minimum fee `0`. Use
`--no-update`; never add `--download-data` or a remote historical provider.
The derived files are ignored and the converter refuses to overwrite a
differing file.

```powershell
Push-Location .\lean-workspace
& $LeanExe backtest "Strategies/MovingAverageBaseline" --no-update `
  --output ".\Strategies\MovingAverageBaseline\backtests\parity-v1" `
  --parameter symbol PARITY `
  --parameter start-date 2024-01-02 `
  --parameter end-date 2024-01-11 `
  --parameter fast-period 2 `
  --parameter slow-period 3 `
  --parameter warmup-bars 0 `
  --parameter minimum-fee 0
Pop-Location
```

Docker was not healthy during this implementation, so no actual LEAN fixture
run or `lean_engine_observation` trace has been produced. The comparator remains
ready for such a trace, but its labelled unit-test candidate is not parity
evidence.

## Failure rule

Missing fields, version/hash mismatch, wrong trade direction/count, same-bar or
final-bar fills, unrecognized model exceptions, and values outside tolerance
all fail. A divergence may pass only when its field and rationale are declared
in the contract.
