# LEAN integration guide

Status: **LEAN cloud engine validation completed for both SPY research projects;
the pinned local parity runtime is prepared, identical-data execution remains
pending, and live trading remains prohibited.**

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
    ParityFixtureV1/
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

`ParityFixtureV1` is the separate identical-data project. It reads only the
eight-row `1.0.0` scenario fixture at
`tests/fixtures/parity/v1/synthetic_weekdays.csv`, exact-byte SHA-256
`a68bcf7fc30d2593b32e5a98852c4f8e0190ed99865640485b344515d9f1f78a`.
Its strategy, costs, precision, next-row-open execution, position limits, and
halt behavior are fixed to and regression-tested against the versioned
scenario. It emits long or flat observations only and never uses leverage or
shorting.

The dedicated project defaults to `data-transport=local-file` and resolves
`Globals.data_folder/custom/parity/v1/synthetic_weekdays.csv`. The only other
accepted value is the explicit `object-store` mode, which requires
`object-store-key=trading-bot-lab/parity/v1/synthetic_weekdays.csv`. Both modes
use the same parser and financial logic. There is no remote URL, automatic
upload or download, network fallback, external market data, or optimization.

All three projects raise immediately if LEAN reports live mode. None contains a
secret, live configuration, model, data-download path, or broker/exchange
integration. Only the two SPY projects have completed a LEAN run;
`ParityFixtureV1` has not.

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
every CLI-linked project configuration in a clearly named local stash or that
backup before restoring its tracked public three-key form. After a cloud
session, refresh the private backup before preparing any commit.

Never pop or apply a linkage stash during a commit workflow. Before review,
verify that `git check-ignore --no-index lean-workspace/lean.json` succeeds,
that the file is absent from `git ls-files`, that every tracked project config
contains exactly the three public top-level keys, and that the staged diff
contains no linkage IDs. Preflight reads staged project-config blobs, so
sanitizing only the worktree cannot hide private index content. This workflow
preserves repeatable local cloud access without placing account or organization
metadata in branch history.

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

Those commands are the already completed SPY cloud-validation workflow. They do
not authorize a `ParityFixtureV1` push, cloud backtest, or Object Store action.
Any such action requires a later explicit operator approval.

## Identical-data preparation and pinned local execution

The offline preparer validates the committed fixture's LF bytes, schema version,
rows, and exact hash, then atomically copies those same bytes to the ignored
local path:

```powershell
python scripts\prepare_lean_parity_data.py
```

It is idempotent, rejects symlinks and unsafe destinations, and never invokes
LEAN, Docker, QuantConnect, Object Store, a network, or a paid-data operation.
Generated LEAN output stays under ignored `backtests/`; raw logs stay under
`logs/`; normalized local and LEAN traces stay under `reports/` until explicitly
sanitized and reviewed.

The Linux-only operator is the authoritative local path. Its default invocation is
a read-only preflight. Pull, preparation, execution, and comparison each require
their exact public authorization phrase:

```bash
python scripts/run_lean_parity_local.py

python scripts/run_lean_parity_local.py pull \
  --pull-authorization pull-pinned-lean-parity-image

python scripts/run_lean_parity_local.py prepare \
  --prepare-authorization prepare-exact-parity-fixture

python scripts/run_lean_parity_local.py run \
  --run-authorization execute-pinned-parity-v1

python scripts/run_lean_parity_local.py compare \
  --compare-authorization compare-exact-parity-v1
```

The runtime accepts only LEAN CLI `1.0.227` and
`quantconnect/lean@sha256:c03e9acab0ef6bd67cd44b968d10c40c13f4079164b8fe02148de45dbd0c0649`
for `linux/amd64`, whose expected platform manifest is
`sha256:6cdc4112fa14ed99eca5c313bc84c8008cc07d6143e25b3f6ddeb01df2501f0e`.
A mutable tag moving after an immutable digest has been reviewed is expected
and does not invalidate that digest. Runtime execution validates and uses the
immutable digest directly. The optional `latest` observation is stored only as
sanitized, explicitly non-authoritative discovery metadata.

It validates the explicit rootless Unix socket and daemon identity, refuses
system Docker, disables CLI database updates, and uses a private temporary HOME
without credentials. Host HTTP and HTTPS are forced to a failing local proxy
while the Docker SDK must still reach the rootless Unix socket.

A process-local compatibility guard for the audited CLI strips its generated
identity and broker defaults, runs from a temporary copy of the public project,
prevents an implicit image pull or bridge network, and validates the realized
container before starting it. The engine has `network_mode=none`, no published
ports, no host namespace, no Docker socket or credential mount, all capabilities
dropped, `no-new-privileges`, and bounded memory, CPU, and process count.
Ignored state permits one pull and at most five executions, rejects parallel
runs, and cleanup requires a current-run sentinel. Windows can validate the
contract but intentionally refuses Linux Docker execution.

LEAN must emit exactly one bounded line prefixed
`TRADING_BOT_LAB_LEAN_PARITY_V1:`. The strict extractor accepts only its
canonical JSON suffix, requires engine name `quantconnect_lean` and a dotted
numeric runtime version, and rejects malformed or duplicate observations,
non-finite numbers, paths, URLs, account metadata, cloud IDs, and credentials.

A future, separately approved cloud run requires the operator to place the
exact fixture bytes at the fixed Object Store key manually and explicitly set
both transport parameters. Repository tooling performs no Object Store write.
Never use `lean data download`, `--download-data`, a historical data provider,
optimization, remote URL, or network fallback.

No local or cloud LEAN execution or Object Store operation occurred in this
implementation sprint. Execution-timing and numerical-accounting parity remain
`pending_identical_data_execution` until an actual extracted
`lean_engine_observation` passes every comparison dimension.

See `windows-lean-setup.md`, `qcc-guardrails.md`,
`execution-timing-comparison.md`, `risk-policy-mapping.md`, and
`cross-engine-parity.md`.
