# LEAN cloud parity workspace

This is the active, backtest-only LEAN workspace. It contains two deliberately
small QuantConnect Cloud projects and is an additive migration target: the
historical `../lean/` tree remains untouched until both projects compile,
backtest, and pass a documented parity review.

Live trading remains prohibited.

## Safety boundary

- Cloud backtesting only. Both algorithms raise during initialization when
  `live_mode` is true.
- SPY daily data only; no crypto, derivatives, shorting, margin, leverage,
  optimization, broker integration, or live deployment configuration.
- The projects never download local market data and contain no external API
  calls.
- Credentials remain in the operator's global LEAN CLI credential store. Never
  put user IDs, tokens, secrets, passwords, or encryption-key paths in this
  public repository. The CLI may add reviewed `organization-id`, `cloud-id`, or
  `local-id` project metadata; these identifiers are not authentication
  credentials.
- A reviewed workspace `lean.json` is required by the CLI, but it must remain
  credential-free. Standard `lean init` also downloads sample data, so it is not
  part of this no-data-download sprint.

## Projects

- `Strategies/SkeletonBacktest`: subscribes to SPY and submits no orders. Its
  configuration exposes a maximum allocation of 5% so a later extension cannot
  silently inherit LEAN's 100% starter allocation.
- `Strategies/MovingAverageBaseline`: a long-only 20/50 moving-average smoke
  test with explicit fees, slippage, caps, and next-open execution.

The moving-average project separates signal, portfolio target, risk, and
execution responsibilities. A completed daily close may create a target, but
only a market-on-open order can act on that target at the following session's
open. The final close can therefore leave an unfilled order; `on_end_of_algorithm`
cancels it instead of fabricating a final fill.

This is intentionally a hybrid of separated, Framework-style components and a
small explicit orchestrator. LEAN's default Framework execution models can
submit immediate orders; the custom execution boundary is necessary to enforce
the stricter completed-close-to-next-open MOO contract.

The cloud guard intentionally differs from the local oracle after a circuit
breaker. The local simulator latches and continues valuation without an
automatic order. This cloud baseline cancels pending orders and, when invested,
submits one conservative market-on-open liquidation for the next session. The
difference must remain visible in parity reports.

## Operator commands

Authenticate interactively from the repository root. Do not place a token on
the command line.

```powershell
$LeanExe = (Resolve-Path ".\.venv\Scripts\lean.exe").Path
& $LeanExe login
& $LeanExe whoami
```

After the workspace has a reviewed credential-free `lean.json`, run these from
`lean-workspace/`:

`cloud push` synchronizes the selected remote project and can remove remote-only
files. Use dedicated cloud projects, verify the project mapping first, and keep
the explicit `--project` scope shown below.

```powershell
& $LeanExe cloud push --project ".\Strategies\SkeletonBacktest"
& $LeanExe cloud backtest "Strategies/SkeletonBacktest" --push `
  --name "skeleton-activation"

& $LeanExe cloud push --project ".\Strategies\MovingAverageBaseline"
& $LeanExe cloud backtest "Strategies/MovingAverageBaseline" --push `
  --name "moving-average-parity"
```

Do not add `--force`, `--open`, or `--verbose`. Do not run `lean data`, local
`lean backtest --download-data`, `lean optimize`, or any `lean live` /
`lean cloud live` command. `lean cloud status` reports live-deployment state,
not cloud-backtest progress; the synchronous `lean cloud backtest` output is the
CLI status stream for these runs.
