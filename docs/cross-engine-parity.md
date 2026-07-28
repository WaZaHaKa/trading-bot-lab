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

The v1 input is exactly
`tests/fixtures/parity/v1/synthetic_weekdays.csv`, whose exact-byte SHA-256 is
`a68bcf7fc30d2593b32e5a98852c4f8e0190ed99865640485b344515d9f1f78a`.
The fixture and its `1.0.0` scenario manifest require LF bytes. CRLF conversion,
mutation, truncation, extra rows, invalid or non-finite OHLCV values, and any
attempt to sort or repair the input fail before strategy logic runs.

## Comparison dimensions

The comparator reports divergences by dimension instead of collapsing them
into one unexplained pass/fail value:

- fixture identity and version bindings,
- visible bars and trailing-only signal history,
- signal, intent, and fill timing,
- trade direction and count,
- position quantity and average cost,
- fees and adverse slippage,
- cash, realized and unrealized PnL, and equity,
- exposure and drawdown,
- final-bar pending-signal behavior,
- risk rejection and halt state.

Raw LEAN headline statistics are not treated as parity evidence because their
trade-count, turnover, benchmark, and P&L definitions differ from the local
report contract.

Categorical values, timestamps, integer fields, direction, count, risk reasons,
and final-bar fields use exact equality. The versioned v1 absolute tolerances
are price `0.00000001`, money `0.01`, quantity `0`, and ratio `0.0000001`.
The money and derived-ratio tolerances narrowly cover the documented fee-hook
difference: LEAN's custom fee model receives the security's cached price, while
the local oracle charges on its adverse-slippage execution price. A tolerance
is not permission for a timing, direction, quantity, or unexplained accounting
difference.

## Provenance states

- `local_python_oracle_observation`: produced by the repository's local engine.
- `lean_engine_observation`: produced by an actual LEAN run as a versioned,
  content-bound normalized trace.
- `contract_fixture_not_engine_observation`: sanitized unit-test data used only
  to test the comparator. It must never be reported as a LEAN result.

Only two engine-observation traces over the identical fixture can establish
cross-engine parity. Unit tests over a contract fixture prove comparator
behavior, not LEAN execution. Project IDs, backtest IDs, links, and account
metadata are outside the parity schema and are not needed to establish trace
provenance.

The extractor validates the claimed provenance and canonical trace content; it
does not cryptographically attest which process emitted the log line. The
operator must retain and review the actual ignored LEAN run log and invocation
context. A self-authored or copied trace cannot establish parity.

## 2026-07-28 cloud-validation classification

The successful non-verbose cloud runs used QuantConnect's SPY data, not the
committed synthetic fixture. Their versioned sanitized observations live under
`contracts/lean-cloud-validation/v1/`; the separate schema prevents a cloud
smoke test from being represented as a parity result.

Cloud-record normalization derives lifecycle classifications and fixes both
parity fields at `pending_identical_data_execution`; neither a schema-valid
record nor a cloud headline metric can substitute for two comparator traces.

| Validation dimension | Status | Evidence boundary |
|---|---|---|
| Cloud engine | `passed` | Both projects compiled, initialized, and completed |
| Project synchronization | `passed` | Both explicit non-verbose pushes succeeded |
| Source/configuration | `passed` | Public source/config digests bind the pushed inputs |
| Execution timing parity | `pending_identical_data_execution` | No LEAN trace over the synthetic fixture |
| Numerical accounting parity | `pending_identical_data_execution` | No identical-data normalized comparison |

Both compilations emitted only the non-fatal stable category
`discouraged_exception_handling`. Skeleton's zero-order result and the moving
average observations are cloud-validation facts only; they do not establish
strategy quality or profitability.

## Dedicated LEAN parity project

`lean-workspace/Strategies/ParityFixtureV1` is the only LEAN project for this
identical-data workflow. It is versioned, synthetic-data-only, single-symbol,
long-only, backtest-only, and independent from the local oracle's strategy,
risk, fill, sizing, and accounting implementations.

The project uses one parser and one financial path behind two transport modes:

- `data-transport=local-file` is the offline default and resolves only
  `Globals.data_folder/custom/parity/v1/synthetic_weekdays.csv`.
- `data-transport=object-store` is an explicit later cloud selection and also
  requires `object-store-key=trading-bot-lab/parity/v1/synthetic_weekdays.csv`.

The Object Store key is fixed and contains no account, organization, or project
identity. There is no remote-URL transport, upload, download, automatic Object
Store write, or network fallback. Either transport must deliver bytes matching
the committed fixture hash before any signal or trading logic can run.

## Observation boundary

One completed LEAN run emits exactly one bounded machine-readable line beginning
with `TRADING_BOT_LAB_LEAN_PARITY_V1:`. The suffix is compact canonical JSON for
a v1 `lean_engine_observation`; ordinary human-readable log lines are never
parsed as parity evidence. The strict extractor rejects missing or duplicate
prefixed records, duplicate JSON keys, non-finite or non-canonical numbers,
wrong versions or hashes, extra fields, machine paths, URLs, account metadata,
and credentials. Stable serialization uses a final newline and contains no
wall-clock-derived identity.

Normalized traces retain the existing v1 fields for contract, scenario and
fixture identity; engine name `quantconnect_lean` and its execution-time dotted
numeric LEAN version; visible bars; signals and next-open intents; fills, fees
and slippage; cash, position, average cost, realized and unrealized PnL, equity,
exposure and drawdown; risk events; and final pending-signal status.

## Preparation and pinned local execution

Preparation is offline and copies the exact fixture bytes, after validation, to
the ignored local default path:

```powershell
python scripts\prepare_lean_parity_data.py
```

It writes only
`lean-workspace/data/custom/parity/v1/synthetic_weekdays.csv`, preserves LF and
the exact SHA-256, uses atomic replacement, and rejects unsafe paths and
symlinks. It is idempotent and never invokes LEAN, Docker, QuantConnect, Object
Store, a network, or a paid-data operation.

The Linux-only operator is the authoritative execution boundary. A bare command
runs only read-only preflight; each mutating phase requires its exact public
authorization phrase:

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

The contract accepts one immutable OCI index and its one `linux/amd64`
platform manifest, LEAN CLI `1.0.227`, and the explicit user-owned rootless
Docker socket. The host CLI gets a private credential-free HOME and failing
HTTP/HTTPS proxy while Docker SDK access to the Unix socket must still succeed.
A mutable tag moving after an immutable digest has been reviewed is expected
and does not invalidate that digest. Runtime execution validates and uses the
immutable digest directly. Mutable discovery metadata cannot authorize a pull,
an engine container, or a parity claim.

The realized engine container is validated before start with no network,
published ports, privilege, host namespaces, Docker socket, credentials, or
mounts beyond the exact temporary project, data, output, and CLI paths.
One ignored state file permits only the authorized pull and at most five
serialized executions. Raw logs, runtime audits, normalized traces, comparison
output, and engine results remain ignored.

For a later, separately authorized cloud run, the operator must first place the
same exact fixture bytes at the fixed Object Store key using an explicitly
reviewed manual process, then select both Object Store parameters. Repository
scripts never upload it. Missing content or a wrong hash fails closed. Do not
add `--download-data`, a historical provider, optimization, or remote URL.

This sprint prepares and tests the workflow only. No local LEAN run, cloud run,
Object Store operation, or `lean_engine_observation` has been produced. The
comparator's labelled test candidate remains test data, not parity evidence;
the successful SPY cloud runs do not change that boundary.

## Failure rule

Missing fields, version/hash mismatch, wrong trade direction/count, same-bar or
final-bar fills, unrecognized model exceptions, and values outside tolerance
all fail. A divergence may pass only when its field and rationale are declared
in the contract.
