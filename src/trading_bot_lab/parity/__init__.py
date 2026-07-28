"""Versioned, offline cross-engine parity helpers."""

from trading_bot_lab.parity.compare import (
    ParityComparison,
    ParityMismatchError,
    ParityValidationError,
    compare_parity_files,
    compare_parity_traces,
)
from trading_bot_lab.parity.contract import (
    CONTRACT_VERSION,
    DEFAULT_SCENARIO_PATH,
    TRACE_SCHEMA_VERSION,
)
from trading_bot_lab.parity.local import build_local_parity_trace, write_local_parity_trace

__all__ = [
    "CONTRACT_VERSION",
    "DEFAULT_SCENARIO_PATH",
    "TRACE_SCHEMA_VERSION",
    "ParityComparison",
    "ParityMismatchError",
    "ParityValidationError",
    "build_local_parity_trace",
    "compare_parity_files",
    "compare_parity_traces",
    "write_local_parity_trace",
]
