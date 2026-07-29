from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

REQUIRED_FILES = [
    ".gitattributes",
    "AGENTS.md",
    "README.md",
    ".env.example",
    "docs/risk-policy.md",
    "docs/risk-policy-mapping.md",
    "docs/architecture.md",
    "docs/lean-getting-started.md",
    "docs/lean-integration.md",
    "docs/windows-lean-setup.md",
    "docs/qcc-guardrails.md",
    "docs/execution-timing-comparison.md",
    "docs/cross-engine-parity.md",
    "docs/known-limitations.md",
    "docs/lean-paused.md",
    "docs/local-backtesting.md",
    "docs/report-schemas.md",
    "docs/paper-replay.md",
    "docs/adr/0004-shared-event-simulation-core.md",
    "docs/adr/0005-execution-timing-and-accounting-invariants.md",
    "docs/adr/0006-content-bound-replay-and-atomic-reports.md",
    "docs/adr/0007-lean-cloud-primary-local-oracle.md",
    "docs/adr/0008-public-repository-policy.md",
    "config/risk.example.yaml",
    "src/trading_bot_lab/risk/policy.py",
    "src/trading_bot_lab/domain.py",
    "src/trading_bot_lab/cli.py",
    "src/trading_bot_lab/paper.py",
    "src/trading_bot_lab/observability.py",
    "src/trading_bot_lab/artifacts.py",
    "src/trading_bot_lab/provenance.py",
    "src/trading_bot_lab/backtesting/engine.py",
    "src/trading_bot_lab/backtesting/csv_data.py",
    "src/trading_bot_lab/backtesting/moving_average.py",
    "src/trading_bot_lab/backtesting/reports.py",
    "tests/test_risk_policy.py",
    "tests/test_market_data.py",
    "tests/test_engine_and_strategy.py",
    "tests/test_reports_paper_cli.py",
    "tests/test_sprint2_contracts.py",
    "tests/test_lean_projects.py",
    "tests/test_lean_parity_data.py",
    "tests/test_cross_engine_parity.py",
    "tests/test_lean_cloud_validation.py",
    "tests/test_lean_parity_observation.py",
    "contracts/parity/v1/README.md",
    "contracts/parity/v1/contract.json",
    "contracts/parity/v1/scenario.schema.json",
    "contracts/parity/v1/trace.schema.json",
    "contracts/lean-cloud-validation/v1/2026-07-28.json",
    "contracts/lean-cloud-validation/v1/record.schema.json",
    "contracts/lean-local-parity/v1/2026-07-28.json",
    "contracts/lean-local-parity/v1/2026-07-28-open-phase-rerun-1.json",
    "contracts/lean-local-parity/v1/record.schema.json",
    "contracts/walk-forward/v1/README.md",
    "contracts/walk-forward/v1/protocol.json",
    "contracts/walk-forward/v1/protocol.schema.json",
    "contracts/walk-forward/v1/observation.schema.json",
    "contracts/walk-forward/v1/aggregate-record.schema.json",
    "tests/test_lean_local_parity_evidence.py",
    "src/trading_bot_lab/parity/__init__.py",
    "src/trading_bot_lab/parity/contract.py",
    "src/trading_bot_lab/parity/local.py",
    "src/trading_bot_lab/parity/compare.py",
    "src/trading_bot_lab/parity/lean.py",
    "src/trading_bot_lab/lean_validation.py",
    "src/trading_bot_lab/walk_forward/__init__.py",
    "src/trading_bot_lab/walk_forward/contract.py",
    "src/trading_bot_lab/walk_forward/observation.py",
    "src/trading_bot_lab/walk_forward/operator.py",
    "data/local/.gitkeep",
    "data/sample/README.md",
    "data/sample/synthetic_spy_daily.csv",
    "reports/.gitkeep",
    "scripts/run_local_backtest.py",
    "scripts/run_paper_replay.py",
    "scripts/run_historical_paper_replay.py",
    "scripts/export_local_parity.py",
    "scripts/prepare_lean_parity_data.py",
    "scripts/compare_lean_parity.py",
    "scripts/extract_lean_parity.py",
    "scripts/run_walk_forward_v1.py",
    "lean-workspace/README.md",
    "lean-workspace/Strategies/SkeletonBacktest/main.py",
    "lean-workspace/Strategies/SkeletonBacktest/README.md",
    "lean-workspace/Strategies/SkeletonBacktest/config.json",
    "lean-workspace/Strategies/MovingAverageBaseline/main.py",
    "lean-workspace/Strategies/MovingAverageBaseline/README.md",
    "lean-workspace/Strategies/MovingAverageBaseline/config.json",
    "lean-workspace/Strategies/ParityFixtureV1/main.py",
    "lean-workspace/Strategies/ParityFixtureV1/README.md",
    "lean-workspace/Strategies/ParityFixtureV1/config.json",
    "lean-workspace/Strategies/WalkForwardMovingAverageV1/main.py",
    "lean-workspace/Strategies/WalkForwardMovingAverageV1/README.md",
    "lean-workspace/Strategies/WalkForwardMovingAverageV1/config.json",
    "lean/README.md",
    "lean/algorithms/README.md",
    "lean/algorithms/MovingAverageBaseline/README.md",
    "lean/algorithms/MovingAverageBaseline/config.json",
    "lean/algorithms/SkeletonBacktest/main.py",
    "lean/algorithms/SkeletonBacktest/README.md",
    "lean/algorithms/SkeletonBacktest/config.json",
    "lean/algorithms/MovingAverageBaseline/main.py",
    "lean/data/.gitkeep",
    "lean/data/README.md",
    "lean/results/.gitkeep",
    "lean/results/README.md",
]

FORBIDDEN_PATTERNS = [
    "ALLOW_LIVE_TRADING=true",
    "live_trading_enabled: true",
    "allow_live_trading: true",
    "BROKER_API_KEY=sk_",
    "EXCHANGE_API_KEY=sk_",
]

FORBIDDEN_REGEXES = [
    (
        re.compile(r"\ballow_live_trading\s*[:=]\s*true\b", re.IGNORECASE),
        "live trading is enabled",
    ),
    (
        re.compile(r"\blive_trading_enabled\s*[:=]\s*true\b", re.IGNORECASE),
        "live trading feature flag is enabled",
    ),
    (
        re.compile(r"\btrading_mode\s*[:=]\s*live\b", re.IGNORECASE),
        "trading mode is live",
    ),
    (
        re.compile(
            r"\b(?:broker|exchange|openai|alpaca|binance|coinbase|quantconnect|lean)"
            r"[a-z0-9_-]*(?:api[_-]?key|api[_-]?secret|access[_-]?token|api[_-]?token)"
            r"\s*[:=]\s*['\"]?[A-Za-z0-9_\-/+=]{16,}",
            re.IGNORECASE,
        ),
        "possible credential value",
    ),
]

SOURCE_FORBIDDEN_REGEXES = [
    (
        re.compile(
            r"^\s*(?:from|import)\s+"
            r"(?:requests|httpx|aiohttp|urllib3|socket|http\.client|urllib\.request|"
            r"websocket|websockets)\b",
            re.MULTILINE,
        ),
        "network client import in the active local oracle",
    ),
    (
        re.compile(r"\b(?:pickle\.loads?|joblib\.load|eval|exec|os\.system)\s*\("),
        "unsafe deserialization or arbitrary code execution",
    ),
    (
        re.compile(r"shell\s*=\s*True", re.IGNORECASE),
        "shell command execution",
    ),
    (
        re.compile(
            r"(?:[A-Za-z]:[\\/]Users[\\/][^\\/\s]+|/(?:home)/[^/\s]+)",
            re.IGNORECASE,
        ),
        "user-specific absolute path",
    ),
]

EXECUTABLE_LEAN_COMMAND = re.compile(
    r"(?im)(?:^|[;&|]\s*|\brun:\s*)"
    r"[^\r\n]*\blean(?:\.exe)?\s+"
    r"(?:login|live(?:\s|$)|optimize(?:\s|$)|cloud(?:\s|$)|data\s+download\b)"
)

PRIVATE_KEY_HEADER = re.compile(
    r"-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----",
    re.IGNORECASE,
)
TOKEN_SIGNATURES = (
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
)

SENSITIVE_LEAN_KEYS = {
    "user-id",
    "job-user-id",
    "api-token",
    "api-access-token",
    "access-token",
    "auth-token",
    "authorization-token",
    "oauth-token",
    "refresh-token",
    "api-key",
    "api-secret",
    "client-secret",
    "secret-key",
    "password",
    "passphrase",
    "private-key",
    "private-key-path",
    "encryption-key",
    "encryption-key-path",
    "credentials",
}

LEAN_METADATA_KEYS = {"organization-id", "cloud-id", "local-id"}
LEAN_CLOUD_DESCRIPTION_FORBIDDEN_CHARACTERS = (",", ";")

LEAN_GENERATED_PARTS = {
    ".lean",
    "backtest",
    "backtests",
    "cache",
    "data",
    "live",
    "logs",
    "notebooks",
    "object store",
    "optimization",
    "optimizations",
    "results",
    "storage",
    "__pycache__",
}

SKIPPED_SUFFIXES = {
    ".csv",
    ".db",
    ".duckdb",
    ".feather",
    ".h5",
    ".jpeg",
    ".jpg",
    ".joblib",
    ".onnx",
    ".parquet",
    ".pkl",
    ".png",
    ".pt",
    ".pyc",
    ".sqlite",
    ".zip",
}

TRACKED_BINARY_DENY_SUFFIXES = {
    ".db",
    ".duckdb",
    ".feather",
    ".h5",
    ".joblib",
    ".onnx",
    ".parquet",
    ".pkl",
    ".pt",
    ".sqlite",
    ".zip",
}

ALLOWED_DATA = {
    "data/README.md",
    "data/local/.gitkeep",
    "data/sample/README.md",
    "data/sample/synthetic_spy_daily.csv",
    "data/raw/.gitkeep",
    "data/processed/.gitkeep",
}

ALLOWED_ARTIFACTS = {
    "logs/README.md",
    "logs/.gitkeep",
    "reports/README.md",
    "reports/.gitkeep",
}

ALLOWED_TRACKED_CSV = {
    "data/sample/synthetic_spy_daily.csv",
    "tests/fixtures/parity/v1/synthetic_weekdays.csv",
}

EXPECTED_IGNORED_PATHS = (
    "lean.json",
    "config.json",
    ".lean/credentials",
    "lean-workspace/lean.json",
    "_lean_init_tmp/lean.json",
    "lean-workspace/data/market-hours/market-hours-database.json",
    "lean-workspace/storage/Object Store/state.json",
    "lean-workspace/data/custom/parity/v1/synthetic_weekdays.csv",
    "lean-workspace/Strategies/SkeletonBacktest/backtests/run/result.json",
    "lean-workspace/Strategies/MovingAverageBaseline/optimizations/run/result.json",
    "lean-workspace/live/session.json",
    "lean-workspace/Strategies/ParityFixtureV1/backtests/run/result.json",
    "lean-workspace/logs/run.log",
    "lean-workspace/cache/state.json",
    "lean-workspace/results/result.json",
    "lean-workspace/api-token.txt",
    "reports/parity/lean-observation.json",
    "logs/parity/lean-backtest.log",
    "lean-workspace/Strategies/WalkForwardMovingAverageV1/backtests/wf-v1-spy-2021/result.json",
    "logs/walk-forward/v1/wf-v1-spy-2021.log",
    "reports/walk-forward/v1/observations/spy-2021.json",
    "reports/walk-forward/v1/aggregate-record.json",
    "lean-workspace/api-access-token.txt",
    "lean-workspace/auth-token.txt",
)

EXPECTED_TRACKABLE_PATHS = (
    "lean-workspace/README.md",
    "lean-workspace/Strategies/SkeletonBacktest/main.py",
    "lean-workspace/Strategies/SkeletonBacktest/config.json",
    "lean-workspace/Strategies/SkeletonBacktest/README.md",
    "lean-workspace/Strategies/MovingAverageBaseline/main.py",
    "lean-workspace/Strategies/MovingAverageBaseline/config.json",
    "lean-workspace/Strategies/MovingAverageBaseline/README.md",
    "tests/fixtures/parity/v1/synthetic_weekdays.csv",
    "tests/fixtures/parity/v1/scenario.json",
    "lean-workspace/Strategies/ParityFixtureV1/main.py",
    "lean-workspace/Strategies/ParityFixtureV1/config.json",
    "lean-workspace/Strategies/ParityFixtureV1/README.md",
    "lean-workspace/Strategies/WalkForwardMovingAverageV1/main.py",
    "lean-workspace/Strategies/WalkForwardMovingAverageV1/config.json",
    "lean-workspace/Strategies/WalkForwardMovingAverageV1/README.md",
    "contracts/walk-forward/v1/README.md",
    "contracts/walk-forward/v1/protocol.json",
    "contracts/walk-forward/v1/protocol.schema.json",
    "contracts/walk-forward/v1/observation.schema.json",
    "contracts/walk-forward/v1/aggregate-record.schema.json",
    "src/trading_bot_lab/walk_forward/__init__.py",
    "src/trading_bot_lab/walk_forward/contract.py",
    "src/trading_bot_lab/walk_forward/observation.py",
    "src/trading_bot_lab/walk_forward/operator.py",
    "scripts/run_walk_forward_v1.py",
)


def _normalize_key(value: object) -> str:
    return str(value).strip().lower().replace("_", "-")


def _value_is_empty(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _git_paths(root: Path, *args: str) -> tuple[list[str], str | None]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args, "-z"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return [], "git inventory could not be completed"
    if completed.returncode != 0:
        return [], "git inventory could not be completed"
    return [item for item in completed.stdout.split("\0") if item], None


def _git_index_text(root: Path, relative: str) -> tuple[str | None, str | None]:
    normalized = relative.replace("\\", "/")
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "cat-file", "blob", f":{normalized}"],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, f"{normalized} staged content could not be inspected"
    if completed.returncode != 0:
        return None, f"{normalized} staged content could not be inspected"
    try:
        return completed.stdout.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, f"{normalized} staged content is not UTF-8 text"


def _check_ignore(root: Path, relative: str) -> tuple[bool, str | None]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "--no-index", "--quiet", "--", relative],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "git ignore check could not be completed"
    if completed.returncode == 0:
        return True, None
    if completed.returncode == 1:
        return False, None
    return False, "git ignore check could not be completed"


def _should_skip_text_scan(path: Path) -> bool:
    return path.suffix.lower() in SKIPPED_SUFFIXES


def _scan_text(relative: str, text: str) -> list[str]:
    findings: list[str] = []
    relative_path = Path(relative)
    if relative_path.name == "preflight_check.py":
        return findings

    for pattern in FORBIDDEN_PATTERNS:
        if pattern in text and relative_path.parts[0] != "tests":
            findings.append(f"{relative} contains forbidden pattern: {pattern}")
    for regex, description in FORBIDDEN_REGEXES:
        if relative_path.parts[0] == "tests" and description in {
            "live trading is enabled",
            "live trading feature flag is enabled",
            "trading mode is live",
        }:
            continue
        if regex.search(text):
            findings.append(f"{relative} contains {description}")

    if relative_path.suffix == ".py" and relative_path.parts[0] in {"scripts", "src"}:
        for regex, description in SOURCE_FORBIDDEN_REGEXES:
            if regex.search(text):
                findings.append(f"{relative} contains {description}")

    is_ci = relative.startswith(".github/workflows/")
    is_makefile = relative == "Makefile"
    is_default_script = relative.startswith("scripts/") and relative_path.suffix == ".py"
    if (is_ci or is_makefile or is_default_script) and EXECUTABLE_LEAN_COMMAND.search(text):
        findings.append(f"{relative} contains an automated LEAN cloud/live/data command")

    if PRIVATE_KEY_HEADER.search(text):
        findings.append(f"{relative} contains a private-key header")
    if any(pattern.search(text) for pattern in TOKEN_SIGNATURES):
        findings.append(f"{relative} contains a likely credential signature")
    return findings


def _walk_sensitive_json(value: Any, *, prefix: str = "") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = _normalize_key(raw_key)
            dotted = f"{prefix}.{key}" if prefix else key
            if key in SENSITIVE_LEAN_KEYS and not _value_is_empty(child):
                findings.append(dotted)
            findings.extend(_walk_sensitive_json(child, prefix=dotted))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(_walk_sensitive_json(child, prefix=f"{prefix}[{index}]"))
    return findings


def _normalized_json_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for raw_key, child in value.items():
            keys.add(_normalize_key(raw_key))
            keys.update(_normalized_json_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_normalized_json_keys(child))
    return keys


def _lean_cloud_description_findings(relative: str, payload: Any) -> list[str]:
    git_path = PurePosixPath(relative)
    if (
        not isinstance(payload, dict)
        or git_path.parts[:2] != ("lean-workspace", "Strategies")
        or git_path.name != "config.json"
    ):
        return []

    description = payload.get("description")
    if not isinstance(description, str):
        return []

    findings: list[str] = []
    for character in LEAN_CLOUD_DESCRIPTION_FORBIDDEN_CHARACTERS:
        position = description.find(character)
        if position >= 0:
            findings.append(
                f"{relative} description violates the LEAN cloud push contract: "
                f"Invalid character {character!r} found in input string at position {position}."
            )
    return findings


def _lean_workspace_findings(root: Path, candidates: list[str]) -> list[str]:
    findings: list[str] = []
    if (root / ".lean").exists():
        findings.append("repository-local .lean directory is forbidden")

    if not (root / "lean-workspace").exists():
        return findings

    sensitive_name_parts = (
        "api-token",
        "access-token",
        "auth-token",
        "authorization-token",
        "oauth-token",
        "credentials",
        "encryption-key",
        "password",
        "private-key",
        "secret",
    )
    for candidate in candidates:
        relative = candidate.replace("\\", "/")
        git_path = PurePosixPath(relative)
        if git_path.parts[:1] != ("lean-workspace",):
            continue
        if relative == "lean-workspace/lean.json":
            continue
        path = root / relative
        if not path.exists() or not path.is_file() or path.is_symlink():
            continue
        relative_parts = git_path.parts[1:]
        if not relative_parts:
            continue
        lowered_name = path.name.lower().replace("_", "-")
        if any(part.lower() == ".lean" for part in relative_parts):
            findings.append(f"{relative} is repository-local LEAN credential state")
            continue
        if any(part in lowered_name for part in sensitive_name_parts):
            findings.append(f"{relative} has a credential-bearing filename")
            continue
        if any(part.lower() in LEAN_GENERATED_PARTS for part in relative_parts):
            continue
        if path.stat().st_size > 1_000_000 or _should_skip_text_scan(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            findings.append(f"{relative} could not be safely scanned as UTF-8 text")
            continue
        if path.suffix.lower() == ".json":
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                findings.append(f"{relative} is invalid JSON")
            else:
                for key in _walk_sensitive_json(payload):
                    findings.append(f"{relative} contains non-empty sensitive key: {key}")
                findings.extend(_lean_cloud_description_findings(relative, payload))
        if PRIVATE_KEY_HEADER.search(text):
            findings.append(f"{relative} contains a private-key header")
        if any(pattern.search(text) for pattern in TOKEN_SIGNATURES):
            findings.append(f"{relative} contains a likely credential signature")
    return findings


def _tracked_lean_metadata_findings(root: Path, tracked: list[str]) -> list[str]:
    findings: list[str] = []
    for relative in tracked:
        normalized = relative.replace("\\", "/")
        git_path = PurePosixPath(normalized)
        if git_path.parts[:1] != ("lean-workspace",) or git_path.suffix.lower() != ".json":
            continue
        text, error = _git_index_text(root, normalized)
        if error is not None:
            findings.append(error)
            continue
        assert text is not None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            findings.append(f"{normalized} staged content is invalid JSON")
            continue
        findings.extend(_lean_cloud_description_findings(normalized, payload))
        for key in _walk_sensitive_json(payload):
            findings.append(f"{normalized} staged content contains non-empty sensitive key: {key}")
        forbidden = sorted(LEAN_METADATA_KEYS.intersection(_normalized_json_keys(payload)))
        for key in forbidden:
            findings.append(f"{normalized} contains private local cloud-linkage metadata: {key}")
        if PRIVATE_KEY_HEADER.search(text):
            findings.append(f"{normalized} staged content contains a private-key header")
        if any(pattern.search(text) for pattern in TOKEN_SIGNATURES):
            findings.append(f"{normalized} staged content contains a likely credential signature")
    return findings


def _tracked_artifact_findings(tracked: list[str]) -> list[str]:
    findings: list[str] = []
    for tracked_path in tracked:
        normalized = tracked_path.replace("\\", "/")
        path = Path(normalized)
        lowered_parts = tuple(part.lower() for part in path.parts)
        lowered_name = path.name.lower()

        if normalized in {"lean.json", "config.json"}:
            findings.append(f"tracked root LEAN configuration is forbidden: {normalized}")
        if normalized.lower() == "lean-workspace/lean.json":
            findings.append(
                "tracked operator-local LEAN linkage is forbidden: lean-workspace/lean.json"
            )
        if ".lean" in lowered_parts or normalized.startswith("_lean_init_tmp/"):
            findings.append(f"tracked LEAN credential/bootstrap state is forbidden: {normalized}")
        if normalized.startswith("lean-workspace/") and any(
            part in LEAN_GENERATED_PARTS for part in lowered_parts[1:]
        ):
            findings.append(f"tracked generated LEAN artifact is forbidden: {normalized}")

        if normalized.startswith("data/") and normalized not in ALLOWED_DATA:
            findings.append(f"tracked local data artifact is forbidden: {normalized}")
        if normalized.startswith(("logs/", "reports/", "checkpoints/")) and (
            normalized not in ALLOWED_ARTIFACTS
        ):
            findings.append(f"tracked generated artifact is forbidden: {normalized}")
        if normalized.startswith("models/") and normalized not in {
            "models/README.md",
            "models/.gitkeep",
        }:
            findings.append(f"tracked model artifact is forbidden: {normalized}")

        if path.suffix.lower() == ".csv" and normalized not in ALLOWED_TRACKED_CSV:
            findings.append(f"tracked CSV is not an approved synthetic fixture: {normalized}")
        if path.suffix.lower() in TRACKED_BINARY_DENY_SUFFIXES:
            findings.append(f"tracked binary/generated artifact is forbidden: {normalized}")
        if path.suffix.lower() == ".ipynb":
            findings.append(f"tracked notebook output is forbidden: {normalized}")
        if lowered_name == ".env" or (
            lowered_name.startswith(".env.") and lowered_name != ".env.example"
        ):
            findings.append(f"tracked environment file is forbidden: {normalized}")
        if path.suffix.lower() in {".key", ".p12", ".pfx", ".pem"}:
            findings.append(f"tracked credential/key file is forbidden: {normalized}")
    return findings


def _ignore_rule_findings(root: Path) -> list[str]:
    findings: list[str] = []
    for relative in EXPECTED_IGNORED_PATHS:
        ignored, error = _check_ignore(root, relative)
        if error is not None:
            findings.append(error)
            return findings
        if not ignored:
            findings.append(f".gitignore does not ignore required path: {relative}")
    for relative in EXPECTED_TRACKABLE_PATHS:
        ignored, error = _check_ignore(root, relative)
        if error is not None:
            findings.append(error)
            return findings
        if ignored:
            findings.append(f".gitignore incorrectly ignores source/config path: {relative}")
    return findings


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings: list[str] = []

    missing = [path for path in REQUIRED_FILES if not (root / path).exists()]
    for path in missing:
        findings.append(f"missing required file: {path}")

    tracked, tracked_error = _git_paths(root, "ls-files")
    untracked, untracked_error = _git_paths(root, "ls-files", "--others", "--exclude-standard")
    if tracked_error is not None:
        findings.append(tracked_error)
    if untracked_error is not None:
        findings.append(untracked_error)

    candidates = sorted(set(tracked) | set(untracked))
    for relative in candidates:
        path = root / relative
        if not path.exists() or not path.is_file() or _should_skip_text_scan(path):
            continue
        if path.is_symlink():
            findings.append(f"{relative} is a symlink and was not scanned")
            continue
        try:
            if path.stat().st_size > 2_000_000:
                findings.append(f"{relative} is oversized text and requires review")
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        findings.extend(_scan_text(relative.replace("\\", "/"), text))

    findings.extend(_lean_workspace_findings(root, candidates))
    findings.extend(_tracked_lean_metadata_findings(root, tracked))
    findings.extend(_tracked_artifact_findings(tracked))
    findings.extend(_ignore_rule_findings(root))

    if findings:
        print("Preflight failed:")
        for finding in sorted(set(findings)):
            print(f"- {finding}")
        return 1

    print(
        "Preflight passed: required files, public-repository hygiene, credential-free LEAN "
        "workspace sources, and generated-artifact boundaries were verified."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
