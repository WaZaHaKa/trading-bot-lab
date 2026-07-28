"""Compare an existing LEAN trace with an existing local-oracle trace offline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from trading_bot_lab.parity import (
    DEFAULT_SCENARIO_PATH,
    ParityMismatchError,
    ParityValidationError,
    compare_parity_files,
)
from trading_bot_lab.parity.contract import ParityContractError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare normalized parity traces without invoking LEAN or a network."
    )
    parser.add_argument("--local-trace", type=Path, required=True)
    parser.add_argument("--lean-trace", type=Path, required=True)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        comparison = compare_parity_files(
            args.local_trace,
            args.lean_trace,
            scenario_path=args.scenario,
        )
    except (OSError, ParityContractError, ParityValidationError, ParityMismatchError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(comparison.as_dict(), indent=2, sort_keys=True))
    print("Network activity: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
