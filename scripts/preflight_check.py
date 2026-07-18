from __future__ import annotations

import re
from pathlib import Path

REQUIRED_FILES = [
    "AGENTS.md",
    "README.md",
    ".env.example",
    "docs/risk-policy.md",
    "docs/architecture.md",
    "docs/lean-getting-started.md",
    "docs/lean-paused.md",
    "docs/local-backtesting.md",
    "docs/report-schemas.md",
    "docs/paper-replay.md",
    "docs/adr/0004-shared-event-simulation-core.md",
    "config/risk.example.yaml",
    "src/trading_bot_lab/risk/policy.py",
    "src/trading_bot_lab/domain.py",
    "src/trading_bot_lab/cli.py",
    "src/trading_bot_lab/paper.py",
    "src/trading_bot_lab/observability.py",
    "src/trading_bot_lab/backtesting/engine.py",
    "src/trading_bot_lab/backtesting/csv_data.py",
    "src/trading_bot_lab/backtesting/moving_average.py",
    "src/trading_bot_lab/backtesting/reports.py",
    "tests/test_risk_policy.py",
    "tests/test_market_data.py",
    "tests/test_engine_and_strategy.py",
    "tests/test_reports_paper_cli.py",
    "data/local/.gitkeep",
    "data/sample/README.md",
    "data/sample/synthetic_spy_daily.csv",
    "reports/.gitkeep",
    "scripts/run_local_backtest.py",
    "scripts/run_paper_replay.py",
    "lean/algorithms/SkeletonBacktest/main.py",
    "lean/algorithms/MovingAverageBaseline/main.py",
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
            r"\b(?:broker|exchange|openai|alpaca|binance|coinbase|quantconnect)"
            r"[a-z0-9_-]*(?:api[_-]?key|api[_-]?secret|token)"
            r"\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}",
            re.IGNORECASE,
        ),
        "possible credential value",
    ),
]

SOURCE_FORBIDDEN_REGEXES = [
    (
        re.compile(
            r"^\s*(?:from|import)\s+(?:requests|httpx|aiohttp|urllib3|socket)\b",
            re.MULTILINE,
        ),
        "network client import in the active local MVP",
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
        re.compile(r"[A-Za-z]:\\Users\\", re.IGNORECASE),
        "user-specific absolute Windows path",
    ),
]

EXCLUDED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "build",
    "data",
    "dist",
    "htmlcov",
    "logs",
    "models",
    "reports",
    "results",
    "__pycache__",
    ".pytest-tmp",
    ".pytest_run",
    ".pytest_tmp",
    ".uv",
}

SKIPPED_SUFFIXES = {
    ".csv",
    ".jpeg",
    ".jpg",
    ".parquet",
    ".png",
    ".pyc",
    ".zip",
}


def should_skip_file(path: Path, root: Path) -> bool:
    relative_parts = set(path.relative_to(root).parts)
    if relative_parts & EXCLUDED_PARTS:
        return True
    return path.suffix.lower() in SKIPPED_SUFFIXES


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    missing = [path for path in REQUIRED_FILES if not (root / path).exists()]

    if missing:
        print("Missing required files:")
        for path in missing:
            print(f"- {path}")
        return 1

    checked_files = [p for p in root.rglob("*") if p.is_file() and not should_skip_file(p, root)]
    findings: list[str] = []
    for path in checked_files:
        if path.name == "preflight_check.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        relative = path.relative_to(root)
        for pattern in FORBIDDEN_PATTERNS:
            if pattern in text and relative.parts[0] != "tests":
                findings.append(f"{path.relative_to(root)} contains forbidden pattern: {pattern}")
        for regex, description in FORBIDDEN_REGEXES:
            if relative.parts[0] == "tests" and description in {
                "live trading is enabled",
                "live trading feature flag is enabled",
                "trading mode is live",
            }:
                continue
            if regex.search(text):
                findings.append(f"{path.relative_to(root)} contains {description}")
        if path.suffix == ".py" and relative.parts[0] in {"scripts", "src"}:
            for regex, description in SOURCE_FORBIDDEN_REGEXES:
                if regex.search(text):
                    findings.append(f"{path.relative_to(root)} contains {description}")

    required_ignore_rules = (
        "/data/**",
        "/logs/**",
        "/reports/**",
        "/models/**",
        "*.pkl",
        "*.pt",
        ".env",
    )
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")
    for rule in required_ignore_rules:
        if rule not in gitignore:
            findings.append(f".gitignore is missing required rule: {rule}")

    if findings:
        print("Preflight failed:")
        for finding in findings:
            print(f"- {finding}")
        return 1

    print("Preflight passed: required files exist and no obvious forbidden patterns were found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
