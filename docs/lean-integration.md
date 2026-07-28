# LEAN integration guide

Status: **LEAN cloud engine validation completed for both research projects;
live trading remains prohibited.**

LEAN is the primary cross-asset research engine. The local Python CSV engine
remains an independent deterministic oracle and must not be deleted or changed
to call LEAN internally.

## Layout

```text
lean-workspace/
  lean.json                         ignored operator-local workspace linkage
  data/                             ignored local/synthetic data
  storage/                          ignored local Object Store
  Strategies/
    SkeletonBacktest/
      main.py
      config.json                   public configuration only
      README.md
    MovingAverageBaseline/
      main.py
      config.json                   public configuration only
      README.md

contracts/lean-cloud-validation/v1/
  record.schema.json                closed public record schema
  2026-07-28.json                   sanitized canonical observations

lean/                               preserved pre-activation files
```

Global CLI credentials belong in the user's `.lean/credentials` file and must
never be copied into this repository. The current `lean-workspace/lean.json` is
operator-local, Git-ignored, and must never be force-added. Ignoring it preserves
the locally working linkage in place; it does not delete or rewrite the file. The
tracked project configurations may
contain only `algorithm-language`, `parameters`, and a public semicolon-free
`description`; `organization-id`, `cloud-id`, and `local-id` are private local
linkage metadata even though they are not access tokens.

## Projects

`SkeletonBacktest` subscribes to daily SPY data, establishes an explicit cash
account and one-times leverage, and intentionally submits no orders. It checks
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

## Canonical cloud validation

The non-verbose 2026-07-28 pushes, compilations, and backtests completed
successfully with LEAN `2.5.0.0.17942` and no live deployment.

| Project | Orders | Simulated fees | Maximum drawdown | Starting equity | Ending equity |
|---|---:|---:|---:|---:|---:|
| `SkeletonBacktest` | 0 | $0 | 0% | $100,000.00 | $100,000.00 |
| `MovingAverageBaseline` | 40 | $40 | 1.3% | $100,000.00 | $102,118.20 |

Both compilations emitted the non-fatal stable warning category
`discouraged_exception_handling`. The observations validate that the projects
can be synchronized, compiled, initialized, and completed in the cloud. The
moving-average result is not evidence of profitability, robustness, or
strategy quality.

The sanitized record is
`contracts/lean-cloud-validation/v1/2026-07-28.json`. It contains canonical
finite decimal strings, stable warning categories, public parameters, and
SHA-256 digests of the exact source, public configuration, and ignored local
push/validation logs. It omits account metadata, identifiers, URLs, absolute
paths, warning text, and raw cloud output. Raw logs remain ignored and must
never be committed.

The repository-root `.gitattributes` requires canonical LF checkout bytes for
every digest-bound public LEAN source and configuration file on every operating
system. These remain exact-byte SHA-256 digests; hashing does not normalize
newlines.

The Draft 2020-12 schema closes project identity, canonical parameters, finite
canonical decimal ranges, lifecycle implications, and derived validation
classification. The typed Python normalizer remains authoritative for recursive
privacy screening and for requiring each project timestamp's UTC date to equal
`execution_date_utc`; portable JSON Schema cannot compare those sibling string
values.

Because the cloud runs used QuantConnect SPY data instead of the committed
synthetic parity fixture, the classifications are intentionally separate:

| Classification | Status |
|---|---|
| Cloud engine validation | `passed` |
| Cloud project synchronization | `passed` |
| Source/configuration validation | `passed` |
| Execution-timing parity | `pending_identical_data_execution` |
| Numerical accounting parity | `pending_identical_data_execution` |

Only the content-bound cross-engine comparator may establish future timing or
accounting parity; the cloud-validation record cannot promote those statuses.

## Private local-linkage workflow

Leave the working `lean-workspace/lean.json` in place as an ignored local file,
and keep one recoverable operator-only backup outside the repository. Preserve
CLI-linked versions of the two project configurations in a clearly named local
stash or that backup before restoring their tracked public three-key forms.
After a cloud session, refresh the private backup before preparing any commit.

Never pop or apply a linkage stash during a commit workflow. Before review,
verify that `git check-ignore --no-index lean-workspace/lean.json` succeeds,
that the file is absent from `git ls-files`, that both project configs contain
exactly the three public top-level keys, and that the staged diff contains no
linkage IDs. Preflight reads the staged project-config blobs, so sanitizing only
the worktree cannot hide private index content. This workflow preserves
repeatable local cloud access without placing account or organization metadata
in branch history.

## Manual cloud workflow

Run the preflight first. Authentication is interactive and must not be pasted
into chat, command arguments, documentation, or shell history. Run cloud
commands only during a separately authorized validation session.

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
Record only fields allowed by the versioned sanitized schema; keep any IDs,
links, or raw output in ignored local evidence. `lean cloud status` reports live
deployment status, not cloud-backtest status.

## Local LEAN execution

Local LEAN execution is optional. It requires Docker plus already-present or
synthetic data. Never use `lean data download`, `--download-data`, or the
QuantConnect historical data provider. A missing Docker engine or dataset does
not invalidate the completed cloud-engine checks, but it leaves identical-data
parity pending.

See `windows-lean-setup.md`, `qcc-guardrails.md`,
`execution-timing-comparison.md`, `risk-policy-mapping.md`, and
`cross-engine-parity.md`.
