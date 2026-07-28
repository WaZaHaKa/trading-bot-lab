# LEAN integration guide

Status: **LEAN active for cloud research/backtesting; live trading remains prohibited.**

LEAN is the primary cross-asset research engine. The local Python CSV engine
remains an independent deterministic oracle and must not be deleted or changed
to call LEAN internally.

## Layout

```text
lean-workspace/
  lean.json                         organization workspace config, credential-free only
  data/                             ignored local/downloaded data
  storage/                          ignored local Object Store
  Strategies/
    SkeletonBacktest/
      main.py
      config.json
      README.md
    MovingAverageBaseline/
      main.py
      config.json
      README.md

lean/                               preserved pre-activation files
```

`lean-workspace/lean.json` may be tracked only after preflight and manual
review confirm that it contains no token, password, key, credential path, or
other secret. Global CLI credentials belong in the user's `.lean/credentials`
file and must never be copied into this repository.

## Projects

`SkeletonBacktest` subscribes to daily SPY data, establishes an explicit cash
account and one-times leverage, and intentionally submits no orders. It proves
initialization, data access, benchmark configuration, and cloud engine wiring
without making a performance claim.

`MovingAverageBaseline` is a daily, trailing-window, long-or-flat SPY baseline.
It validates every parameter, separates signal, portfolio construction, risk,
and next-open execution, caps the symbol at 10% and total exposure at 30%, and
latches daily-loss or peak-drawdown halts. Its risk liquidation is scheduled
for the next market open; the local oracle instead halts without automatic
liquidation. That difference is intentional and must remain visible in parity
reports.

Both projects raise immediately if LEAN reports live mode. Neither project has
a brokerage connection, secret, live configuration, optimizer, model, or data
download path.

## Manual cloud workflow

Run the preflight first. Authentication is interactive and must not be pasted
into chat, command arguments, documentation, or shell history.

```powershell
$LeanExe = (Resolve-Path ".\.venv\Scripts\lean.exe").Path
& $LeanExe --version
& $LeanExe whoami
python scripts\preflight_check.py

Push-Location .\lean-workspace
& $LeanExe cloud push --project ".\Strategies\SkeletonBacktest"
& $LeanExe cloud backtest "Strategies/SkeletonBacktest" --push `
  --name "skeleton-activation-20260728"

& $LeanExe cloud push --project ".\Strategies\MovingAverageBaseline"
& $LeanExe cloud backtest "Strategies/MovingAverageBaseline" --push `
  --name "moving-average-baseline-20260728"
Pop-Location
```

Do not add `--force`, `--verbose`, or `--open`. Do not run an unscoped push.
Record the project, backtest identifier/link, dates, parameters, completion
status, and warnings; do not interpret returns as evidence of profitability.

`lean cloud status` reports live deployment status, not cloud-backtest status.
It is optional and may only be used as a read-only assertion that no live node
exists.

## Local LEAN execution

Local LEAN execution is optional. It requires Docker plus already-present or
synthetic data. Never use `lean data download`, `--download-data`, or the
QuantConnect historical data provider. A missing Docker engine or dataset does
not block this cloud-first sprint.

See `windows-lean-setup.md`, `qcc-guardrails.md`,
`execution-timing-comparison.md`, `risk-policy-mapping.md`, and
`cross-engine-parity.md`.
