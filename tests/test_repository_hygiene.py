from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_PYTHON = tuple((ROOT / "src").rglob("*.py")) + tuple((ROOT / "scripts").glob("*.py"))


def active_source_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in ACTIVE_PYTHON)


def test_active_mvp_has_no_network_or_live_adapter_imports() -> None:
    source = active_source_text()
    forbidden_imports = re.compile(
        r"^\s*(?:from|import)\s+(?:requests|httpx|aiohttp|urllib3|socket|ccxt|alpaca)",
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
    pattern = re.compile(r"[A-Za-z]:\\Users\\", re.IGNORECASE)

    assert all(pattern.search(path.read_text(encoding="utf-8")) is None for path in paths)


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


def test_only_synthetic_demonstration_csv_exists_under_data() -> None:
    csv_files = [path.relative_to(ROOT).as_posix() for path in (ROOT / "data").rglob("*.csv")]

    assert csv_files == ["data/sample/synthetic_spy_daily.csv"]
    sample_notes = (ROOT / "data" / "sample" / "README.md").read_text(encoding="utf-8")
    assert "synthetic/demo" in sample_notes
    assert "not suitable for research" in sample_notes and "claims" in sample_notes


def test_lean_is_preserved_and_clearly_paused() -> None:
    assert (ROOT / "lean" / "algorithms" / "SkeletonBacktest" / "main.py").exists()
    assert (ROOT / "lean" / "algorithms" / "MovingAverageBaseline" / "main.py").exists()
    assert "LEAN CLI work is paused" in (ROOT / "docs" / "lean-paused.md").read_text(
        encoding="utf-8"
    )
    runtime = (ROOT / "config" / "runtime.example.yaml").read_text(encoding="utf-8")
    assert "engine: local_python" in runtime
    assert "engine: lean" not in runtime


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

    assert "schema version `1.0.0`" in report_docs.lower()
    assert "Future-row protection" in paper_docs
    assert "no network calls" in paper_docs
