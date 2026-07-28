"""Strictly extract one normalized LEAN parity observation from an existing log."""

from __future__ import annotations

import json
import re
from math import isfinite
from pathlib import Path
from typing import Any, BinaryIO

from trading_bot_lab.artifacts import atomic_write_text
from trading_bot_lab.parity.compare import (
    ParityValidationError,
    validate_parity_candidate_trace,
)
from trading_bot_lab.parity.contract import DEFAULT_SCENARIO_PATH, deterministic_json

LEAN_OBSERVATION_PREFIX = "TRADING_BOT_LAB_LEAN_PARITY_V1:"
LEAN_ENGINE_NAME = "quantconnect_lean"
MAX_LEAN_OBSERVATION_PAYLOAD_BYTES = 65_536

_PREFIX_BYTES = LEAN_OBSERVATION_PREFIX.encode("ascii")
_LEAN_VERSION_PATTERN = re.compile(r"[0-9]{1,9}(?:\.[0-9]{1,9}){1,5}")
_PRIVATE_METADATA_TOKENS = {
    "account",
    "accountid",
    "accountmetadata",
    "accountname",
    "accountnumber",
    "apikey",
    "backtestid",
    "billing",
    "cloudid",
    "credential",
    "email",
    "invoice",
    "localid",
    "modulelicense",
    "nodeid",
    "organizationid",
    "owner",
    "projectid",
    "secret",
    "subscription",
    "token",
    "url",
}


_MAX_LEAN_LOG_LINE_BYTES = len(_PREFIX_BYTES) + MAX_LEAN_OBSERVATION_PAYLOAD_BYTES + 2


class LeanParityObservationError(ValueError):
    """Raised when a LEAN log does not contain one safe canonical observation."""


def parse_lean_parity_log(
    input_log: str | Path,
    *,
    scenario_path: str | Path = DEFAULT_SCENARIO_PATH,
) -> dict[str, Any]:
    """Parse and validate exactly one prefixed parity observation from a log."""

    payload = _read_single_payload(Path(input_log))
    trace = _decode_canonical_payload(payload)
    try:
        validate_parity_candidate_trace(trace, scenario_path=scenario_path)
    except ParityValidationError as exc:
        raise LeanParityObservationError(
            f"LEAN observation is not a valid parity v1 candidate: {exc}"
        ) from exc
    if trace["provenance"] != "lean_engine_observation":
        raise LeanParityObservationError(
            "LEAN observation provenance must be lean_engine_observation"
        )
    _validate_lean_engine(trace["engine"])
    _reject_private_metadata(trace)
    return trace


def extract_lean_parity_observation(
    input_log: str | Path,
    output_path: str | Path,
    *,
    scenario_path: str | Path = DEFAULT_SCENARIO_PATH,
) -> Path:
    """Write only the validated trace as deterministic pretty canonical JSON."""

    source = Path(input_log)
    destination = Path(output_path)
    if source.resolve() == destination.resolve():
        raise LeanParityObservationError("input log and output trace must be different files")
    trace = parse_lean_parity_log(source, scenario_path=scenario_path)
    return atomic_write_text(destination, deterministic_json(trace))


def compact_lean_parity_payload(trace: dict[str, Any]) -> str:
    """Return the one accepted compact JSON representation for a trace payload."""

    return json.dumps(
        trace,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _read_single_payload(input_log: Path) -> bytes:
    selected: bytes | None = None
    with input_log.open("rb") as handle:
        while True:
            raw_line = handle.readline(_MAX_LEAN_LOG_LINE_BYTES + 1)
            if not raw_line:
                break
            if len(raw_line) > _MAX_LEAN_LOG_LINE_BYTES:
                prefixed = raw_line.startswith(_PREFIX_BYTES)
                while raw_line and not raw_line.endswith(b"\n"):
                    raw_line = handle.readline(_MAX_LEAN_LOG_LINE_BYTES + 1)
                if prefixed:
                    raise LeanParityObservationError(
                        "LEAN parity observation payload exceeds the fixed size limit"
                    )
                continue
            if not raw_line.startswith(_PREFIX_BYTES):
                continue
            if selected is not None:
                raise LeanParityObservationError(
                    "input log must contain exactly one prefixed LEAN parity observation"
                )
            line = _strip_line_ending(raw_line)
            selected = line[len(_PREFIX_BYTES) :]
            if not selected:
                raise LeanParityObservationError("LEAN parity observation payload is empty")
            selected = _read_wrapped_payload(selected, handle)
    if selected is None:
        raise LeanParityObservationError(
            "input log must contain exactly one prefixed LEAN parity observation"
        )
    return selected


def _read_wrapped_payload(initial: bytes, handle: BinaryIO) -> bytes:
    payload = bytearray(initial)
    if len(payload) > MAX_LEAN_OBSERVATION_PAYLOAD_BYTES:
        raise LeanParityObservationError(
            "LEAN parity observation payload exceeds the fixed size limit"
        )
    while not _json_object_is_complete(payload):
        raw_line = handle.readline(_MAX_LEAN_LOG_LINE_BYTES + 1)
        if not raw_line:
            break
        payload.extend(_strip_line_ending(raw_line))
        if len(payload) > MAX_LEAN_OBSERVATION_PAYLOAD_BYTES:
            raise LeanParityObservationError(
                "LEAN parity observation payload exceeds the fixed size limit"
            )
    return bytes(payload)


def _strip_line_ending(line: bytes) -> bytes:
    if line.endswith(b"\n"):
        line = line[:-1]
    if line.endswith(b"\r"):
        line = line[:-1]
    return line


def _json_object_is_complete(payload: bytes | bytearray) -> bool:
    if not payload or payload[0] != ord("{"):
        return True
    depth = 0
    in_string = False
    escaped = False
    for byte in payload:
        if in_string:
            if escaped:
                escaped = False
            elif byte == ord("\\"):
                escaped = True
            elif byte == ord('"'):
                in_string = False
            continue
        if byte == ord('"'):
            in_string = True
        elif byte in (ord("{"), ord("[")):
            depth += 1
        elif byte in (ord("}"), ord("]")):
            depth -= 1
            if depth <= 0:
                return True
    return False


def _decode_canonical_payload(payload: bytes) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LeanParityObservationError(
            "LEAN parity observation payload must be valid UTF-8"
        ) from exc

    def reject_constant(value: str) -> None:
        raise LeanParityObservationError(
            f"LEAN parity observation contains non-finite JSON value {value}"
        )

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise LeanParityObservationError(
                    "LEAN parity observation contains a duplicate JSON key"
                )
            result[key] = value
        return result

    try:
        decoded = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise LeanParityObservationError(
            "LEAN parity observation payload is malformed JSON"
        ) from exc
    except (OverflowError, RecursionError, ValueError) as exc:
        if isinstance(exc, LeanParityObservationError):
            raise
        raise LeanParityObservationError(
            "LEAN parity observation payload contains an invalid JSON value"
        ) from exc
    if not isinstance(decoded, dict):
        raise LeanParityObservationError("LEAN parity observation must be a JSON object")
    _reject_non_finite(decoded)
    try:
        canonical = compact_lean_parity_payload(decoded)
    except (RecursionError, ValueError) as exc:
        raise LeanParityObservationError(
            "LEAN parity observation payload contains an invalid JSON value"
        ) from exc
    if canonical != text:
        raise LeanParityObservationError(
            "LEAN parity observation payload must use canonical compact JSON"
        )
    return decoded


def _reject_non_finite(value: object) -> None:
    if isinstance(value, float) and not isfinite(value):
        raise LeanParityObservationError("LEAN parity observation contains a non-finite number")
    if isinstance(value, dict):
        for key, nested in value.items():
            _reject_non_finite(key)
            _reject_non_finite(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_non_finite(nested)


def _validate_lean_engine(engine: object) -> None:
    if not isinstance(engine, dict) or engine.get("name") != LEAN_ENGINE_NAME:
        raise LeanParityObservationError(f"LEAN observation engine.name must be {LEAN_ENGINE_NAME}")
    version = engine.get("version")
    if not isinstance(version, str) or _LEAN_VERSION_PATTERN.fullmatch(version) is None:
        raise LeanParityObservationError(
            "LEAN observation engine.version must be a safe dotted numeric version"
        )


def _reject_private_metadata(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if _contains_private_metadata(key):
                raise LeanParityObservationError(
                    "LEAN observation contains forbidden private metadata"
                )
            _reject_private_metadata(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_private_metadata(nested)
    elif isinstance(value, str) and _contains_private_metadata(value):
        raise LeanParityObservationError("LEAN observation contains forbidden private metadata")


def _contains_private_metadata(value: str) -> bool:
    if "/" in value or "\\" in value or "@" in value:
        return True
    compact = re.sub(r"[^a-z0-9]+", "", value.casefold())
    return any(token in compact for token in _PRIVATE_METADATA_TOKENS)


__all__ = [
    "LEAN_ENGINE_NAME",
    "LEAN_OBSERVATION_PREFIX",
    "MAX_LEAN_OBSERVATION_PAYLOAD_BYTES",
    "LeanParityObservationError",
    "compact_lean_parity_payload",
    "extract_lean_parity_observation",
    "parse_lean_parity_log",
]
