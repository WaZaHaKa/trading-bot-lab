from __future__ import annotations

import copy
import json
import os
import subprocess
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from scripts.preflight_check import _tracked_artifact_findings
from trading_bot_lab.parity.contract import deterministic_json
from trading_bot_lab.walk_forward import observation as walk_forward_observation
from trading_bot_lab.walk_forward import operator as walk_forward_operator
from trading_bot_lab.walk_forward.contract import (
    FOLD_IDS,
    MAX_OBSERVATION_PAYLOAD_BYTES,
    OBSERVATION_PREFIX,
    ProtocolBundle,
    compact_json,
    load_protocol_bundle,
)
from trading_bot_lab.walk_forward.observation import (
    WalkForwardObservationError,
    aggregate_observation_files,
    aggregate_observations,
    extract_observation,
    load_aggregate_record,
    load_observation,
    normalize_aggregate_record,
    normalize_observation,
    parse_observation_log,
    write_aggregate_files,
    write_aggregate_record,
)
from trading_bot_lab.walk_forward.operator import (
    WalkForwardOperatorError,
    build_cloud_command_plan,
    build_parser,
    phase_is_read_only,
    run_phase,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def protocol_bundle() -> ProtocolBundle:
    return load_protocol_bundle()


def _observation(
    bundle: ProtocolBundle,
    fold_index: int = 0,
    *,
    engine_version: str = "2.5.0.0.17942",
) -> dict[str, Any]:
    fold = bundle.folds[fold_index]
    return {
        "costs": copy.deepcopy(bundle.manifest["costs"]),
        "data": copy.deepcopy(bundle.manifest["data"]),
        "engine": {"name": "quantconnect_lean", "version": engine_version},
        "evaluation_end": fold.evaluation_end,
        "evaluation_start": fold.evaluation_start,
        "execution": copy.deepcopy(bundle.manifest["execution"]),
        "fold_id": fold.fold_id,
        "metrics": {
            "benchmark_ending_value": "105",
            "benchmark_return": "0.05",
            "benchmark_starting_value": "100",
            "ending_equity_usd": "110000",
            "estimated_slippage_usd": "2",
            "excess_return": "0.05",
            "fill_count": 2,
            "maximum_drawdown": "0.02",
            "order_count": 2,
            "rejected_order_count": 0,
            "starting_equity_usd": "100000",
            "total_fees_usd": "5",
            "total_return": "0.1",
        },
        "protocol_version": "1.0.0",
        "risk": copy.deepcopy(bundle.manifest["risk"]),
        "schema_version": "1.0.0",
        "source": {
            "project_source_sha256": bundle.project_source_sha256,
            "public_configuration_sha256": bundle.public_configuration_sha256,
        },
        "state": {
            "completion_status": "completed",
            "final_evaluation_close_seen": True,
            "final_position": {"quantity": "0", "state": "cash"},
            "first_eligible_evaluation_timestamp": f"{fold.evaluation_start}T00:00:00Z",
            "halt_reasons": [],
            "last_processed_evaluation_timestamp": f"{fold.evaluation_end}T23:59:59Z",
            "risk_halted": False,
            "warmup_completed": True,
        },
        "strategy": copy.deepcopy(bundle.manifest["strategy"]),
    }


def _five_observations(bundle: ProtocolBundle) -> list[dict[str, Any]]:
    return [_observation(bundle, index) for index in range(len(FOLD_IDS))]


def _write_log(path: Path, payload: bytes, *, prefix: bytes | None = None) -> Path:
    selected_prefix = OBSERVATION_PREFIX.encode("ascii") if prefix is None else prefix
    path.write_bytes(b"ordinary LEAN output\n" + selected_prefix + payload + b"\r\ntrailing\n")
    return path


def test_normalizes_only_the_exact_fold_and_fixed_contract(
    protocol_bundle: ProtocolBundle,
) -> None:
    for index, fold_id in enumerate(FOLD_IDS):
        candidate = _observation(protocol_bundle, index)
        normalized = normalize_observation(candidate, bundle=protocol_bundle)

        assert normalized == candidate
        assert normalized["fold_id"] == fold_id
        assert normalized["strategy"] == protocol_bundle.manifest["strategy"]
        assert normalized["risk"] == protocol_bundle.manifest["risk"]
        assert normalized["costs"] == protocol_bundle.manifest["costs"]
        assert normalized["execution"]["orders_during_warmup"] is False
        assert normalized["execution"]["timing"] == "next_market_open"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("fold_id", "spy-2026", "unknown walk-forward fold"),
        ("evaluation_start", "2021-01-02", "evaluation_start differs"),
        ("evaluation_end", "2021-12-30", "evaluation_end differs"),
    ],
)
def test_observation_rejects_unknown_folds_and_date_overrides(
    protocol_bundle: ProtocolBundle,
    field: str,
    value: str,
    message: str,
) -> None:
    candidate = _observation(protocol_bundle)
    candidate[field] = value

    with pytest.raises(WalkForwardObservationError, match=message):
        normalize_observation(candidate, bundle=protocol_bundle)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("strategy", "fast_period", 21),
        ("strategy", "slow_period", 51),
        ("strategy", "warmup_bars", 49),
        ("strategy", "target_weight", "0.11"),
        ("risk", "account_type", "margin"),
        ("risk", "leverage", "2"),
        ("risk", "long_only", False),
        ("risk", "max_position_weight", "0.11"),
        ("risk", "max_daily_loss", "0.03"),
        ("risk", "max_drawdown", "0.06"),
        ("costs", "fee_bps", "0"),
        ("costs", "minimum_fee_usd", "0"),
        ("costs", "slippage_bps", "0"),
        ("data", "symbol", "QQQ"),
        ("data", "resolution", "hour"),
        ("execution", "orders_during_warmup", True),
        ("execution", "timing", "same_close"),
    ],
)
def test_observation_rejects_cross_fold_parameter_or_runtime_contract_drift(
    protocol_bundle: ProtocolBundle,
    section: str,
    field: str,
    value: object,
) -> None:
    candidate = _observation(protocol_bundle)
    candidate[section][field] = value

    with pytest.raises(WalkForwardObservationError, match="fixed v1 protocol"):
        normalize_observation(candidate, bundle=protocol_bundle)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("project_source_sha256", "0" * 64, "source hash has drifted"),
        ("public_configuration_sha256", "1" * 64, "configuration hash has drifted"),
    ],
)
def test_observation_rejects_source_and_configuration_drift(
    protocol_bundle: ProtocolBundle,
    field: str,
    value: str,
    message: str,
) -> None:
    candidate = _observation(protocol_bundle)
    candidate["source"][field] = value

    with pytest.raises(WalkForwardObservationError, match=message):
        normalize_observation(candidate, bundle=protocol_bundle)


@pytest.mark.parametrize("version", ["", "dev", "2", "2.5-beta", "2.5.0/host", "2.5.0.0.0.0.1"])
def test_observation_requires_safe_dotted_engine_provenance(
    protocol_bundle: ProtocolBundle,
    version: str,
) -> None:
    candidate = _observation(protocol_bundle, engine_version=version)

    with pytest.raises(WalkForwardObservationError):
        normalize_observation(candidate, bundle=protocol_bundle)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e999", "01", "1.0"])
def test_observation_rejects_nonfinite_or_noncanonical_decimal_metrics(
    protocol_bundle: ProtocolBundle,
    value: str,
) -> None:
    candidate = _observation(protocol_bundle)
    candidate["metrics"]["total_return"] = value

    with pytest.raises(WalkForwardObservationError, match="finite canonical decimal"):
        normalize_observation(candidate, bundle=protocol_bundle)


@pytest.mark.parametrize(
    "private_key",
    [
        "account_id",
        "backtest-id",
        "billing_data",
        "cloudId",
        "credentials",
        "email",
        "license",
        "machine_path",
        "organization_id",
        "owner_name",
        "project_id",
        "raw_order_ids",
        "secret",
        "subscription",
        "token",
        "url",
    ],
)
def test_observation_rejects_identity_bearing_fields(
    protocol_bundle: ProtocolBundle,
    private_key: str,
) -> None:
    candidate = _observation(protocol_bundle)
    candidate["metrics"][private_key] = "redacted"

    with pytest.raises(WalkForwardObservationError):
        normalize_observation(candidate, bundle=protocol_bundle)


@pytest.mark.parametrize(
    "private_value",
    [
        "/home/operator/lean/result.json",
        r"C:\Users\operator\lean\result.json",
        "https://example.invalid/result",
        "operator@example.invalid",
        "account_id=12345",
        "project token",
        "ghp_" + "A" * 20,
        "-----BEGIN " + "PRIVATE KEY-----",
        "Bearer private-credential",
    ],
)
def test_observation_rejects_paths_urls_emails_ids_and_credentials_in_values(
    protocol_bundle: ProtocolBundle,
    private_value: str,
) -> None:
    candidate = _observation(protocol_bundle)
    candidate["engine"]["version"] = private_value

    with pytest.raises(WalkForwardObservationError):
        normalize_observation(candidate, bundle=protocol_bundle)


def test_extracts_exactly_one_bounded_observation_atomically_without_raw_log_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
) -> None:
    candidate = _observation(protocol_bundle)
    payload = compact_json(candidate).encode("utf-8")
    assert b"\n" not in payload
    assert len(payload) <= MAX_OBSERVATION_PAYLOAD_BYTES
    source = _write_log(tmp_path / "raw.log", payload)
    output = tmp_path / "normalized" / "spy-2021.json"
    calls: list[tuple[Path, str]] = []
    original = walk_forward_observation.atomic_write_text

    def recording_atomic_write(path: str | Path, text: str) -> Path:
        calls.append((Path(path), text))
        return original(path, text)

    monkeypatch.setattr(walk_forward_observation, "atomic_write_text", recording_atomic_write)
    written = extract_observation(source, output, bundle=protocol_bundle)

    assert written == output
    assert calls == [(output, deterministic_json(candidate))]
    assert load_observation(output, bundle=protocol_bundle) == candidate
    assert output.read_text(encoding="utf-8") == deterministic_json(candidate)
    assert "ordinary LEAN output" not in output.read_text(encoding="utf-8")
    assert not list(output.parent.glob(f".{output.name}.*.tmp"))


@pytest.mark.parametrize("line_count", [0, 2])
def test_log_requires_exactly_one_canonical_observation(
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
    line_count: int,
) -> None:
    payload = compact_json(_observation(protocol_bundle)).encode()
    line = OBSERVATION_PREFIX.encode() + payload + b"\n"
    source = tmp_path / "count.log"
    source.write_bytes(b"ordinary output\n" + line * line_count)

    with pytest.raises(WalkForwardObservationError, match="exactly one"):
        parse_observation_log(source, bundle=protocol_bundle)


def test_log_parser_reconstructs_only_supported_physical_line_wrapping(
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
) -> None:
    candidate = _observation(protocol_bundle)
    payload = compact_json(candidate).encode()
    wrapped = b"\r\n".join(payload[index : index + 73] for index in range(0, len(payload), 73))
    source = tmp_path / "wrapped.log"
    source.write_bytes(OBSERVATION_PREFIX.encode() + wrapped + b"\n")

    assert parse_observation_log(source, bundle=protocol_bundle) == candidate


def test_log_reader_never_uses_unbounded_iteration_or_reads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
) -> None:
    payload = compact_json(_observation(protocol_bundle)).encode()
    data = (
        b"x" * (MAX_OBSERVATION_PAYLOAD_BYTES * 2) + b"\n" + OBSERVATION_PREFIX.encode() + payload
    )

    class BoundedReader(BytesIO):
        def __init__(self, initial_bytes: bytes) -> None:
            super().__init__(initial_bytes)
            self.read_limits: list[int] = []

        def __iter__(self) -> Any:
            raise AssertionError("log parsing must not use unbounded physical-line iteration")

        def readline(self, size: int = -1) -> bytes:
            assert 0 < size <= len(OBSERVATION_PREFIX) + MAX_OBSERVATION_PAYLOAD_BYTES + 3
            self.read_limits.append(size)
            return super().readline(size)

    source = tmp_path / "bounded.log"
    source.touch()
    reader = BoundedReader(data)
    original_open = Path.open

    def guarded_open(path: Path, *args: object, **kwargs: object) -> Any:
        if path == source:
            return reader
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    assert parse_observation_log(source, bundle=protocol_bundle) == _observation(protocol_bundle)
    assert len(reader.read_limits) >= 3


def test_log_rejects_oversized_single_or_wrapped_observations(tmp_path: Path) -> None:
    oversized = b"{" + b"x" * MAX_OBSERVATION_PAYLOAD_BYTES
    single = tmp_path / "single.log"
    single.write_bytes(OBSERVATION_PREFIX.encode() + oversized + b"\n")
    wrapped = tmp_path / "wrapped.log"
    wrapped.write_bytes(
        OBSERVATION_PREFIX.encode()
        + b"\n".join(oversized[index : index + 79] for index in range(0, len(oversized), 79))
        + b"\n"
    )

    for source in (single, wrapped):
        with pytest.raises(WalkForwardObservationError, match="size limit"):
            parse_observation_log(source)


def test_log_rejects_duplicate_keys_nonfinite_json_and_noncanonical_encoding(
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
) -> None:
    canonical = compact_json(_observation(protocol_bundle))
    field = '"schema_version":"1.0.0"'
    duplicate = canonical.replace(field, f"{field},{field}", 1).encode()
    nonfinite = canonical.replace('"order_count":2', '"order_count":NaN', 1).encode()
    spaced = json.dumps(_observation(protocol_bundle), sort_keys=True).encode()

    with pytest.raises(WalkForwardObservationError, match="duplicate JSON key"):
        parse_observation_log(
            _write_log(tmp_path / "duplicate.log", duplicate), bundle=protocol_bundle
        )
    with pytest.raises(WalkForwardObservationError, match="non-finite"):
        parse_observation_log(
            _write_log(tmp_path / "nonfinite.log", nonfinite), bundle=protocol_bundle
        )
    with pytest.raises(WalkForwardObservationError, match="canonical compact JSON"):
        parse_observation_log(_write_log(tmp_path / "spaced.log", spaced), bundle=protocol_bundle)


def test_input_and_output_symlinks_are_rejected_cross_platform(
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
) -> None:
    source = _write_log(
        tmp_path / "raw.log",
        compact_json(_observation(protocol_bundle)).encode(),
    )
    input_link = tmp_path / "input-link.log"
    output_target = tmp_path / "target.json"
    output_target.write_text("sentinel", encoding="utf-8")
    output_link = tmp_path / "output-link.json"
    try:
        input_link.symlink_to(source)
        output_link.symlink_to(output_target)
    except OSError:
        pytest.skip("symlink creation is unavailable on this platform")

    with pytest.raises(WalkForwardObservationError, match="input log must not be a symlink"):
        parse_observation_log(input_link, bundle=protocol_bundle)
    with pytest.raises(WalkForwardObservationError, match="must not be symlinks"):
        extract_observation(source, output_link, bundle=protocol_bundle)
    assert output_target.read_text(encoding="utf-8") == "sentinel"


def test_aggregation_requires_all_five_exact_unique_folds(
    protocol_bundle: ProtocolBundle,
) -> None:
    observations = _five_observations(protocol_bundle)

    with pytest.raises(WalkForwardObservationError, match="exact five folds"):
        aggregate_observations(observations[:-1], bundle=protocol_bundle)
    duplicate = observations[:-1] + [copy.deepcopy(observations[0])]
    with pytest.raises(WalkForwardObservationError, match="duplicate fold"):
        aggregate_observations(duplicate, bundle=protocol_bundle)


def test_aggregation_rejects_cross_fold_parameter_drift(
    protocol_bundle: ProtocolBundle,
) -> None:
    observations = _five_observations(protocol_bundle)
    observations[3]["strategy"]["fast_period"] = 19

    with pytest.raises(WalkForwardObservationError, match="fixed v1 protocol"):
        aggregate_observations(observations, bundle=protocol_bundle)


def test_aggregation_reports_runtime_drift_without_reclassifying_strategy_quality(
    protocol_bundle: ProtocolBundle,
) -> None:
    observations = _five_observations(protocol_bundle)
    observations[3]["engine"]["version"] = "2.6.0"
    aggregate = aggregate_observations(observations, bundle=protocol_bundle)

    assert [item["fold_id"] for item in aggregate["fold_results"]] == list(FOLD_IDS)
    assert aggregate["runtime_consistency"] == {
        "consistent": False,
        "versions": ["2.5.0.0.17942", "2.6.0"],
    }
    assert aggregate["contract_status"] == "walk_forward_contract_complete"
    serialized = deterministic_json(aggregate).casefold()
    for prohibited_claim in (
        "profitable",
        "robust",
        "production-ready",
        "paper-ready",
        "live-ready",
        "approved strategy",
    ):
        assert prohibited_claim not in serialized


def test_aggregate_summary_is_descriptive_derived_and_canonical(
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
) -> None:
    observations = _five_observations(protocol_bundle)
    aggregate = aggregate_observations(observations, bundle=protocol_bundle)
    output = write_aggregate_record(
        observations, tmp_path / "aggregate.json", bundle=protocol_bundle
    )

    assert aggregate["summary"] == {
        "benchmark_beating_fold_count": 5,
        "completed_fold_count": 5,
        "halt_count": 0,
        "median_benchmark_return": "0.05",
        "median_excess_return": "0.05",
        "median_strategy_return": "0.1",
        "positive_return_fold_count": 5,
        "total_fees_usd": "25",
        "total_orders": 10,
        "worst_fold_return": "0.1",
        "worst_maximum_drawdown": "0.02",
    }
    assert output.read_text(encoding="utf-8") == deterministic_json(aggregate)
    assert load_aggregate_record(output, bundle=protocol_bundle) == aggregate
    tampered = copy.deepcopy(aggregate)
    tampered["summary"]["positive_return_fold_count"] = 0
    with pytest.raises(WalkForwardObservationError, match="not derived"):
        normalize_aggregate_record(tampered, bundle=protocol_bundle)


def test_default_operator_plan_is_read_only_local_and_deterministic(
    monkeypatch: pytest.MonkeyPatch,
    protocol_bundle: ProtocolBundle,
) -> None:
    args = build_parser().parse_args([])
    monkeypatch.setattr(
        walk_forward_operator,
        "extract_observation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected write")),
    )
    monkeypatch.setattr(
        walk_forward_operator,
        "write_aggregate_files",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected write")),
    )

    first = run_phase(args, bundle=protocol_bundle)
    second = run_phase(args, bundle=protocol_bundle)
    plan = json.loads(first)

    assert first == second == deterministic_json(plan)
    assert args.phase == "plan"
    assert plan["default_phase_read_only"] is True
    assert plan["cloud_commands_executed"] == 0
    assert plan["network_activity"] == "none"
    assert plan["optimization_jobs_planned"] == 0
    assert plan["cloud_backtests_planned"] == 5
    assert [fold["fold_id"] for fold in plan["folds"]] == list(FOLD_IDS)
    assert phase_is_read_only("plan")
    assert phase_is_read_only("validate")
    assert phase_is_read_only("print-cloud-commands")
    assert phase_is_read_only("evidence")
    assert not phase_is_read_only("extract")
    assert not phase_is_read_only("aggregate")
    with pytest.raises(WalkForwardOperatorError, match="unknown walk-forward phase"):
        phase_is_read_only("cloud-run")


def test_cloud_command_plan_is_exact_structured_scoped_and_print_only(
    protocol_bundle: ProtocolBundle,
) -> None:
    commands = build_cloud_command_plan()

    assert len(commands) == 5
    assert tuple(command.fold_id for command in commands) == FOLD_IDS
    for command, fold_id in zip(commands, FOLD_IDS, strict=True):
        assert command.backtest_name == f"wf-v1-{fold_id}"
        assert command.argv == (
            "lean",
            "cloud",
            "backtest",
            "$LEAN_WALK_FORWARD_PROJECT_ID",
            "--name",
            f"wf-v1-{fold_id}",
            "--parameter",
            "fold-id",
            fold_id,
            "--parameter",
            "optimization-mode",
            "false",
        )
        assert "--push" not in command.argv
        assert command.as_dict()["argv"] == list(command.argv)
        lowered = " ".join(command.argv).casefold()
        for forbidden in (
            "--force",
            "--verbose",
            "--open",
            "optimize",
            " live ",
            "data download",
            "object store",
        ):
            assert forbidden not in f" {lowered} "

    printed = run_phase(
        build_parser().parse_args(["print-cloud-commands"]),
        bundle=protocol_bundle,
    )
    assert printed.splitlines() == [command.render() for command in commands]


@pytest.mark.parametrize(
    "argv",
    [
        ["plan", "--start-date", "2021-01-01"],
        ["plan", "--end-date", "2021-12-31"],
        ["plan", "--fold-id", "spy-2021"],
        ["plan", "--project", "OtherProject"],
        ["cloud-run"],
    ],
)
def test_operator_has_no_arbitrary_date_fold_project_or_cloud_run_surface(
    argv: list[str],
) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(argv)


def test_operator_aggregate_requires_exactly_five_paths(protocol_bundle: ProtocolBundle) -> None:
    args = build_parser().parse_args(
        ["aggregate", "--observation", "one.json", "--observation", "two.json"]
    )

    with pytest.raises(WalkForwardOperatorError, match="exactly five"):
        run_phase(args, bundle=protocol_bundle)


def test_raw_normalized_aggregate_and_backtest_outputs_are_ignored_and_rejected_if_tracked() -> (
    None
):
    paths = (
        "logs/walk-forward/v1/wf-v1-spy-2021.log",
        "reports/walk-forward/v1/observations/spy-2021.json",
        "reports/walk-forward/v1/aggregate-record.json",
        "lean-workspace/Strategies/WalkForwardMovingAverageV1/backtests/wf-v1-spy-2021/result.json",
    )
    for relative in paths:
        ignored = subprocess.run(
            ["git", "-C", str(ROOT), "check-ignore", "--no-index", "--quiet", "--", relative],
            check=False,
        )
        assert ignored.returncode == 0, relative
        assert _tracked_artifact_findings([relative]), relative


def test_ci_covers_windows_without_invoking_walk_forward_or_lean_cloud() -> None:
    workflows = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / ".github" / "workflows").glob("*")
    )
    lowered = workflows.casefold()

    assert "os: [ubuntu-latest, windows-latest]" in workflows
    assert "run_walk_forward_v1.py" not in workflows
    assert "lean cloud" not in lowered
    assert "lean optimize" not in lowered
    assert "lean live" not in lowered
    assert "data download" not in lowered


@pytest.mark.parametrize(
    ("metric", "value"),
    [
        ("starting_equity_usd", "99999"),
        ("ending_equity_usd", "110001"),
        ("total_return", "0.09"),
        ("benchmark_ending_value", "106"),
        ("benchmark_return", "0.04"),
        ("excess_return", "0.04"),
    ],
)
def test_observation_requires_exact_starting_capital_and_metric_coherence(
    protocol_bundle: ProtocolBundle,
    metric: str,
    value: str,
) -> None:
    candidate = _observation(protocol_bundle)
    candidate["metrics"][metric] = value

    with pytest.raises(WalkForwardObservationError):
        normalize_observation(candidate, bundle=protocol_bundle)


@pytest.mark.parametrize(
    "value",
    [
        "1e100000",
        "9" * (MAX_OBSERVATION_PAYLOAD_BYTES + 1),
    ],
)
def test_observation_rejects_huge_exponents_and_oversized_decimal_strings_cleanly(
    protocol_bundle: ProtocolBundle,
    value: str,
) -> None:
    candidate = _observation(protocol_bundle)
    candidate["metrics"]["total_return"] = value

    with pytest.raises(WalkForwardObservationError):
        normalize_observation(candidate, bundle=protocol_bundle)


def test_input_log_rejects_a_symlinked_ancestor_cross_platform(
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
) -> None:
    real_directory = tmp_path / "real"
    real_directory.mkdir()
    source = _write_log(
        real_directory / "raw.log",
        compact_json(_observation(protocol_bundle)).encode(),
    )
    linked_directory = tmp_path / "linked"
    try:
        linked_directory.symlink_to(real_directory, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable on this platform")
    linked_source = linked_directory / source.name

    with pytest.raises(WalkForwardObservationError, match="symlink"):
        parse_observation_log(linked_source, bundle=protocol_bundle)


def test_operator_confines_all_writes_to_the_ignored_report_root(
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
) -> None:
    source = _write_log(
        tmp_path / "raw.log",
        compact_json(_observation(protocol_bundle)).encode(),
    )
    protected = ROOT / "contracts" / "walk-forward" / "v1" / "protocol.json"
    original = protected.read_bytes()

    extract_args = build_parser().parse_args(
        ["extract", "--input-log", str(source), "--output", str(protected)]
    )
    with pytest.raises(WalkForwardOperatorError, match="ignored walk-forward report"):
        run_phase(extract_args, bundle=protocol_bundle)

    aggregate_args = build_parser().parse_args(["aggregate", "--output", str(protected)])
    with pytest.raises(WalkForwardOperatorError, match="ignored walk-forward report"):
        run_phase(aggregate_args, bundle=protocol_bundle)
    assert protected.read_bytes() == original


def test_operator_rejects_a_symlink_loop_without_disclosing_the_path(
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    try:
        first.symlink_to(second, target_is_directory=True)
        second.symlink_to(first, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this platform")
    args = build_parser().parse_args(
        ["extract", "--input-log", str(tmp_path / "unused.log"), "--output", str(first / "x.json")]
    )

    with pytest.raises(WalkForwardOperatorError) as captured:
        run_phase(args, bundle=protocol_bundle)
    assert str(first) not in str(captured.value)
    assert str(second) not in str(captured.value)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation is unavailable")
def test_input_log_rejects_a_fifo_before_attempting_to_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
) -> None:
    fifo = tmp_path / "raw.pipe"
    os.mkfifo(fifo)
    monkeypatch.setattr(
        walk_forward_observation,
        "_read_single_payload",
        lambda _path: (_ for _ in ()).throw(AssertionError("FIFO must never be opened")),
    )

    with pytest.raises(WalkForwardObservationError, match="regular file"):
        parse_observation_log(fifo, bundle=protocol_bundle)


def test_untrusted_logs_and_json_artifacts_have_total_byte_and_depth_bounds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
) -> None:
    monkeypatch.setattr(walk_forward_observation, "_MAX_INPUT_LOG_BYTES", 32)
    oversized_log = tmp_path / "oversized.log"
    oversized_log.write_bytes(b"x" * 33)
    with pytest.raises(WalkForwardObservationError, match="byte limit"):
        parse_observation_log(oversized_log, bundle=protocol_bundle)

    monkeypatch.setattr(walk_forward_observation, "_MAX_NORMALIZED_OBSERVATION_BYTES", 64)
    oversized_observation = tmp_path / "oversized.json"
    oversized_observation.write_bytes(b"{" + b"x" * 64 + b"}")
    with pytest.raises(WalkForwardObservationError, match="byte limit"):
        load_observation(oversized_observation, bundle=protocol_bundle)

    monkeypatch.setattr(walk_forward_observation, "_MAX_NORMALIZED_OBSERVATION_BYTES", 64 * 1024)
    deeply_nested = tmp_path / "deep.json"
    deeply_nested.write_text(
        '{"x":' + "[" * 20_000 + "0" + "]" * 20_000 + "}\n",
        encoding="utf-8",
    )
    with pytest.raises(WalkForwardObservationError, match="invalid JSON|structural limit"):
        load_observation(deeply_nested, bundle=protocol_bundle)


def test_file_aggregation_checks_count_and_output_alias_before_loading(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
) -> None:
    monkeypatch.setattr(
        walk_forward_observation,
        "load_observation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not load")),
    )
    with pytest.raises(WalkForwardObservationError, match="exactly five artifact paths"):
        aggregate_observation_files([tmp_path / f"{index}.json" for index in range(6)])

    paths = [tmp_path / f"{index}.json" for index in range(5)]
    with pytest.raises(WalkForwardObservationError, match="must differ"):
        write_aggregate_files(paths, paths[0], bundle=protocol_bundle)
