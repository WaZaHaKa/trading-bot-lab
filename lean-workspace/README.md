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
  public repository.
- The CLI may add `organization-id`, `cloud-id`, or `local-id` to local project
  configurations. Treat all three as private linkage metadata: preserve them
  outside tracked files and never commit them.
- The current workspace `lean.json` is operator-local, Git-ignored, and must
  never be force-added. The ignore rule preserves the local file in place.
  Standard `lean init` also downloads sample data, so it is not part of this
  no-data-download sprint.

## Projects

- `Strategies/SkeletonBacktest`: subscribes to SPY and submits no orders. Its
  configuration exposes a maximum allocation of 5% so a later extension cannot
  silently inherit LEAN's 100% starter allocation.
- `Strategies/MovingAverageBaseline`: a long-only 20/50 moving-average smoke
  test with explicit fees, slippage, caps, and next-open execution.

The moving-average project separates signal, portfolio target, risk, and
execution responsibilities. A completed daily close may create a target, but
only a market-on-open order can act on that target at the following session's
open. The final close can therefore leave an unfilled order;
`on_end_of_algorithm` cancels it instead of fabricating a final fill.

This is intentionally a hybrid of separated, Framework-style components and a
small explicit orchestrator. LEAN's default Framework execution models can
submit immediate orders; the custom execution boundary is necessary to enforce
the stricter completed-close-to-next-open MOO contract.

The cloud guard intentionally differs from the local oracle after a circuit
breaker. The local simulator latches and continues valuation without an
automatic order. This cloud baseline cancels pending orders and, when invested,
submits one conservative market-on-open liquidation for the next session. The
difference must remain visible in parity reports.

## Private linkage preservation

Leave `lean.json` in place as ignored operator-local state and keep a recoverable
backup in a private directory outside the repository. Preserve CLI-linked
versions of the tracked project configs in that backup or a clearly named local
stash before restoring their public three-key forms. After an authorized cloud
session, refresh the private copies before preparing a commit.

Do not apply a linkage stash while preparing a commit. This separation lets the
cloud CLI retain repeatable local linkage without copying account or
organization metadata into public Git history. Confirm that
`git check-ignore --no-index lean.json` succeeds from this directory, the file
is absent from `git ls-files`, and the staged project configs contain no linkage
IDs. Repository preflight inspects the staged config blobs rather than trusting
a different worktree copy.

## Operator commands

Authenticate interactively from the repository root. Do not place a token on
the command line.

```powershell
$LeanExe = (Resolve-Path ".\.venv\Scripts\lean.exe").Path
& $LeanExe login
& $LeanExe whoami
```

After restoring the private operator linkage, run these from
`lean-workspace/` only during a separately authorized validation session.

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
