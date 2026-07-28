from __future__ import annotations

import copy
import json
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from trading_bot_lab.parity import build_local_parity_trace
from trading_bot_lab.parity.compare import (
    ParityValidationError,
    validate_parity_candidate_trace,
)
from trading_bot_lab.parity.contract import deterministic_json
from trading_bot_lab.parity.lean import (
    LEAN_ENGINE_NAME,
    LEAN_OBSERVATION_PREFIX,
    MAX_LEAN_OBSERVATION_PAYLOAD_BYTES,
    LeanParityObservationError,
    compact_lean_parity_payload,
    extract_lean_parity_observation,
    parse_lean_parity_log,
)


@pytest.fixture(scope="module")
def lean_trace() -> dict[str, Any]:
    trace = copy.deepcopy(build_local_parity_trace())
    trace["provenance"] = "lean_engine_observation"
    trace["engine"] = {
        "name": LEAN_ENGINE_NAME,
        "version": "2.5.0.0.17942",
    }
    return trace


def _payload(trace: dict[str, Any]) -> bytes:
    return compact_lean_parity_payload(trace).encode("utf-8")


def _write_log(path: Path, payload: bytes) -> Path:
    path.write_bytes(
        b"ordinary LEAN output\n"
        + LEAN_OBSERVATION_PREFIX.encode("ascii")
        + payload
        + b"\r\nordinary trailing output\n"
    )
    return path


def test_extracts_one_observation_and_never_copies_surrounding_logs(
    tmp_path: Path,
    lean_trace: dict[str, Any],
) -> None:
    payload = _payload(lean_trace)
    assert b"\n" not in payload
    assert len(payload) <= MAX_LEAN_OBSERVATION_PAYLOAD_BYTES
    source = _write_log(tmp_path / "lean.log", payload)

    first = extract_lean_parity_observation(source, tmp_path / "first.json")
    second = extract_lean_parity_observation(source, tmp_path / "second.json")

    assert parse_lean_parity_log(source) == lean_trace
    assert first.read_text(encoding="utf-8") == deterministic_json(lean_trace)
    assert first.read_bytes() == second.read_bytes()
    assert "ordinary LEAN output" not in first.read_text(encoding="utf-8")


def test_extracts_one_bounded_line_wrapped_observation(
    tmp_path: Path,
    lean_trace: dict[str, Any],
) -> None:
    payload = _payload(lean_trace)
    wrapped = b"\n".join(payload[offset : offset + 79] for offset in range(0, len(payload), 79))
    source = tmp_path / "wrapped.log"
    source.write_bytes(
        b"ordinary LEAN output\n"
        + LEAN_OBSERVATION_PREFIX.encode("ascii")
        + wrapped
        + b"\nordinary trailing output\n"
    )

    output = extract_lean_parity_observation(source, tmp_path / "wrapped.json")

    assert parse_lean_parity_log(source) == lean_trace
    assert output.read_text(encoding="utf-8") == deterministic_json(lean_trace)


def test_rejects_wrapped_payload_over_the_total_size_limit(tmp_path: Path) -> None:
    oversized = b"{" + b"x" * MAX_LEAN_OBSERVATION_PAYLOAD_BYTES
    wrapped = b"\n".join(oversized[offset : offset + 79] for offset in range(0, len(oversized), 79))
    source = tmp_path / "wrapped-oversized.log"
    source.write_bytes(LEAN_OBSERVATION_PREFIX.encode("ascii") + wrapped + b"\n")

    with pytest.raises(LeanParityObservationError, match="size limit"):
        parse_lean_parity_log(source)


def test_public_candidate_validator_reuses_the_v1_contract(
    lean_trace: dict[str, Any],
) -> None:
    validate_parity_candidate_trace(lean_trace)
    missing = copy.deepcopy(lean_trace)
    del missing["fills"][0]["fee"]

    with pytest.raises(ParityValidationError, match="missing=.*fee"):
        validate_parity_candidate_trace(missing)


@pytest.mark.parametrize("line_count", [0, 2])
def test_requires_exactly_one_prefixed_line(
    tmp_path: Path,
    lean_trace: dict[str, Any],
    line_count: int,
) -> None:
    observation = LEAN_OBSERVATION_PREFIX.encode("ascii") + _payload(lean_trace) + b"\n"
    source = tmp_path / "lean.log"
    source.write_bytes(b"human output\n" + observation * line_count)

    with pytest.raises(LeanParityObservationError, match="exactly one"):
        parse_lean_parity_log(source)


def test_prefix_must_start_the_line(tmp_path: Path, lean_trace: dict[str, Any]) -> None:
    source = tmp_path / "lean.log"
    source.write_bytes(b"decorated " + LEAN_OBSERVATION_PREFIX.encode() + _payload(lean_trace))

    with pytest.raises(LeanParityObservationError, match="exactly one"):
        parse_lean_parity_log(source)


def test_rejects_oversized_or_empty_payload(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.log"
    oversized.write_bytes(
        LEAN_OBSERVATION_PREFIX.encode("ascii")
        + b"0" * (MAX_LEAN_OBSERVATION_PAYLOAD_BYTES + 1)
        + b"\n"
    )
    empty = tmp_path / "empty.log"
    empty.write_text(LEAN_OBSERVATION_PREFIX + "\n", encoding="utf-8")

    with pytest.raises(LeanParityObservationError, match="size limit"):
        parse_lean_parity_log(oversized)
    with pytest.raises(LeanParityObservationError, match="empty"):
        parse_lean_parity_log(empty)


def test_log_reader_uses_bounded_chunks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    lean_trace: dict[str, Any],
) -> None:
    prefix = LEAN_OBSERVATION_PREFIX.encode("ascii")
    data = (
        b"x" * (MAX_LEAN_OBSERVATION_PAYLOAD_BYTES * 3)
        + b"\n"
        + prefix
        + _payload(lean_trace)
        + b"\n"
    )

    class BoundedReader(BytesIO):
        def __init__(self, initial_bytes: bytes) -> None:
            super().__init__(initial_bytes)
            self.read_limits: list[int] = []

        def __iter__(self) -> Any:
            raise AssertionError("log parsing must not use unbounded line iteration")

        def readline(self, size: int = -1) -> bytes:
            assert 0 < size <= len(prefix) + MAX_LEAN_OBSERVATION_PAYLOAD_BYTES + 3
            self.read_limits.append(size)
            return super().readline(size)

    source = tmp_path / "bounded.log"
    reader = BoundedReader(data)
    original_open = Path.open

    def guarded_open(path: Path, *args: object, **kwargs: object) -> Any:
        if path == source:
            return reader
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    assert parse_lean_parity_log(source) == lean_trace
    assert len(reader.read_limits) >= 4


def test_rejects_noncanonical_or_invalid_utf8(
    tmp_path: Path,
    lean_trace: dict[str, Any],
) -> None:
    noncanonical = json.dumps(lean_trace, sort_keys=True).encode("utf-8")
    invalid_utf8 = _payload(lean_trace)[:-1] + b"\xff}"

    with pytest.raises(LeanParityObservationError, match="canonical compact"):
        parse_lean_parity_log(_write_log(tmp_path / "spaced.log", noncanonical))
    with pytest.raises(LeanParityObservationError, match="UTF-8"):
        parse_lean_parity_log(_write_log(tmp_path / "utf8.log", invalid_utf8))


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_rejects_every_non_finite_json_spelling(
    tmp_path: Path,
    lean_trace: dict[str, Any],
    constant: str,
) -> None:
    payload = compact_lean_parity_payload(lean_trace)
    payload = payload.replace('"bar_count":8', f'"bar_count":{constant}', 1)

    with pytest.raises(LeanParityObservationError, match="non-finite"):
        parse_lean_parity_log(_write_log(tmp_path / f"non-finite-{constant}.log", payload.encode()))


def test_rejects_duplicate_keys_and_malformed_json(
    tmp_path: Path,
    lean_trace: dict[str, Any],
) -> None:
    payload = compact_lean_parity_payload(lean_trace)
    field = '"schema_version":"1.0.0"'
    duplicate = payload.replace(field, f"{field},{field}", 1).encode()

    with pytest.raises(LeanParityObservationError, match="duplicate JSON key"):
        parse_lean_parity_log(_write_log(tmp_path / "duplicate.log", duplicate))
    with pytest.raises(LeanParityObservationError, match="malformed JSON"):
        parse_lean_parity_log(_write_log(tmp_path / "malformed.log", b"{"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "lean"),
        ("name", "/home/operator/lean"),
        ("name", "https://example.invalid/lean"),
        ("name", "operator@example.invalid"),
        ("name", "account_id=12345"),
        ("version", "runtime-version"),
        ("version", "2.5.0.0.17942/host"),
    ],
)
def test_requires_safe_quantconnect_lean_engine_identity(
    tmp_path: Path,
    lean_trace: dict[str, Any],
    field: str,
    value: str,
) -> None:
    candidate = copy.deepcopy(lean_trace)
    candidate["engine"][field] = value

    with pytest.raises(LeanParityObservationError):
        parse_lean_parity_log(_write_log(tmp_path / f"bad-{field}.log", _payload(candidate)))


@pytest.mark.parametrize(
    "private_key",
    [
        "account_id",
        "backtest_id",
        "billing_id",
        "invoice_id",
        "module_license",
        "node_id",
        "owner_name",
        "subscription_id",
    ],
)
def test_rejects_private_metadata_hidden_in_extensible_fields(
    tmp_path: Path,
    lean_trace: dict[str, Any],
    private_key: str,
) -> None:
    candidate = copy.deepcopy(lean_trace)
    candidate["risk_decisions"][0]["metrics"][private_key] = "1"

    with pytest.raises(LeanParityObservationError, match="private metadata"):
        parse_lean_parity_log(_write_log(tmp_path / "private.log", _payload(candidate)))


def test_requires_actual_lean_provenance_and_complete_v1_trace(
    tmp_path: Path,
    lean_trace: dict[str, Any],
) -> None:
    fixture = copy.deepcopy(lean_trace)
    fixture["provenance"] = "contract_fixture_not_engine_observation"
    missing = copy.deepcopy(lean_trace)
    del missing["final_bar"]

    with pytest.raises(LeanParityObservationError, match="provenance"):
        parse_lean_parity_log(_write_log(tmp_path / "fixture.log", _payload(fixture)))
    with pytest.raises(LeanParityObservationError, match="valid parity v1"):
        parse_lean_parity_log(_write_log(tmp_path / "missing.log", _payload(missing)))


def test_refuses_to_replace_the_input_log(
    tmp_path: Path,
    lean_trace: dict[str, Any],
) -> None:
    source = _write_log(tmp_path / "lean.log", _payload(lean_trace))

    with pytest.raises(LeanParityObservationError, match="different files"):
        extract_lean_parity_observation(source, source)
