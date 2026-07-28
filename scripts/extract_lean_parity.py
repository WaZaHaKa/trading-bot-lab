"""Extract one strict normalized LEAN parity observation from an existing log."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from trading_bot_lab.parity import DEFAULT_SCENARIO_PATH
from trading_bot_lab.parity.lean import (
    LeanParityObservationError,
    extract_lean_parity_observation,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract one canonical parity trace without invoking LEAN or a network."
    )
    parser.add_argument("--input-log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output = extract_lean_parity_observation(
            args.input_log,
            args.output,
            scenario_path=args.scenario,
        )
    except (OSError, LeanParityObservationError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote normalized LEAN parity observation: {output}")
    print("Prefixed observations consumed: 1")
    print("Surrounding log lines copied: 0")
    print("Network activity: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
