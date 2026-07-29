from __future__ import annotations

import copy
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from trading_bot_lab.parity.contract import deterministic_json
from trading_bot_lab.walk_forward import operator as walk_forward_operator
from trading_bot_lab.walk_forward import result_json as walk_forward_result
from trading_bot_lab.walk_forward.contract import FOLD_IDS, ProtocolBundle, load_protocol_bundle
from trading_bot_lab.walk_forward.observation import WalkForwardObservationError
from trading_bot_lab.walk_forward.operator import (
    PROJECT_REFERENCE,
    build_cloud_command_plan,
    build_parser,
    phase_is_read_only,
    run_phase,
)
from trading_bot_lab.walk_forward.result_json import (
    RESULT_AGGREGATE_SCHEMA_PATH,
    RESULT_OBSERVATION_SCHEMA_PATH,
    RESULT_SOURCE_FORMAT,
    WalkForwardResultError,
    aggregate_result_files,
    aggregate_result_observations,
    extract_result_json,
    load_result_aggregate,
    load_result_observation,
    normalize_result_aggregate,
    normalize_result_observation,
    parse_result_json,
    write_result_aggregate_files,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "walk-forward" / "v1" / "quantconnect-result-spy-2021.json"


@pytest.fixture(scope="module")
def protocol_bundle() -> ProtocolBundle:
    return load_protocol_bundle()


def _download_result() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _result() -> dict[str, Any]:
    payload = _download_result()
    values = payload["charts"]["Benchmark"]["series"]["Benchmark"]["values"]
    payload["charts"]["Benchmark"]["series"]["Benchmark"]["values"] = [
        {"x": point[0], "y": point[1]} for point in values
    ]
    payload["orderEvents"] = [
        {
            "fillPrice": order["price"],
            "fillPriceCurrency": "USD",
            "fillQuantity": order["quantity"],
            "orderFee": {"value": {"amount": 1, "currency": "USD"}},
            "orderId": order["id"],
            "status": order["status"],
            "symbol": copy.deepcopy(order["symbol"]),
            "utcTime": order["lastFillTime"],
        }
        for order in payload["orders"].values()
    ]
    return payload


def _write_result(path: Path, payload: object) -> Path:
    path.write_text(deterministic_json(payload), encoding="utf-8", newline="")
    return path


def _fold_result(index: int) -> dict[str, Any]:
    payload = _result()
    fold_id = FOLD_IDS[index]
    year = 2021 + index
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    first = start
    while first.weekday() >= 5:
        first += timedelta(days=1)
    last = end
    while last.weekday() >= 5:
        last -= timedelta(days=1)
    name = f"wf-v1-{fold_id}"
    payload["state"]["Name"] = name
    payload["algorithmConfiguration"]["name"] = name
    payload["algorithmConfiguration"]["parameters"]["fold-id"] = fold_id
    payload["algorithmConfiguration"]["startDate"] = f"{start.isoformat()}T00:00:00Z"
    payload["algorithmConfiguration"]["endDate"] = f"{end.isoformat()}T00:00:00Z"
    payload["algorithmConfiguration"]["outOfSampleMaxEndDate"] = f"{end.isoformat()}T00:00:00Z"
    payload["charts"]["Benchmark"]["series"]["Benchmark"]["values"][0]["x"] = int(
        datetime.combine(first, datetime.min.time(), UTC).timestamp()
    )
    payload["charts"]["Benchmark"]["series"]["Benchmark"]["values"][1]["x"] = int(
        datetime.combine(last, datetime.min.time(), UTC).timestamp()
    )
    event_time = f"{year}-07-01T13:30:00Z"
    for order in payload["orders"].values():
        order["lastFillTime"] = event_time
        order["time"] = event_time
    for event in payload["orderEvents"]:
        event["utcTime"] = event_time
    return payload


def _normalized_five(tmp_path: Path, bundle: ProtocolBundle) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for index, fold_id in enumerate(FOLD_IDS):
        path = _write_result(tmp_path / f"{fold_id}-raw.json", _fold_result(index))
        observations.append(parse_result_json(path, bundle=bundle))
    return observations


def test_valid_2021_result_is_strictly_sanitized_and_content_bound(
    protocol_bundle: ProtocolBundle,
) -> None:
    normalized = parse_result_json(FIXTURE, bundle=protocol_bundle)

    assert normalized["fold_id"] == "spy-2021"
    assert normalized["source_format"] == RESULT_SOURCE_FORMAT
    assert normalized["state"] == {"completion_status": "completed"}
    assert normalized["configuration"]["parameters"] == {
        "fold-id": "spy-2021",
        "optimization-mode": "false",
    }
    assert normalized["protocol"]["content_sha256"] == protocol_bundle.manifest_sha256
    assert normalized["source"]["project_source_sha256"] == (protocol_bundle.project_source_sha256)
    assert normalized["source"]["public_configuration_sha256"] == (
        protocol_bundle.public_configuration_sha256
    )
    assert normalized["metrics"]["directly_reported"] == {
        "ending_equity_usd": "110000",
        "maximum_drawdown": "0.05",
        "order_count": 2,
        "probabilistic_sharpe_ratio": "0.75",
        "sharpe_ratio": "1.2",
        "sortino_ratio": "1.5",
        "starting_equity_usd": "100000",
        "total_fees_usd": "2",
    }
    assert normalized["metrics"]["derived"] == {
        "benchmark_ending_value": "110",
        "benchmark_return": "0.1",
        "benchmark_starting_value": "100",
        "excess_return": "0",
        "total_return": "0.1",
    }
    assert normalized["metrics"]["unavailable"] == [
        "algorithm_risk_halt_state",
        "engine_version",
        "estimated_slippage_usd",
        "rejected_order_count",
        "order_event_detail",
    ]
    assert normalized["orders"] == {
        "completed_order_count": 2,
        "final_position_quantity": "5",
        "final_position_state": "long",
        "order_validation_source": "completed_orders",
    }
    assert "outOfSampleMaxEndDate" not in deterministic_json(normalized)
    serialized = deterministic_json(normalized).casefold()
    for forbidden in (
        "backtest_id",
        "organization_id",
        "project_id",
        "hostname",
        "https://",
        "user_id",
    ):
        assert forbidden not in serialized


def test_official_end_of_day_configuration_timestamp_matches_calendar_fold(
    protocol_bundle: ProtocolBundle,
) -> None:
    payload = _download_result()

    assert payload["algorithmConfiguration"]["endDate"].endswith("23:59:59.999999Z")
    assert parse_result_json(FIXTURE, bundle=protocol_bundle)["evaluation_end"] == "2021-12-31"


def test_order_event_variant_preserves_event_level_validation(
    tmp_path: Path, protocol_bundle: ProtocolBundle
) -> None:
    normalized = parse_result_json(
        _write_result(tmp_path / "event-result.json", _result()), bundle=protocol_bundle
    )

    assert normalized["orders"]["order_validation_source"] == "order_events"
    assert normalized["metrics"]["unavailable"] == [
        "algorithm_risk_halt_state",
        "engine_version",
        "estimated_slippage_usd",
        "rejected_order_count",
    ]


def test_result_extraction_is_deterministic_and_round_trips(
    tmp_path: Path, protocol_bundle: ProtocolBundle
) -> None:
    first = extract_result_json(FIXTURE, tmp_path / "first.json", bundle=protocol_bundle)
    second = extract_result_json(FIXTURE, tmp_path / "second.json", bundle=protocol_bundle)

    assert first.read_bytes() == second.read_bytes()
    assert load_result_observation(first, bundle=protocol_bundle) == parse_result_json(
        FIXTURE, bundle=protocol_bundle
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["state"].__setitem__("Name", "Measured Black Zebra"),
        lambda value: value["algorithmConfiguration"].__setitem__("name", "Measured Black Zebra"),
        lambda value: value["algorithmConfiguration"]["parameters"].pop("fold-id"),
        lambda value: value["algorithmConfiguration"]["parameters"].__setitem__(
            "fold-id", "spy-2020"
        ),
        lambda value: value["algorithmConfiguration"]["parameters"].pop("optimization-mode"),
        lambda value: value["algorithmConfiguration"]["parameters"].__setitem__(
            "optimization-mode", "true"
        ),
        lambda value: value["algorithmConfiguration"]["parameters"].__setitem__(
            "unsupported", "value"
        ),
        lambda value: value["algorithmConfiguration"].__setitem__(
            "startDate", "2021-01-02T00:00:00Z"
        ),
        lambda value: value["algorithmConfiguration"].__setitem__(
            "endDate", "2021-12-30T00:00:00Z"
        ),
        lambda value: value["algorithmConfiguration"].__setitem__(
            "startDate", "2020-01-01T00:00:00Z"
        ),
        lambda value: value["state"].__setitem__("RuntimeError", "initialization failed"),
        lambda value: value["state"].__setitem__("StackTrace", "initialize:275"),
        lambda value: value["state"].__setitem__("Status", "Running"),
        lambda value: value["state"].__setitem__(
            "ProjectName", "Strategies/WalkForwardMovingAverageV1 1"
        ),
    ],
)
def test_result_rejects_wrong_stale_failed_or_unsupported_identity(
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
    mutate: Any,
) -> None:
    payload = _result()
    mutate(payload)
    path = _write_result(tmp_path / "invalid.json", payload)

    with pytest.raises(WalkForwardResultError):
        parse_result_json(path, bundle=protocol_bundle)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.pop("charts"),
        lambda value: value["charts"]["Benchmark"]["series"]["Benchmark"].__setitem__(
            "values", [{"x": 1609718400, "y": 100}]
        ),
        lambda value: value["charts"]["Benchmark"]["series"].__setitem__(
            "Other", {"values": [{"x": 1609718400, "y": 100}]}
        ),
        lambda value: value["orders"]["1"].__setitem__("symbol", {"value": "QQQ"}),
        lambda value: value["orders"]["1"].__setitem__("price", "not-a-number"),
        lambda value: value["orders"]["1"].__setitem__("value", 999),
        lambda value: value["orderEvents"][0]["orderFee"]["value"].__setitem__("currency", "EUR"),
        lambda value: value["totalPerformance"]["portfolioStatistics"].__setitem__(
            "endEquity", "109000"
        ),
    ],
)
def test_result_rejects_missing_benchmark_non_spy_malformed_order_or_contradiction(
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
    mutate: Any,
) -> None:
    payload = _result()
    mutate(payload)
    path = _write_result(tmp_path / "invalid.json", payload)

    with pytest.raises(WalkForwardResultError):
        parse_result_json(path, bundle=protocol_bundle)


def test_result_rejects_final_short_position(
    tmp_path: Path, protocol_bundle: ProtocolBundle
) -> None:
    payload = _result()
    payload["orders"]["1"]["quantity"] = -10
    payload["orders"]["1"]["value"] = -1000
    payload["orderEvents"][0]["fillQuantity"] = -10

    with pytest.raises(WalkForwardResultError, match="short"):
        parse_result_json(_write_result(tmp_path / "short.json", payload), bundle=protocol_bundle)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (
            lambda value: value["algorithmConfiguration"].__setitem__(
                "outOfSampleMaxEndDate", "not-an-iso-timestamp"
            ),
            "metadata timestamp is invalid",
        ),
        (
            lambda value: value["algorithmConfiguration"].__setitem__(
                "outOfSampleMaxEndDate", "2021-12-31T00:00:00+01:00"
            ),
            "metadata timestamp must be UTC",
        ),
        (
            lambda value: value["algorithmConfiguration"].__setitem__("outOfSampleDays", 1),
            "out-of-sample days must be zero",
        ),
    ],
)
def test_result_rejects_invalid_oos_metadata(
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
    mutate: Any,
    message: str,
) -> None:
    payload = _download_result()
    mutate(payload)

    with pytest.raises(WalkForwardResultError, match=message):
        parse_result_json(
            _write_result(tmp_path / "invalid-oos.json", payload), bundle=protocol_bundle
        )


@pytest.mark.parametrize(
    "point",
    [
        [1609718400, 100, 1],
        {"x": 1609718400, "y": 100, "extra": 1},
        ["1609718400", 100],
        [1609718400, "100"],
        None,
    ],
)
def test_result_rejects_invalid_official_benchmark_point_shapes(
    tmp_path: Path, protocol_bundle: ProtocolBundle, point: object
) -> None:
    payload = _download_result()
    payload["charts"]["Benchmark"]["series"]["Benchmark"]["values"][0] = point

    with pytest.raises(WalkForwardResultError, match="Benchmark"):
        parse_result_json(
            _write_result(tmp_path / "invalid-benchmark.json", payload), bundle=protocol_bundle
        )


def test_completed_orders_require_fill_time_and_reconcile_counts(
    tmp_path: Path, protocol_bundle: ProtocolBundle
) -> None:
    missing_time = _download_result()
    missing_time["orders"]["1"].pop("lastFillTime")
    with pytest.raises(WalkForwardResultError, match="lastFillTime"):
        parse_result_json(
            _write_result(tmp_path / "missing-fill-time.json", missing_time),
            bundle=protocol_bundle,
        )

    malformed_time = _download_result()
    malformed_time["orders"]["1"]["lastFillTime"] = "not-an-iso-timestamp"
    with pytest.raises(WalkForwardResultError, match="ISO timestamp"):
        parse_result_json(
            _write_result(tmp_path / "malformed-fill-time.json", malformed_time),
            bundle=protocol_bundle,
        )

    missing_state_count = _download_result()
    missing_state_count["state"].pop("OrderCount")
    with pytest.raises(WalkForwardResultError, match="required without order events"):
        parse_result_json(
            _write_result(tmp_path / "missing-state-count.json", missing_state_count),
            bundle=protocol_bundle,
        )

    wrong_state_count = _download_result()
    wrong_state_count["state"]["OrderCount"] = 3
    with pytest.raises(WalkForwardResultError, match="state order count"):
        parse_result_json(
            _write_result(tmp_path / "wrong-state-count.json", wrong_state_count),
            bundle=protocol_bundle,
        )

    wrong_statistics_count = _download_result()
    wrong_statistics_count["statistics"]["Total Orders"] = "3"
    with pytest.raises(WalkForwardResultError, match="reported order count"):
        parse_result_json(
            _write_result(tmp_path / "wrong-statistics-count.json", wrong_statistics_count),
            bundle=protocol_bundle,
        )


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("status", 5, "must all be filled"),
        ("price", 0, "positive fill price"),
    ],
)
def test_completed_orders_require_filled_status_and_positive_price(
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
    field: str,
    value: object,
    message: str,
) -> None:
    payload = _download_result()
    payload["orders"]["1"][field] = value
    if field == "price":
        payload["orders"]["1"]["value"] = 0

    with pytest.raises(WalkForwardResultError, match=message):
        parse_result_json(
            _write_result(tmp_path / f"invalid-completed-order-{field}.json", payload),
            bundle=protocol_bundle,
        )


def test_completed_orders_reject_short_intermediate_position(
    tmp_path: Path, protocol_bundle: ProtocolBundle
) -> None:
    payload = _download_result()
    payload["orders"]["1"]["quantity"] = -5
    payload["orders"]["1"]["value"] = -500
    payload["orders"]["2"]["quantity"] = 10
    payload["orders"]["2"]["value"] = 1100

    with pytest.raises(WalkForwardResultError, match="short"):
        parse_result_json(
            _write_result(tmp_path / "intermediate-short.json", payload), bundle=protocol_bundle
        )


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("statistics", "$3.00", "statistics.Total Fees"),
        ("runtimeStatistics", "-$3.00", "runtime fees"),
    ],
)
def test_completed_orders_reconcile_authoritative_aggregate_fees(
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
    field: str,
    value: str,
    message: str,
) -> None:
    payload = _download_result()
    if field == "statistics":
        payload[field]["Total Fees"] = value
    else:
        payload[field]["Fees"] = value

    with pytest.raises(WalkForwardResultError, match=message):
        parse_result_json(
            _write_result(tmp_path / f"wrong-{field}-fees.json", payload),
            bundle=protocol_bundle,
        )


def test_cli_exposes_only_specific_safe_result_validation_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _download_result()
    private_marker = "synthetic-private-marker-must-not-appear"
    payload["privateExternalValue"] = private_marker
    payload["algorithmConfiguration"]["outOfSampleMaxEndDate"] = "invalid"
    source = _write_result(tmp_path / "invalid.json", payload)

    result = walk_forward_operator.main(["validate", "--input-result", str(source)])

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err == (
        "Error: QuantConnect result artifact validation failed: "
        "out-of-sample metadata timestamp is invalid\n"
    )
    assert private_marker not in captured.err
    assert str(source) not in captured.err


def test_result_rejects_non_finite_json_number(
    tmp_path: Path, protocol_bundle: ProtocolBundle
) -> None:
    raw = FIXTURE.read_text(encoding="utf-8").replace('"endEquity": "110000"', '"endEquity": NaN')
    path = tmp_path / "non-finite.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(WalkForwardResultError, match="non-finite"):
        parse_result_json(path, bundle=protocol_bundle)


def test_raw_private_metadata_is_discarded_and_normalized_leakage_is_rejected(
    tmp_path: Path, protocol_bundle: ProtocolBundle
) -> None:
    payload = _result()
    payload.update(
        {
            "backtestId": "synthetic-private-id",
            "organizationId": "synthetic-organization",
            "projectId": 123,
            "url": "https://private.invalid/result",
        }
    )
    normalized = parse_result_json(
        _write_result(tmp_path / "private-input.json", payload), bundle=protocol_bundle
    )
    serialized = deterministic_json(normalized)
    assert "synthetic-private" not in serialized
    assert "private.invalid" not in serialized

    tampered = copy.deepcopy(normalized)
    tampered["project_id"] = 123
    with pytest.raises(WalkForwardResultError, match="private identity"):
        normalize_result_observation(tampered, bundle=protocol_bundle)


def test_result_input_is_bounded_regular_utf8_and_not_a_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
) -> None:
    monkeypatch.setattr(walk_forward_result, "_MAX_INPUT_RESULT_BYTES", 32)
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"{" + b"x" * 32 + b"}")
    with pytest.raises(WalkForwardResultError, match="byte limit"):
        parse_result_json(oversized, bundle=protocol_bundle)

    bad_utf8 = tmp_path / "bad-utf8.json"
    bad_utf8.write_bytes(b"{\xff}")
    with pytest.raises(WalkForwardResultError, match="UTF-8"):
        parse_result_json(bad_utf8, bundle=protocol_bundle)

    source = tmp_path / "source.json"
    source.write_bytes(FIXTURE.read_bytes())
    linked = tmp_path / "linked.json"
    try:
        linked.symlink_to(source)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(WalkForwardResultError, match="symlink"):
        parse_result_json(linked, bundle=protocol_bundle)


def test_result_input_rejects_directory_and_fifo(
    tmp_path: Path, protocol_bundle: ProtocolBundle
) -> None:
    directory = tmp_path / "result-directory"
    directory.mkdir()
    with pytest.raises(WalkForwardResultError, match="regular file"):
        parse_result_json(directory, bundle=protocol_bundle)

    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation is unavailable")
    fifo = tmp_path / "result-fifo"
    os.mkfifo(fifo)
    with pytest.raises(WalkForwardResultError, match="regular file"):
        parse_result_json(fifo, bundle=protocol_bundle)


def test_result_output_rejects_unsafe_paths(
    tmp_path: Path, protocol_bundle: ProtocolBundle
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")

    with pytest.raises(WalkForwardResultError, match="symlink"):
        extract_result_json(FIXTURE, linked / "output.json", bundle=protocol_bundle)


def test_exact_five_result_aggregation_is_deterministic_and_rejects_duplicates(
    tmp_path: Path, protocol_bundle: ProtocolBundle
) -> None:
    observations = _normalized_five(tmp_path, protocol_bundle)
    aggregate = aggregate_result_observations(observations, bundle=protocol_bundle)

    assert [item["fold_id"] for item in aggregate["fold_results"]] == list(FOLD_IDS)
    assert aggregate["source_formats"] == [RESULT_SOURCE_FORMAT]
    assert aggregate["summary"]["completed_fold_count"] == 5
    assert aggregate["summary"]["total_orders"] == 10
    assert aggregate["summary"]["total_fees_usd"] == "10"
    assert normalize_result_aggregate(aggregate, bundle=protocol_bundle) == aggregate

    duplicate = observations[:-1] + [copy.deepcopy(observations[0])]
    with pytest.raises(WalkForwardResultError, match="duplicate"):
        aggregate_result_observations(duplicate, bundle=protocol_bundle)


def test_result_aggregation_rejects_mixed_source_types(
    tmp_path: Path, protocol_bundle: ProtocolBundle
) -> None:
    observations = _normalized_five(tmp_path, protocol_bundle)
    observations[0]["source_format"] = "canonical_algorithm_log"

    with pytest.raises(WalkForwardResultError, match="source format"):
        aggregate_result_observations(observations, bundle=protocol_bundle)

    with pytest.raises(WalkForwardObservationError):
        from trading_bot_lab.walk_forward.observation import aggregate_observations

        aggregate_observations(observations, bundle=protocol_bundle)


def test_result_file_aggregation_and_evidence_round_trip(
    tmp_path: Path, protocol_bundle: ProtocolBundle
) -> None:
    paths: list[Path] = []
    for index, fold_id in enumerate(FOLD_IDS):
        raw = _write_result(tmp_path / f"{fold_id}-raw.json", _fold_result(index))
        normalized = extract_result_json(raw, tmp_path / f"{fold_id}.json", bundle=protocol_bundle)
        paths.append(normalized)
    expected = aggregate_result_files(paths, bundle=protocol_bundle)
    output = write_result_aggregate_files(
        paths, tmp_path / "aggregate.json", bundle=protocol_bundle
    )

    assert load_result_aggregate(output, bundle=protocol_bundle) == expected
    with pytest.raises(WalkForwardResultError, match="must differ"):
        write_result_aggregate_files(paths, paths[0], bundle=protocol_bundle)


def test_result_schemas_are_closed_canonical_and_validate_normalized_records(
    tmp_path: Path, protocol_bundle: ProtocolBundle
) -> None:
    observation = parse_result_json(FIXTURE, bundle=protocol_bundle)
    aggregate = aggregate_result_observations(
        _normalized_five(tmp_path, protocol_bundle), bundle=protocol_bundle
    )
    observation_schema = json.loads(RESULT_OBSERVATION_SCHEMA_PATH.read_text(encoding="utf-8"))
    aggregate_schema = json.loads(RESULT_AGGREGATE_SCHEMA_PATH.read_text(encoding="utf-8"))

    assert RESULT_OBSERVATION_SCHEMA_PATH.read_bytes() == deterministic_json(
        observation_schema
    ).encode("utf-8")
    assert RESULT_AGGREGATE_SCHEMA_PATH.read_bytes() == deterministic_json(aggregate_schema).encode(
        "utf-8"
    )
    assert observation_schema["additionalProperties"] is False
    assert aggregate_schema["additionalProperties"] is False
    assert normalize_result_observation(observation, bundle=protocol_bundle) == observation
    assert normalize_result_aggregate(aggregate, bundle=protocol_bundle) == aggregate


def test_operator_extract_result_and_aggregate_result_are_offline_and_confined(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
) -> None:
    report_root = tmp_path / "reports" / "walk-forward" / "v1"
    monkeypatch.setattr(walk_forward_operator, "REPORT_DIRECTORY", report_root)
    output = report_root / "spy-2021.json"
    result = run_phase(
        build_parser().parse_args(
            ["extract-result", "--input-result", str(FIXTURE), "--output", str(output)]
        ),
        bundle=protocol_bundle,
    )
    assert result == "Wrote normalized QuantConnect result observation.\n"
    assert output.is_file()
    assert not phase_is_read_only("extract-result")
    assert not phase_is_read_only("aggregate-result")
    assert phase_is_read_only("evidence-result")

    protected = ROOT / "contracts" / "walk-forward" / "v1" / "protocol.json"
    args = build_parser().parse_args(
        ["extract-result", "--input-result", str(FIXTURE), "--output", str(protected)]
    )
    with pytest.raises(ValueError, match="ignored walk-forward report"):
        run_phase(args, bundle=protocol_bundle)


def test_future_cloud_plan_uses_private_placeholder_two_parameters_and_no_push() -> None:
    commands = build_cloud_command_plan()

    assert len(commands) == 5
    assert tuple(command.fold_id for command in commands) == FOLD_IDS
    for command, fold_id in zip(commands, FOLD_IDS, strict=True):
        assert command.argv == (
            "lean",
            "cloud",
            "backtest",
            PROJECT_REFERENCE,
            "--name",
            f"wf-v1-{fold_id}",
            "--parameter",
            "fold-id",
            fold_id,
            "--parameter",
            "optimization-mode",
            "false",
        )
        assert command.argv.count("--parameter") == 2
        assert "--push" not in command.argv
        assert command.render().startswith('lean cloud backtest "$LEAN_WALK_FORWARD_PROJECT_ID"')

    printed = run_phase(build_parser().parse_args(["print-cloud-commands"]))
    assert printed.splitlines() == [command.render() for command in commands]
    assert "--push" not in printed
    assert printed.count("--parameter optimization-mode false") == 5
    assert printed.count("--parameter fold-id") == 5


def test_operator_source_has_zero_cloud_execution_surface() -> None:
    source = (ROOT / "src" / "trading_bot_lab" / "walk_forward" / "operator.py").read_text(
        encoding="utf-8"
    )
    lowered = source.casefold()

    assert "subprocess" not in lowered
    assert 'cloud_commands_executed": 0' in source
    assert 'network_activity": "none' in source
    for forbidden in ("lean cloud backtest ", "lean optimize", "lean live", "object store"):
        assert forbidden not in lowered
