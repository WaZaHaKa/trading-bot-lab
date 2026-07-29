"""Print-only cloud plan and offline walk-forward operator phases."""

from __future__ import annotations

import argparse
import shlex
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from trading_bot_lab.parity.contract import deterministic_json
from trading_bot_lab.walk_forward.contract import (
    FOLD_IDS,
    PROJECT_DIRECTORY,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    REPOSITORY_ROOT,
    ProtocolBundle,
    WalkForwardContractError,
    load_protocol_bundle,
)
from trading_bot_lab.walk_forward.observation import (
    WalkForwardObservationError,
    extract_observation,
    load_aggregate_record,
    load_observation,
    parse_observation_log,
    write_aggregate_files,
)
from trading_bot_lab.walk_forward.result_json import (
    WalkForwardResultError,
    extract_result_json,
    load_result_aggregate,
    load_result_observation,
    parse_result_json,
    write_result_aggregate_files,
)

PROJECT_NAME = "Strategies/WalkForwardMovingAverageV1"
PROJECT_REFERENCE = "$LEAN_WALK_FORWARD_PROJECT_ID"
REPORT_DIRECTORY = REPOSITORY_ROOT / "reports" / "walk-forward" / "v1"
DEFAULT_AGGREGATE_PATH = REPORT_DIRECTORY / "aggregate.json"
DEFAULT_RESULT_AGGREGATE_PATH = REPORT_DIRECTORY / "result-aggregate.json"

_LEAN_COMMAND = "lean"
_CLOUD_GROUP = "cloud"
_BACKTEST_ACTION = "backtest"
_PHASES = (
    "plan",
    "validate",
    "print-cloud-commands",
    "extract",
    "extract-result",
    "aggregate",
    "aggregate-result",
    "evidence",
    "evidence-result",
)
_READ_ONLY_PHASES = frozenset(
    {"plan", "validate", "print-cloud-commands", "evidence", "evidence-result"}
)


class WalkForwardOperatorError(ValueError):
    """Raised when an offline operator phase is incomplete or unsafe."""


@dataclass(frozen=True)
class CloudBacktestCommand:
    """One immutable, display-only future cloud backtest command."""

    fold_id: str
    backtest_name: str
    argv: tuple[str, ...]

    def render(self) -> str:
        rendered: list[str] = []
        for value in self.argv:
            if value == PROJECT_REFERENCE:
                rendered.append(f'"{PROJECT_REFERENCE}"')
            else:
                rendered.append(shlex.quote(value))
        return " ".join(rendered)

    def as_dict(self) -> dict[str, object]:
        return {
            "argv": list(self.argv),
            "backtest_name": self.backtest_name,
            "fold_id": self.fold_id,
            "project": PROJECT_REFERENCE,
        }


def build_cloud_command_plan() -> tuple[CloudBacktestCommand, ...]:
    """Return exactly five closed commands without executing a process."""

    commands: list[CloudBacktestCommand] = []
    for fold_id in FOLD_IDS:
        name = f"wf-v1-{fold_id}"
        argv = (
            _LEAN_COMMAND,
            _CLOUD_GROUP,
            _BACKTEST_ACTION,
            PROJECT_REFERENCE,
            "--name",
            name,
            "--parameter",
            "fold-id",
            fold_id,
            "--parameter",
            "optimization-mode",
            "false",
        )
        commands.append(CloudBacktestCommand(fold_id=fold_id, backtest_name=name, argv=argv))
    return tuple(commands)


def phase_is_read_only(phase: str) -> bool:
    """Return whether a supported phase is contractually non-mutating."""

    if phase not in _PHASES:
        raise WalkForwardOperatorError("unknown walk-forward phase")
    return phase in _READ_ONLY_PHASES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Plan and process fixed walk-forward v1 evidence offline. "
            "No phase executes a cloud command."
        )
    )
    parser.add_argument("phase", nargs="?", choices=_PHASES, default="plan")
    parser.add_argument("--input-log", type=Path)
    parser.add_argument("--input-result", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--observation", type=Path, action="append", default=[])
    parser.add_argument("--result-observation", type=Path, action="append", default=[])
    parser.add_argument("--aggregate-record", type=Path)
    return parser


def run_phase(args: argparse.Namespace, *, bundle: ProtocolBundle | None = None) -> str:
    """Run one local phase and return deterministic user-facing text."""

    selected_bundle = bundle or load_protocol_bundle()
    phase = str(args.phase)
    if phase == "plan":
        return deterministic_json(_plan_payload(selected_bundle))
    if phase == "validate":
        validated = _validate_inputs(args, selected_bundle)
        return deterministic_json(
            {
                "cloud_commands_executed": 0,
                "network_activity": "none",
                "protocol_name": PROTOCOL_NAME,
                "protocol_version": PROTOCOL_VERSION,
                "validated_artifacts": validated,
            }
        )
    if phase == "print-cloud-commands":
        return "\n".join(command.render() for command in build_cloud_command_plan()) + "\n"
    if phase == "extract":
        if args.input_log is None:
            raise WalkForwardOperatorError("extract requires --input-log")
        output = _report_output_path(args.output) if args.output is not None else None
        observation = parse_observation_log(args.input_log, bundle=selected_bundle)
        if output is None:
            output = _report_output_path(REPORT_DIRECTORY / f"{observation['fold_id']}.json")
        extract_observation(args.input_log, output, bundle=selected_bundle)
        return "Wrote normalized walk-forward observation.\n"
    if phase == "extract-result":
        if args.input_result is None:
            raise WalkForwardOperatorError("extract-result requires --input-result")
        output = _report_output_path(args.output) if args.output is not None else None
        observation = parse_result_json(args.input_result, bundle=selected_bundle)
        if output is None:
            output = _report_output_path(REPORT_DIRECTORY / f"{observation['fold_id']}.json")
        extract_result_json(args.input_result, output, bundle=selected_bundle)
        return "Wrote normalized QuantConnect result observation.\n"
    if phase == "aggregate":
        output = _report_output_path(args.output or DEFAULT_AGGREGATE_PATH)
        paths = _observation_paths(args.observation)
        write_aggregate_files(paths, output, bundle=selected_bundle)
        return "Wrote walk-forward aggregate.\n"
    if phase == "aggregate-result":
        output = _report_output_path(args.output or DEFAULT_RESULT_AGGREGATE_PATH)
        paths = _result_observation_paths(args.result_observation or args.observation)
        write_result_aggregate_files(paths, output, bundle=selected_bundle)
        return "Wrote QuantConnect result aggregate.\n"
    if phase == "evidence":
        path = args.aggregate_record or args.output or DEFAULT_AGGREGATE_PATH
        evidence = load_aggregate_record(path, bundle=selected_bundle)
        return deterministic_json(evidence)
    if phase == "evidence-result":
        path = args.aggregate_record or args.output or DEFAULT_RESULT_AGGREGATE_PATH
        evidence = load_result_aggregate(path, bundle=selected_bundle)
        return deterministic_json(evidence)
    raise WalkForwardOperatorError("unsupported walk-forward phase")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output = run_phase(args)
    except OSError:
        print("Error: local artifact operation failed", file=sys.stderr)
        return 2
    except WalkForwardContractError:
        print("Error: walk-forward protocol validation failed", file=sys.stderr)
        return 2
    except WalkForwardObservationError:
        print("Error: walk-forward artifact validation failed", file=sys.stderr)
        return 2
    except WalkForwardResultError:
        print("Error: QuantConnect result artifact validation failed", file=sys.stderr)
        return 2
    except WalkForwardOperatorError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(output, end="")
    return 0


def _plan_payload(bundle: ProtocolBundle) -> dict[str, object]:
    commands = build_cloud_command_plan()
    return {
        "cloud_backtests_planned": len(commands),
        "cloud_commands_executed": 0,
        "commands_are_print_only": True,
        "default_phase": "plan",
        "default_phase_read_only": True,
        "folds": [fold.as_dict() for fold in bundle.folds],
        "network_activity": "none",
        "optimization_jobs_planned": 0,
        "project": PROJECT_NAME,
        "project_directory": PROJECT_DIRECTORY.relative_to(REPOSITORY_ROOT).as_posix(),
        "protocol_name": PROTOCOL_NAME,
        "protocol_version": PROTOCOL_VERSION,
    }


def _validate_inputs(args: argparse.Namespace, bundle: ProtocolBundle) -> list[str]:
    validated = ["protocol", "project_source", "public_configuration", "schemas"]
    for index, path in enumerate(args.observation, start=1):
        load_observation(path, bundle=bundle)
        validated.append(f"normalized_observation_{index}")
    for index, path in enumerate(args.result_observation, start=1):
        load_result_observation(path, bundle=bundle)
        validated.append(f"normalized_result_observation_{index}")
    if args.input_log is not None:
        parse_observation_log(args.input_log, bundle=bundle)
        validated.append("raw_observation_log")
    if args.input_result is not None:
        parse_result_json(args.input_result, bundle=bundle)
        validated.append("quantconnect_result_json")
    if args.aggregate_record is not None:
        load_aggregate_record(args.aggregate_record, bundle=bundle)
        validated.append("aggregate_record")
    return validated


def _observation_paths(values: Sequence[Path]) -> tuple[Path, ...]:
    if values:
        if len(values) != len(FOLD_IDS):
            raise WalkForwardOperatorError("aggregate requires exactly five --observation paths")
        return tuple(values)
    return tuple(REPORT_DIRECTORY / f"{fold_id}.json" for fold_id in FOLD_IDS)


def _result_observation_paths(values: Sequence[Path]) -> tuple[Path, ...]:
    if values:
        if len(values) != len(FOLD_IDS):
            raise WalkForwardOperatorError(
                "aggregate-result requires exactly five result observation paths"
            )
        return tuple(values)
    return tuple(REPORT_DIRECTORY / f"{fold_id}.json" for fold_id in FOLD_IDS)


def _report_output_path(path: Path) -> Path:
    try:
        report_root = REPORT_DIRECTORY.resolve(strict=False)
        candidate = path.resolve(strict=False)
        candidate.relative_to(report_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise WalkForwardOperatorError(
            "output must remain inside the ignored walk-forward report directory"
        ) from exc
    return path


__all__ = [
    "DEFAULT_AGGREGATE_PATH",
    "DEFAULT_RESULT_AGGREGATE_PATH",
    "PROJECT_NAME",
    "PROJECT_REFERENCE",
    "REPORT_DIRECTORY",
    "CloudBacktestCommand",
    "WalkForwardOperatorError",
    "build_cloud_command_plan",
    "build_parser",
    "main",
    "phase_is_read_only",
    "run_phase",
]
