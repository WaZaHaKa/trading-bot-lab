"""Operate the one authorized local identical-data LEAN parity workflow.

The default phase is a read-only preflight. Every phase that creates files,
pulls an image, or launches LEAN requires an exact public authorization phrase.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from trading_bot_lab.artifacts import atomic_write_text
from trading_bot_lab.lean_runtime import (
    COMPARE_AUTHORIZATION,
    EXPECTED_CLI_VERSION,
    EXPECTED_PLATFORM,
    MAX_EXECUTIONS,
    MUTABLE_DISCOVERY_IMAGE,
    OCI_INDEX_DIGEST,
    PINNED_IMAGE,
    PLATFORM_MANIFEST_DIGEST,
    PREPARE_AUTHORIZATION,
    PULL_AUTHORIZATION,
    ROOTLESS_HOST,
    RUN_AUTHORIZATION,
    RUNTIME_CONTAINER_LABEL,
    RUNTIME_CONTAINER_LABEL_VALUE,
    RUNTIME_CONTAINER_NAME,
    ExclusiveRunLock,
    LeanRuntimeError,
    assert_ignored_path,
    build_cli_general_config,
    build_extra_docker_config,
    build_isolated_environment,
    build_minimal_lean_config,
    cleanup_runtime_directory,
    increment_execution,
    increment_pull,
    initialize_runtime_directory,
    load_runtime_state,
    parse_registry_identity,
    require_authorization,
    validate_cli_version,
    validate_docker_info,
    validate_linux_host,
    validate_local_image,
    validate_rootless_socket,
    validate_runtime_audit,
    verify_exact_lf_bytes,
    write_private_json,
    write_runtime_state,
)
from trading_bot_lab.parity import (
    COMPARISON_DIMENSIONS,
    DEFAULT_SCENARIO_PATH,
    ParityMismatchError,
    compare_parity_files,
)

ROOT = Path(__file__).resolve().parents[1]
LEAN_EXECUTABLE = ROOT / ".venv" / "bin" / "lean"
PYTHON_EXECUTABLE = ROOT / ".venv" / "bin" / "python"
PROJECT_SOURCE = ROOT / "lean-workspace" / "Strategies" / "ParityFixtureV1"
SOURCE_FIXTURE = ROOT / "tests" / "fixtures" / "parity" / "v1" / "synthetic_weekdays.csv"
STAGED_FIXTURE = (
    ROOT / "lean-workspace" / "data" / "custom" / "parity" / "v1" / "synthetic_weekdays.csv"
)
DATA_DIRECTORY = ROOT / "lean-workspace" / "data"
REPORT_DIRECTORY = ROOT / "reports" / "parity"
LOG_DIRECTORY = ROOT / "logs" / "parity"
RUNTIME_DIRECTORY = LOG_DIRECTORY / "runtime-v1"
STATE_PATH = LOG_DIRECTORY / "runtime-state.json"
LOCK_PATH = LOG_DIRECTORY / "runtime-v1.lock"
LOCAL_TRACE = REPORT_DIRECTORY / "local-v1.json"
LEAN_TRACE = REPORT_DIRECTORY / "lean-v1.json"
COMPARISON_PATH = REPORT_DIRECTORY / "comparison-v1.json"
IMAGE_RECORD_PATH = REPORT_DIRECTORY / "image-v1.json"
RUNTIME_AUDIT_PATH = REPORT_DIRECTORY / "runtime-audit-v1.json"
PULL_LOG_PATH = LOG_DIRECTORY / "pull-v1.log"
BACKTEST_DIRECTORY = PROJECT_SOURCE / "backtests"

PUBLIC_PROJECT_CONFIG = {
    "algorithm-language": "Python",
    "description": (
        "Backtest-only synthetic PARITY v1 identical-data observation project. live mode forbidden."
    ),
    "parameters": {
        "data-transport": "local-file",
        "object-store-key": "",
    },
}
_MUTATING_PHASES = {"pull", "prepare", "run", "compare", "all"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the pinned, rootless, offline local LEAN parity contract."
    )
    parser.add_argument(
        "phase",
        nargs="?",
        choices=("preflight", "pull", "prepare", "run", "compare", "all"),
        default="preflight",
    )
    parser.add_argument("--pull-authorization")
    parser.add_argument("--prepare-authorization")
    parser.add_argument("--run-authorization")
    parser.add_argument("--compare-authorization")
    return parser


def phase_is_read_only(phase: str) -> bool:
    """Return whether a phase is contractually read-only."""

    if phase not in {"preflight", *_MUTATING_PHASES}:
        raise LeanRuntimeError("unknown local LEAN parity phase")
    return phase == "preflight"


def _run(
    command: Sequence[str],
    *,
    cwd: Path = ROOT,
    environment: Mapping[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    selected_environment = None if environment is None else dict(environment)
    return subprocess.run(
        list(command),
        cwd=cwd,
        env=selected_environment,
        check=check,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _docker_command(*arguments: str) -> tuple[str, ...]:
    return ("docker", "--host", ROOTLESS_HOST, *arguments)


def _docker_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("DOCKER_CONTEXT", None)
    environment["DOCKER_HOST"] = ROOTLESS_HOST
    return environment


def _load_json_output(result: subprocess.CompletedProcess[str], *, label: str) -> object:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise LeanRuntimeError(f"{label} returned malformed JSON") from exc


def _validate_repository_state() -> None:
    top_level = _run(("git", "rev-parse", "--show-toplevel")).stdout.strip()
    if Path(top_level).resolve() != ROOT.resolve():
        raise LeanRuntimeError("repository top level differs from the authorized checkout")
    branch = _run(("git", "branch", "--show-current")).stdout.strip()
    if not branch.startswith("chore/pin-lean-parity-runtime"):
        raise LeanRuntimeError("local LEAN parity must run from its dedicated follow-up branch")
    tracked = _run(("git", "status", "--porcelain=v1", "--untracked-files=no")).stdout
    if tracked:
        raise LeanRuntimeError("tracked working tree or index is not clean")


def _validate_project_source() -> None:
    if not PROJECT_SOURCE.joinpath("main.py").is_file():
        raise LeanRuntimeError("ParityFixtureV1 source is missing")
    try:
        config = json.loads(PROJECT_SOURCE.joinpath("config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LeanRuntimeError("ParityFixtureV1 public configuration is malformed") from exc
    if config != PUBLIC_PROJECT_CONFIG:
        raise LeanRuntimeError("ParityFixtureV1 config contains non-public or altered fields")
    if ";" in str(config["description"]):
        raise LeanRuntimeError("ParityFixtureV1 description must remain semicolon-free")


def _assert_git_ignored(path: Path) -> None:
    assert_ignored_path(ROOT, path)
    relative = path.resolve().relative_to(ROOT.resolve()).as_posix()
    result = _run(
        ("git", "check-ignore", "--no-index", "--quiet", "--", relative),
        check=False,
    )
    if result.returncode != 0:
        raise LeanRuntimeError("generated parity path is not ignored by Git")


def _validate_generated_paths() -> None:
    for path in (
        STAGED_FIXTURE,
        LOCAL_TRACE,
        LEAN_TRACE,
        COMPARISON_PATH,
        IMAGE_RECORD_PATH,
        RUNTIME_AUDIT_PATH,
        PULL_LOG_PATH,
        RUNTIME_DIRECTORY,
        STATE_PATH,
        LOCK_PATH,
        BACKTEST_DIRECTORY / "parity-v1-run-1",
    ):
        _assert_git_ignored(path)


def _docker_identity() -> None:
    validate_rootless_socket()
    result = _run(
        _docker_command("info", "--format", "{{json .}}"),
        environment=_docker_environment(),
    )
    payload = _load_json_output(result, label="rootless Docker info")
    if not isinstance(payload, dict):
        raise LeanRuntimeError("rootless Docker info must be a JSON object")
    identity = validate_docker_info(payload, user_home=Path.home())
    expected_root = Path.home() / ".local" / "share" / "docker"
    if identity.docker_root.resolve() != expected_root.resolve():
        raise LeanRuntimeError("rootless Docker root differs from the authorized user root")


def _assert_container_absent() -> None:
    result = _run(
        _docker_command(
            "container",
            "ls",
            "--all",
            "--filter",
            f"name=^{RUNTIME_CONTAINER_NAME}$",
            "--format",
            "{{.Names}}",
        ),
        environment=_docker_environment(),
    )
    names = [line for line in result.stdout.splitlines() if line]
    if names:
        raise LeanRuntimeError("authorized parity container name is already in use")


def perform_preflight() -> None:
    """Perform the complete read-only host, repository, and daemon preflight."""

    validate_linux_host(system=platform.system(), machine=platform.machine())
    if not hasattr(os, "getuid") or os.getuid() != 1001:
        raise LeanRuntimeError("local LEAN parity requires the authorized unprivileged user")
    _validate_repository_state()
    if not LEAN_EXECUTABLE.is_file() or not PYTHON_EXECUTABLE.is_file():
        raise LeanRuntimeError("repository LEAN or Python executable is missing")
    validate_cli_version()
    if validate_cli_version() != EXPECTED_CLI_VERSION:
        raise LeanRuntimeError("installed LEAN CLI version differs from the pinned contract")
    verify_exact_lf_bytes(SOURCE_FIXTURE)
    _validate_project_source()
    _validate_generated_paths()
    _docker_identity()
    _assert_container_absent()


def _write_public_json(path: Path, payload: Mapping[str, object]) -> None:
    _assert_git_ignored(path)
    serialized = json.dumps(payload, allow_nan=False, indent=2, sort_keys=True) + "\n"
    atomic_write_text(path, serialized)
    path.chmod(0o600)


def _inspect_local_image() -> dict[str, object]:
    result = _run(
        _docker_command("image", "inspect", PINNED_IMAGE),
        environment=_docker_environment(),
    )
    identity = validate_local_image(_load_json_output(result, label="image inspection"))
    return {
        "architecture": identity.architecture,
        "completion_status": "verified",
        "created": identity.created,
        "image_id": identity.image_id,
        "oci_index_digest": OCI_INDEX_DIGEST,
        "os": identity.operating_system,
        "platform": EXPECTED_PLATFORM,
        "platform_manifest_digest": PLATFORM_MANIFEST_DIGEST,
        "repo_digest": identity.repo_digest,
        "size_bytes": identity.size_bytes,
    }


def pull_image(authorization: str | None) -> None:
    """Perform the one authorized immutable rootless image pull."""

    require_authorization(authorization, PULL_AUTHORIZATION, action="image pull")
    perform_preflight()
    with ExclusiveRunLock(LOCK_PATH):
        state = load_runtime_state(STATE_PATH)
        if state.pulls:
            raise LeanRuntimeError("the one authorized image pull was already consumed")
        registry = _run(
            _docker_command(
                "buildx",
                "imagetools",
                "inspect",
                MUTABLE_DISCOVERY_IMAGE,
            ),
            environment=_docker_environment(),
        )
        parse_registry_identity(registry.stdout)
        LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
        _assert_git_ignored(PULL_LOG_PATH)
        next_state = increment_pull(state)
        write_runtime_state(STATE_PATH, next_state)
        with PULL_LOG_PATH.open("w", encoding="utf-8", newline="\n") as handle:
            result = subprocess.run(
                _docker_command(
                    "pull",
                    "--platform",
                    EXPECTED_PLATFORM,
                    PINNED_IMAGE,
                ),
                cwd=ROOT,
                env=_docker_environment(),
                check=False,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=1800,
            )
        if result.returncode != 0:
            raise LeanRuntimeError("authorized immutable image pull failed")
        _write_public_json(IMAGE_RECORD_PATH, _inspect_local_image())
    print("Immutable LEAN image pull: verified")


def _run_script(arguments: Sequence[str]) -> None:
    result = _run((str(PYTHON_EXECUTABLE), *arguments), check=False)
    if result.returncode != 0:
        raise LeanRuntimeError("offline parity helper failed")


def prepare_inputs(authorization: str | None) -> None:
    """Stage exact fixture bytes and export the deterministic local oracle."""

    require_authorization(authorization, PREPARE_AUTHORIZATION, action="input preparation")
    perform_preflight()
    REPORT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    _run_script(("scripts/prepare_lean_parity_data.py",))
    verify_exact_lf_bytes(STAGED_FIXTURE)
    _run_script(
        (
            "scripts/export_local_parity.py",
            "--scenario",
            str(DEFAULT_SCENARIO_PATH),
            "--output",
            str(LOCAL_TRACE),
        )
    )
    print("Fixture and local oracle: verified")


def _write_runtime_files(runtime: Path) -> tuple[Path, Path, Path, Path]:
    home = runtime / "home"
    cli_root = runtime / "cli"
    project = cli_root / "Strategies" / "ParityFixtureV1"
    temporary = runtime / "tmp"
    shim = runtime / "shim"
    for path in (home / ".lean", project, temporary, shim):
        path.mkdir(parents=True, mode=0o700)
    write_private_json(home / ".lean" / "config", build_cli_general_config())
    lean_config_path = cli_root / "lean.json"
    write_private_json(lean_config_path, build_minimal_lean_config(DATA_DIRECTORY))
    shutil.copy2(PROJECT_SOURCE / "main.py", project / "main.py")
    shutil.copy2(PROJECT_SOURCE / "config.json", project / "config.json")
    sitecustomize = (
        "from trading_bot_lab.lean_runtime import install_lean_cli_runtime_guards\n"
        "install_lean_cli_runtime_guards()\n"
    )
    atomic_write_text(shim / "sitecustomize.py", sitecustomize)
    (shim / "sitecustomize.py").chmod(0o600)
    return home, cli_root, temporary, shim


def _assert_network_blocked(environment: Mapping[str, str]) -> None:
    for url in ("http://example.com", "https://example.com"):
        probe = subprocess.run(
            (
                str(PYTHON_EXECUTABLE),
                "-c",
                (f"import urllib.request;urllib.request.urlopen({url!r}, timeout=2).read(1)"),
            ),
            cwd=ROOT,
            env=dict(environment),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if probe.returncode == 0:
            raise LeanRuntimeError("isolated host environment unexpectedly reached HTTP(S)")
    docker_probe = subprocess.run(
        (
            str(PYTHON_EXECUTABLE),
            "-c",
            (
                "import docker;"
                "from pathlib import Path;"
                "from trading_bot_lab.lean_runtime import validate_docker_info;"
                "client=docker.from_env();"
                "validate_docker_info(client.info(), user_home=Path("
                "__import__('os').environ['TRADING_BOT_LAB_PARITY_DOCKER_ROOT_PARENT']));"
                "print('rootless-docker-ok')"
            ),
        ),
        cwd=ROOT,
        env=dict(environment),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if docker_probe.returncode != 0 or docker_probe.stdout.strip() != "rootless-docker-ok":
        raise LeanRuntimeError("isolated host environment cannot reach rootless Docker")


def _remove_owned_stopped_container() -> None:
    inspect = _run(
        _docker_command("container", "inspect", RUNTIME_CONTAINER_NAME),
        environment=_docker_environment(),
        check=False,
    )
    if inspect.returncode != 0:
        return
    payload = _load_json_output(inspect, label="parity container inspection")
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise LeanRuntimeError("parity container inspection is malformed")
    container = payload[0]
    config = container.get("Config")
    state = container.get("State")
    if not isinstance(config, dict) or not isinstance(state, dict):
        raise LeanRuntimeError("parity container inspection is incomplete")
    labels = config.get("Labels")
    if not isinstance(labels, dict) or labels.get(RUNTIME_CONTAINER_LABEL) != (
        RUNTIME_CONTAINER_LABEL_VALUE
    ):
        raise LeanRuntimeError("refusing to remove a container not owned by this runtime")
    if state.get("Running") is not False:
        raise LeanRuntimeError("owned parity container remains running; narrow cleanup stopped")
    removal = _run(
        _docker_command("container", "rm", RUNTIME_CONTAINER_NAME),
        environment=_docker_environment(),
        check=False,
    )
    if removal.returncode != 0:
        raise LeanRuntimeError("failed to remove the exact stopped parity container")


def _copy_runtime_output(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    if destination.exists():
        raise LeanRuntimeError("refusing to overwrite an earlier raw LEAN result")
    _assert_git_ignored(destination)
    shutil.copytree(source, destination)


def run_lean(authorization: str | None) -> int:
    """Launch one bounded genuine LEAN execution and extract one observation."""

    require_authorization(authorization, RUN_AUTHORIZATION, action="LEAN execution")
    perform_preflight()
    _inspect_local_image()
    verify_exact_lf_bytes(STAGED_FIXTURE)
    if not LOCAL_TRACE.is_file():
        raise LeanRuntimeError("local oracle trace must be exported before LEAN execution")
    with ExclusiveRunLock(LOCK_PATH):
        state = load_runtime_state(STATE_PATH)
        if state.pulls != 1 or not IMAGE_RECORD_PATH.is_file():
            raise LeanRuntimeError("verified authorized image pull must precede LEAN execution")
        next_state = increment_execution(state)
        LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
        REPORT_DIRECTORY.mkdir(parents=True, exist_ok=True)
        write_runtime_state(STATE_PATH, next_state)
        run_number = next_state.executions
        raw_log = LOG_DIRECTORY / f"lean-v1-run-{run_number}.log"
        preserved_output = BACKTEST_DIRECTORY / f"parity-v1-run-{run_number}"
        _assert_git_ignored(raw_log)
        _assert_git_ignored(preserved_output)
        initialize_runtime_directory(RUNTIME_DIRECTORY)
        runtime_output = RUNTIME_DIRECTORY / "cli" / "backtest-output"
        home, cli_root, temporary, shim = _write_runtime_files(RUNTIME_DIRECTORY)
        environment = build_isolated_environment(
            os.environ,
            home=home,
            temporary_directory=temporary,
            shim_directory=shim,
            source_directory=ROOT / "src",
            audit_path=RUNTIME_AUDIT_PATH,
            docker_root_parent=Path.home(),
            allowed_mount_roots=(RUNTIME_DIRECTORY, DATA_DIRECTORY),
        )
        _assert_network_blocked(environment)
        if (home / ".lean" / "credentials").exists():
            raise LeanRuntimeError("temporary HOME unexpectedly contains LEAN credentials")
        command = (
            str(LEAN_EXECUTABLE),
            "backtest",
            "Strategies/ParityFixtureV1",
            "--output",
            str(runtime_output),
            "--image",
            PINNED_IMAGE,
            "--no-update",
            "--lean-config",
            str(cli_root / "lean.json"),
            "--extra-docker-config",
            json.dumps(build_extra_docker_config(), separators=(",", ":"), sort_keys=True),
        )
        run_error: Exception | None = None
        result: subprocess.CompletedProcess[str] | None = None
        try:
            with raw_log.open("w", encoding="utf-8", newline="\n") as handle:
                result = subprocess.run(
                    command,
                    cwd=cli_root,
                    env=environment,
                    check=False,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=900,
                )
        except Exception as exc:
            run_error = exc
        finally:
            _copy_runtime_output(runtime_output, preserved_output)
            _remove_owned_stopped_container()
            if (home / ".lean" / "credentials").exists():
                raise LeanRuntimeError("LEAN created credentials in the isolated HOME")
            cleanup_runtime_directory(RUNTIME_DIRECTORY)
        if run_error is not None:
            raise LeanRuntimeError("genuine LEAN execution did not complete") from run_error
        if result is None or result.returncode != 0:
            raise LeanRuntimeError("genuine LEAN execution returned a failure")
        try:
            audit = json.loads(RUNTIME_AUDIT_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LeanRuntimeError("runtime isolation audit is missing or malformed") from exc
        if not isinstance(audit, dict):
            raise LeanRuntimeError("runtime isolation audit must be a JSON object")
        validate_runtime_audit(audit)
        _run_script(
            (
                "scripts/extract_lean_parity.py",
                "--input-log",
                str(raw_log),
                "--output",
                str(LEAN_TRACE),
                "--scenario",
                str(DEFAULT_SCENARIO_PATH),
            )
        )
    print(f"Genuine LEAN execution: completed ({run_number}/{MAX_EXECUTIONS})")
    return run_number


def compare_outputs(authorization: str | None) -> bool:
    """Compare all 16 dimensions and persist one ignored deterministic result."""

    require_authorization(
        authorization,
        COMPARE_AUTHORIZATION,
        action="parity comparison",
    )
    if not LOCAL_TRACE.is_file() or not LEAN_TRACE.is_file():
        raise LeanRuntimeError("both normalized observations are required for comparison")
    try:
        comparison = compare_parity_files(
            LOCAL_TRACE,
            LEAN_TRACE,
            scenario_path=DEFAULT_SCENARIO_PATH,
        )
    except ParityMismatchError as exc:
        dimensions = {
            dimension: ("failed" if dimension in exc.differences_by_dimension else "passed")
            for dimension in COMPARISON_DIMENSIONS
        }
        payload: dict[str, object] = {
            "differences": {
                dimension: list(values)
                for dimension, values in sorted(exc.differences_by_dimension.items())
            },
            "dimensions": dimensions,
            "matched": False,
            "scenario_id": "weekday_ma_next_open_v1",
        }
        _write_public_json(COMPARISON_PATH, payload)
        print("Parity comparison: failed")
        return False
    _write_public_json(COMPARISON_PATH, comparison.as_dict())
    print("Parity comparison: passed")
    return True


def execute(args: argparse.Namespace) -> int:
    phase = str(args.phase)
    if phase == "preflight":
        perform_preflight()
        print("Local LEAN parity preflight: passed (read-only)")
        return 0
    if phase == "pull":
        pull_image(args.pull_authorization)
        return 0
    if phase == "prepare":
        prepare_inputs(args.prepare_authorization)
        return 0
    if phase == "run":
        run_lean(args.run_authorization)
        return 0
    if phase == "compare":
        return 0 if compare_outputs(args.compare_authorization) else 2
    if phase == "all":
        pull_image(args.pull_authorization)
        prepare_inputs(args.prepare_authorization)
        run_lean(args.run_authorization)
        return 0 if compare_outputs(args.compare_authorization) else 2
    raise LeanRuntimeError("unknown local LEAN parity phase")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return execute(args)
    except (
        LeanRuntimeError,
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
        ValueError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
