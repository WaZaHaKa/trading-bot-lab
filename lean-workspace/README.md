# LEAN cloud parity workspace

This active backtest-only workspace contains two historical cloud projects, one
synthetic local parity project, and one dedicated fixed walk-forward project.
The preserved historical `../lean/` tree remains untouched.

Live trading remains prohibited.

## Safety boundary

- Backtesting only. Every project rejects LEAN live mode during initialization.
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
- `Strategies/ParityFixtureV1`: a synthetic, long-or-flat identical-data project
  whose local-file run is permitted only through the pinned rootless runtime
  operator in `scripts/run_lean_parity_local.py`.
- `Strategies/WalkForwardMovingAverageV1`: a fixed 20/50 adjusted-daily-SPY
  evaluation over the predeclared `spy-2021` through `spy-2025` folds. It accepts
  only a closed fold ID, uses 50 preceding bars only for no-trade/no-metric
  warmup, and emits one sanitized content-bound observation.

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

The local parity operator copies only the public parity project into an ignored
temporary workspace. LEAN CLI may add a generated local ID to that copy, which
is removed with the current-run sentinel; the tracked config and private cloud
linkage remain untouched. The operator accepts only the pinned immutable engine
image, uses the explicit rootless Docker socket, blocks host HTTP/HTTPS, and
starts the engine only after validating a networkless unprivileged container.

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

## Historical activation commands

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

The two commands above are completed activation history, not walk-forward
authorization.

## Fixed walk-forward operator

From the repository root:

```powershell
python scripts\run_walk_forward_v1.py
python scripts\run_walk_forward_v1.py validate
python scripts\run_walk_forward_v1.py print-cloud-commands
```

The default plan is read-only and network-free. The printer emits exactly five
named future backtests and never starts them; the script has no cloud-run phase.
Exactly those five commands require a separate human authorization. `extract`,
`aggregate`, and `evidence` later process only existing ignored local artifacts.

Every fold keeps USD 100,000, adjusted SPY daily data, 20/50 periods, 50-bar
warmup, 10% target/position and 30% gross caps, cash account, leverage one, a
1 bp fee with USD 1 minimum, 2 bp slippage, 2% daily-loss and 5% drawdown limits,
and next-open execution. `daily_precise_end_time = True` pins the completed bar
to market close; public dates pass unchanged as inclusive boundaries. A halt
latches without automatic liquidation, unlike the older cloud baseline.

No walk-forward cloud execution, optimization, data upload/download, Object
Store action, broker/exchange action, paper trade, or live trade has occurred.
Raw results stay ignored; only separately reviewed sanitized, content-bound
evidence may be tracked. No fold result, profitability/robustness claim, or
paper/live readiness exists.
