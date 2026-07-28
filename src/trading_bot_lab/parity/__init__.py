"""Versioned, offline cross-engine parity helpers."""

from trading_bot_lab.parity.compare import (
    COMPARISON_DIMENSIONS,
    ParityComparison,
    ParityMismatchError,
    ParityValidationError,
    compare_parity_files,
    compare_parity_traces,
    validate_parity_candidate_trace,
)
from trading_bot_lab.parity.contract import (
    CONTRACT_VERSION,
    DEFAULT_SCENARIO_PATH,
    TRACE_SCHEMA_VERSION,
)
from trading_bot_lab.parity.lean import (
    LEAN_ENGINE_NAME,
    LEAN_OBSERVATION_PREFIX,
    MAX_LEAN_OBSERVATION_PAYLOAD_BYTES,
    LeanParityObservationError,
    compact_lean_parity_payload,
    extract_lean_parity_observation,
    parse_lean_parity_log,
)
from trading_bot_lab.parity.local import build_local_parity_trace, write_local_parity_trace

__all__ = [
    "COMPARISON_DIMENSIONS",
    "CONTRACT_VERSION",
    "DEFAULT_SCENARIO_PATH",
    "LEAN_ENGINE_NAME",
    "LEAN_OBSERVATION_PREFIX",
    "MAX_LEAN_OBSERVATION_PAYLOAD_BYTES",
    "TRACE_SCHEMA_VERSION",
    "LeanParityObservationError",
    "ParityComparison",
    "ParityMismatchError",
    "ParityValidationError",
    "build_local_parity_trace",
    "compact_lean_parity_payload",
    "compare_parity_files",
    "compare_parity_traces",
    "extract_lean_parity_observation",
    "parse_lean_parity_log",
    "validate_parity_candidate_trace",
    "write_local_parity_trace",
]
