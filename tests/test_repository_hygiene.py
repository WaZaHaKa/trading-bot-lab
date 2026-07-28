from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from hashlib import sha256
from pathlib import Path

import pytest

from scripts.preflight_check import (
    _tracked_artifact_findings,
    _tracked_lean_metadata_findings,
    _walk_sensitive_json,
)

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_PYTHON = tuple((ROOT / "src").rglob("*.py")) + tuple((ROOT / "scripts").glob("*.py"))
USER_PATH_PATTERN = re.compile(
    r"(?:[A-Za-z]:[\\/]Users[\\/][^\\/\s]+|/(?:home)/[^/\s]+)",
    re.IGNORECASE,
)


def active_source_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in ACTIVE_PYTHON)


def git_source_files() -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(path for path in result.stdout.split("\0") if path)


def initialize_git_fixture(root: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=root,
        check=True,
    )


def test_active_mvp_has_no_network_or_live_adapter_imports() -> None:
    source = active_source_text()
    forbidden_imports = re.compile(
        r"^\s*(?:from|import)\s+"
        r"(?:requests|httpx|aiohttp|urllib3|socket|http\.client|urllib\.request|"
        r"websocket|websockets|ccxt|alpaca)",
        re.MULTILINE,
    )

    assert forbidden_imports.search(source) is None
    assert not any(
        path.name.lower().startswith(("broker", "exchange", "live"))
        for path in (ROOT / "src").rglob("*.py")
    )


def test_active_mvp_has_no_unsafe_deserialization_or_shell_execution() -> None:
    source = active_source_text()
    patterns = (
        r"\bpickle\.loads?\s*\(",
        r"\bjoblib\.load\s*\(",
        r"\beval\s*\(",
        r"\bexec\s*\(",
        r"\bos\.system\s*\(",
        r"shell\s*=\s*True",
    )

    assert all(re.search(pattern, source) is None for pattern in patterns)


def test_no_user_specific_absolute_paths_in_project_text() -> None:
    paths = list((ROOT / "src").rglob("*.py"))
    paths += list((ROOT / "scripts").glob("*.py"))
    paths += list((ROOT / "docs").rglob("*.md"))
    paths += [ROOT / "README.md", ROOT / "pyproject.toml"]
    assert all(USER_PATH_PATTERN.search(path.read_text(encoding="utf-8")) is None for path in paths)


@pytest.mark.parametrize(
    "value",
    [r"C:\Users\owner\project", "C:/Users/owner/project", "/home/owner/project"],
)
def test_user_specific_absolute_path_guard_covers_common_forms(value: str) -> None:
    assert USER_PATH_PATTERN.search(value) is not None


def test_no_likely_secret_values_in_source_config_or_docs() -> None:
    paths = list((ROOT / "src").rglob("*.py"))
    paths += list((ROOT / "config").glob("*"))
    paths += [ROOT / ".env.example"]
    assignment = re.compile(
        r"(?im)^\s*(?:api[_-]?key|api[_-]?secret|password|private[_-]?key|token)"
        r"\s*[:=]\s*['\"]?[A-Za-z0-9_\-/+=]{16,}"
    )

    assert all(assignment.search(path.read_text(encoding="utf-8")) is None for path in paths)


def test_primary_package_has_no_runtime_dependencies() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["dependencies"] == []


def test_tracked_csvs_are_only_approved_synthetic_fixtures() -> None:
    csv_files = sorted(path for path in git_source_files() if path.endswith(".csv"))

    assert csv_files == [
        "data/sample/synthetic_spy_daily.csv",
        "tests/fixtures/parity/v1/synthetic_weekdays.csv",
    ]
    sample_notes = (ROOT / "data" / "sample" / "README.md").read_text(encoding="utf-8")
    assert "synthetic/demo" in sample_notes
    assert "not suitable for research" in sample_notes and "claims" in sample_notes


def test_lean_workspace_is_active_backtest_only_and_legacy_is_preserved() -> None:
    expected = {
        "README.md",
        "algorithms/README.md",
        "algorithms/MovingAverageBaseline/README.md",
        "algorithms/MovingAverageBaseline/config.json",
        "algorithms/MovingAverageBaseline/main.py",
        "algorithms/SkeletonBacktest/README.md",
        "algorithms/SkeletonBacktest/config.json",
        "algorithms/SkeletonBacktest/main.py",
        "data/.gitkeep",
        "data/README.md",
        "results/.gitkeep",
        "results/README.md",
    }
    actual = {
        path.relative_to(ROOT / "lean").as_posix()
        for path in (ROOT / "lean").rglob("*")
        if path.is_file()
    }
    assert expected <= actual
    workspace_files = {
        "README.md",
        "Strategies/MovingAverageBaseline/README.md",
        "Strategies/MovingAverageBaseline/config.json",
        "Strategies/MovingAverageBaseline/main.py",
        "Strategies/SkeletonBacktest/README.md",
        "Strategies/SkeletonBacktest/config.json",
        "Strategies/SkeletonBacktest/main.py",
        "Strategies/ParityFixtureV1/README.md",
        "Strategies/ParityFixtureV1/config.json",
        "Strategies/ParityFixtureV1/main.py",
    }
    workspace_actual = {
        path.relative_to(ROOT / "lean-workspace").as_posix()
        for path in (ROOT / "lean-workspace").rglob("*")
        if path.is_file()
    }
    assert workspace_files <= workspace_actual
    workspace_readme = (ROOT / "lean-workspace" / "README.md").read_text(encoding="utf-8")
    assert "active" in workspace_readme.lower()
    assert "backtest" in workspace_readme.lower()
    assert "live trading" in workspace_readme.lower()
    runtime = (ROOT / "config" / "runtime.example.yaml").read_text(encoding="utf-8")
    assert "primary_engine: lean_cloud" in runtime
    assert "parity_oracle: local_python" in runtime
    assert "live_trading_enabled: false" in runtime


def test_repository_current_state_is_public() -> None:
    current_files = (
        ROOT / "README.md",
        ROOT / "LICENSE_NOT_SELECTED.md",
        ROOT / "docs" / "security.md",
        ROOT / "docs" / "codex-workflow.md",
        ROOT / "codex-prompts" / "001-setup-first-backtest.md",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in current_files).lower()

    assert "public" in combined
    assert "keep this repository private" not in combined
    assert "we are in the private repository" not in combined


@pytest.mark.parametrize(
    "relative",
    [
        "lean.json",
        "config.json",
        ".lean/credentials",
        "_lean_init_tmp/lean.json",
        "lean-workspace/lean.json",
        "lean-workspace/data/market-hours/market-hours-database.json",
        "lean-workspace/storage/Object Store/state.json",
        "lean-workspace/Strategies/SkeletonBacktest/backtests/run/result.json",
        "lean-workspace/Strategies/MovingAverageBaseline/optimizations/run/result.json",
        "lean-workspace/data/custom/parity/v1/synthetic_weekdays.csv",
        "lean-workspace/Strategies/ParityFixtureV1/backtests/run/result.json",
        "reports/parity/lean-observation.json",
        "logs/parity/lean-backtest.log",
        "logs/parity/runtime-state.json",
        "logs/parity/runtime-v1/home/.lean/config",
        "reports/parity/runtime-audit-v1.json",
        "reports/parity/comparison-v1.json",
        "lean-workspace/Strategies/ParityFixtureV1/backtests/parity-v1-run-1/result.json",
        "lean-workspace/live/session.json",
        "lean-workspace/logs/run.log",
        "reports/lean-cloud/skeleton-validation.log",
        "lean-workspace/cache/state.json",
        "lean-workspace/results/result.json",
        "lean-workspace/api-token.txt",
        "lean-workspace/api-access-token.txt",
        "lean-workspace/auth-token.txt",
    ],
)
def test_lean_workspace_generated_paths_are_ignored(relative: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "check-ignore", "--no-index", "--quiet", "--", relative],
        check=False,
    )

    assert result.returncode == 0, relative


@pytest.mark.parametrize(
    "relative",
    [
        "lean-workspace/README.md",
        "lean-workspace/Strategies/SkeletonBacktest/main.py",
        "lean-workspace/Strategies/SkeletonBacktest/config.json",
        "lean-workspace/Strategies/MovingAverageBaseline/main.py",
        "lean-workspace/Strategies/MovingAverageBaseline/config.json",
        "tests/fixtures/parity/v1/synthetic_weekdays.csv",
        "lean-workspace/Strategies/ParityFixtureV1/main.py",
        "lean-workspace/Strategies/ParityFixtureV1/config.json",
        "lean-workspace/Strategies/ParityFixtureV1/README.md",
        "tests/fixtures/parity/v1/scenario.json",
    ],
)
def test_lean_workspace_source_and_fixture_paths_are_trackable(relative: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "check-ignore", "--no-index", "--quiet", "--", relative],
        check=False,
    )

    assert result.returncode == 1, relative


def test_lean_workspace_json_has_no_sensitive_values() -> None:
    sensitive = {
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

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for raw_key, child in value.items():
                key = str(raw_key).strip().lower().replace("_", "-")
                if key in sensitive:
                    assert child in (None, "", [], {})
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for key in ("api-access-token", "auth_token", "client-secret"):
        with pytest.raises(AssertionError):
            visit({key: "redacted-non-empty-test-value"})
        assert _walk_sensitive_json({key: "redacted-non-empty-test-value"}) == [
            key.replace("_", "-")
        ]

    for path in (ROOT / "lean-workspace").rglob("*.json"):
        generated_parts = {
            "data",
            "storage",
            "backtests",
            "optimizations",
            "live",
            "logs",
            "cache",
            "results",
        }
        if generated_parts.intersection(part.lower() for part in path.parts):
            continue
        if path == ROOT / "lean-workspace" / "lean.json":
            continue
        visit(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("metadata_key", ["cloud-id", "organization-id", "local-id"])
def test_preflight_rejects_linkage_metadata_in_tracked_project_configs(
    tmp_path: Path,
    metadata_key: str,
) -> None:
    initialize_git_fixture(tmp_path)
    relative = "lean-workspace/Strategies/Example/config.json"
    config = tmp_path / relative
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "algorithm-language": "Python",
                "description": "Public example.",
                metadata_key: "private-local-linkage",
                "parameters": {"symbol": "SPY"},
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "--", relative], cwd=tmp_path, check=True)
    config.write_text(
        json.dumps(
            {
                "algorithm-language": "Python",
                "description": "Public example.",
                "parameters": {"symbol": "SPY"},
            }
        ),
        encoding="utf-8",
    )

    assert _tracked_lean_metadata_findings(tmp_path, [relative]) == [
        f"{relative} contains private local cloud-linkage metadata: {metadata_key}"
    ]


def test_preflight_linkage_check_uses_staged_not_worktree_bytes(tmp_path: Path) -> None:
    initialize_git_fixture(tmp_path)
    relative = "lean-workspace/Strategies/Example/config.json"
    config = tmp_path / relative
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps({"algorithm-language": "Python", "parameters": {"symbol": "SPY"}}),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "--", relative], cwd=tmp_path, check=True)
    config.write_text(
        json.dumps(
            {
                "algorithm-language": "Python",
                "organization-id": "private-worktree-only-linkage",
            }
        ),
        encoding="utf-8",
    )

    assert _tracked_lean_metadata_findings(tmp_path, [relative]) == []


def test_preflight_rejects_staged_sensitive_key_hidden_by_clean_worktree(tmp_path: Path) -> None:
    initialize_git_fixture(tmp_path)
    relative = "lean-workspace/Strategies/Example/config.json"
    config = tmp_path / relative
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps({"algorithm-language": "Python", "api-token": "private-staged-value"}),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "--", relative], cwd=tmp_path, check=True)
    config.write_text(
        json.dumps({"algorithm-language": "Python", "parameters": {"symbol": "SPY"}}),
        encoding="utf-8",
    )

    assert _tracked_lean_metadata_findings(tmp_path, [relative]) == [
        f"{relative} staged content contains non-empty sensitive key: api-token"
    ]


@pytest.mark.parametrize(
    ("credential_value", "expected_description"),
    [
        ("-----BEGIN " + "PRIVATE KEY-----", "a private-key header"),
        ("ghp_" + "A" * 20, "a likely credential signature"),
    ],
)
def test_preflight_rejects_staged_credential_signature_hidden_by_clean_worktree(
    tmp_path: Path,
    credential_value: str,
    expected_description: str,
) -> None:
    initialize_git_fixture(tmp_path)
    relative = "lean-workspace/Strategies/Example/config.json"
    config = tmp_path / relative
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps({"algorithm-language": "Python", "note": credential_value}),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "--", relative], cwd=tmp_path, check=True)
    config.write_text(
        json.dumps({"algorithm-language": "Python", "parameters": {"symbol": "SPY"}}),
        encoding="utf-8",
    )

    assert _tracked_lean_metadata_findings(tmp_path, [relative]) == [
        f"{relative} staged content contains {expected_description}"
    ]


def test_preflight_rejects_force_tracked_operator_linkage_file() -> None:
    assert _tracked_artifact_findings(["lean-workspace/lean.json"]) == [
        "tracked operator-local LEAN linkage is forbidden: lean-workspace/lean.json"
    ]


@pytest.mark.parametrize(
    "relative",
    [
        "tests/fixtures/parity/v1/synthetic_weekdays.csv",
        "tests/fixtures/parity/v1/scenario.json",
        "contracts/parity/v1/contract.json",
        "contracts/lean-cloud-validation/v1/2026-07-28.json",
        "contracts/lean-cloud-validation/v1/record.schema.json",
        "contracts/lean-local-parity/v1/2026-07-28.json",
        "contracts/lean-local-parity/v1/record.schema.json",
        "lean-workspace/Strategies/ParityFixtureV1/main.py",
        "lean-workspace/Strategies/ParityFixtureV1/config.json",
    ],
)
def test_versioned_contract_identity_files_have_lf_policy(relative: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "check-attr", "text", "eol", "--", relative],
        check=True,
        capture_output=True,
        text=True,
    )

    assert f"{relative}: text: set" in result.stdout
    assert f"{relative}: eol: lf" in result.stdout
    checked_out = (ROOT / relative).read_bytes()
    assert b"\r\n" not in checked_out
    assert checked_out.endswith(b"\n")


def test_cloud_validation_digest_bound_files_have_canonical_lf_bytes() -> None:
    record = json.loads(
        (ROOT / "contracts" / "lean-cloud-validation" / "v1" / "2026-07-28.json").read_text(
            encoding="utf-8"
        )
    )
    bindings: list[tuple[str, str]] = []
    for project in record["projects"]:
        project_root = Path("lean-workspace") / project["project_name"]
        digests = project["evidence_sha256"]
        bindings.extend(
            [
                ((project_root / "main.py").as_posix(), digests["source_sha256"]),
                (
                    (project_root / "config.json").as_posix(),
                    digests["public_configuration_sha256"],
                ),
            ]
        )

    relative_paths = [relative for relative, _ in bindings]
    result = subprocess.run(
        ["git", "-C", str(ROOT), "check-attr", "-z", "text", "eol", "--", *relative_paths],
        check=True,
        capture_output=True,
        text=True,
    )
    fields = result.stdout.split("\0")
    assert fields[-1] == "", "git check-attr output was not NUL terminated"
    fields.pop()
    assert len(fields) % 3 == 0, (
        "git check-attr output did not contain path/attribute/value triples"
    )
    attributes: dict[str, dict[str, str]] = {}
    for index in range(0, len(fields), 3):
        relative, attribute, value = fields[index : index + 3]
        attributes.setdefault(relative, {})[attribute] = value

    for relative, expected_digest in bindings:
        resolved = attributes.get(relative, {})
        assert resolved.get("text") == "set", (
            f"{relative}: expected effective Git text=set, got {resolved.get('text')!r}"
        )
        assert resolved.get("eol") == "lf", (
            f"{relative}: expected effective Git eol=lf, got {resolved.get('eol')!r}"
        )
        checked_out_bytes = (ROOT / relative).read_bytes()
        assert b"\r\n" not in checked_out_bytes, f"{relative}: checkout contains CRLF bytes"
        actual_digest = sha256(checked_out_bytes).hexdigest()
        assert actual_digest == expected_digest, (
            f"{relative}: checked-out SHA-256 {actual_digest} does not match canonical "
            f"evidence digest {expected_digest}"
        )


def test_ci_has_no_lean_cloud_live_login_optimization_or_data_command() -> None:
    workflows = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / ".github" / "workflows").glob("*")
    )
    forbidden = re.compile(
        r"\blean(?:\.exe)?\s+(?:login|live|optimize|cloud|data\s+download)\b",
        re.IGNORECASE,
    )

    assert forbidden.search(workflows) is None
    assert workflows.count("permissions:\n  contents: read") == 1
    assert workflows.count("persist-credentials: false") == 1


def test_preflight_security_and_required_file_checks_pass() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "preflight_check.py")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Preflight passed" in result.stdout


def test_report_and_paper_schema_docs_exist() -> None:
    report_docs = (ROOT / "docs" / "report-schemas.md").read_text(encoding="utf-8")
    paper_docs = (ROOT / "docs" / "paper-replay.md").read_text(encoding="utf-8")

    assert "json backtest summary (`1.2.0`)" in report_docs.lower()
    assert "paper-session manifest (`1.2.0`)" in report_docs.lower()
    assert "Future-row and state protection" in paper_docs
    assert "no network calls" in paper_docs
