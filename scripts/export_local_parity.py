"""Export a deterministic local-oracle trace for the v1 parity contract."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from trading_bot_lab.parity import DEFAULT_SCENARIO_PATH, write_local_parity_trace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the offline local Python oracle and export a parity trace."
    )
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO_PATH)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output = write_local_parity_trace(args.output, args.scenario)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote local parity trace: {output}")
    print("Provenance: local_python_oracle_observation")
    print("Network activity: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
