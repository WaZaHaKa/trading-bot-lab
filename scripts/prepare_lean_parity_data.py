"""Stage the exact v1 parity fixture for an offline LEAN custom-data run.

The command is deliberately offline. It copies the authoritative committed
bytes to one fixed ignored path and never invokes LEAN, Docker, Object Store,
or a network client.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path, PureWindowsPath

from trading_bot_lab.parity import DEFAULT_SCENARIO_PATH
from trading_bot_lab.parity.contract import SCENARIO_MANIFEST_VERSION, sha256_bytes

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIRECTORY = ROOT / "lean-workspace" / "data"
CANONICAL_FIXTURE_PATH = DEFAULT_SCENARIO_PATH.with_name("synthetic_weekdays.csv")
STAGED_FIXTURE_PARTS = ("custom", "parity", "v1", "synthetic_weekdays.csv")
STAGED_FIXTURE_DISPLAY_PATH = "lean-workspace/data/" + "/".join(STAGED_FIXTURE_PARTS)
FIXTURE_SHA256 = "a68bcf7fc30d2593b32e5a98852c4f8e0190ed99865640485b344515d9f1f78a"
FIXTURE_SCHEMA_VERSION = "1.0.0"
EXPECTED_SYMBOL = "PARITY"
EXPECTED_ROW_COUNT = 8
EXPECTED_FIELDS = ("date", "symbol", "open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class ParityFixtureRow:
    """Strictly validated source row used by preparation tests and tooling."""

    session: date
    symbol: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True)
class PreparedLeanParityData:
    """Identity of the exact fixture staged beneath ignored LEAN data."""

    fixture_path: Path
    relative_destination: str
    fixture_sha256: str
    fixture_schema_version: str
    scenario_manifest_version: str
    row_count: int


def validate_fixture_bytes(payload: bytes) -> tuple[ParityFixtureRow, ...]:
    """Validate exact v1 fixture bytes without normalizing any source value."""

    if not payload:
        raise ValueError("parity fixture must not be empty")
    if b"\r" in payload:
        raise ValueError("parity fixture must use LF line endings; CR bytes are forbidden")
    if not payload.endswith(b"\n"):
        raise ValueError("parity fixture must end with one LF byte")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("parity fixture must be valid UTF-8") from exc

    reader = csv.DictReader(io.StringIO(text, newline=""))
    if tuple(reader.fieldnames or ()) != EXPECTED_FIELDS:
        raise ValueError("parity fixture columns do not match the v1 contract")

    rows: list[ParityFixtureRow] = []
    seen_dates: set[date] = set()
    previous_date: date | None = None
    for row_number, raw in enumerate(reader, start=2):
        if None in raw or any(raw.get(field) in (None, "") for field in EXPECTED_FIELDS):
            raise ValueError(f"row {row_number} has missing or extra columns")
        try:
            selected_date = date.fromisoformat(raw["date"])
        except ValueError as exc:
            raise ValueError(f"row {row_number} date must use YYYY-MM-DD") from exc
        if selected_date in seen_dates:
            raise ValueError("parity fixture dates must not be duplicated")
        if previous_date is not None and selected_date < previous_date:
            raise ValueError("parity fixture dates must be strictly increasing")
        if selected_date.weekday() >= 5:
            raise ValueError("parity fixture must contain weekday sessions only")
        if raw["symbol"] != EXPECTED_SYMBOL:
            raise ValueError(f"row {row_number} symbol differs from the v1 contract")

        prices = tuple(
            _positive_decimal(raw[field], field=field, row_number=row_number)
            for field in ("open", "high", "low", "close")
        )
        open_price, high_price, low_price, close_price = prices
        if low_price > high_price:
            raise ValueError(f"row {row_number} low exceeds high")
        if high_price < max(open_price, close_price):
            raise ValueError(f"row {row_number} high is below open or close")
        if low_price > min(open_price, close_price):
            raise ValueError(f"row {row_number} low is above open or close")
        try:
            volume = int(raw["volume"])
        except ValueError as exc:
            raise ValueError(f"row {row_number} volume must be an integer") from exc
        if str(volume) != raw["volume"] or volume < 0:
            raise ValueError(f"row {row_number} volume must be a non-negative integer")

        rows.append(
            ParityFixtureRow(
                session=selected_date,
                symbol=raw["symbol"],
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=volume,
            )
        )
        seen_dates.add(selected_date)
        previous_date = selected_date

    if len(rows) != EXPECTED_ROW_COUNT:
        raise ValueError(f"parity fixture must contain exactly {EXPECTED_ROW_COUNT} rows")
    actual_hash = sha256_bytes(payload)
    if actual_hash != FIXTURE_SHA256:
        raise ValueError(
            f"parity fixture SHA-256 mismatch: expected {FIXTURE_SHA256}, observed {actual_hash}"
        )
    return tuple(rows)


def _positive_decimal(raw: str, *, field: str, row_number: int) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"row {row_number} {field} must be a decimal") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError(f"row {row_number} {field} must be positive and finite")
    return value


def _reject_ambiguous_non_native_path(path: str | Path, *, field: str) -> Path:
    raw = os.fspath(path)
    if os.name != "nt" and PureWindowsPath(raw).is_absolute():
        raise ValueError(f"{field} must use a native path on this platform")
    return Path(path)


def _ensure_no_symlink_components(path: Path, *, field: str) -> None:
    selected = path.absolute()
    for component in (selected, *selected.parents):
        if component.exists() and component.is_symlink():
            raise ValueError(f"{field} must not contain symlink components")


def _safe_destination(data_directory: str | Path) -> Path:
    root = _reject_ambiguous_non_native_path(data_directory, field="data directory")
    if not root.is_absolute():
        raise ValueError("data directory must be an absolute path")
    _ensure_no_symlink_components(root, field="data directory")
    root.mkdir(parents=True, exist_ok=True)
    _ensure_no_symlink_components(root, field="data directory")
    resolved_root = root.resolve(strict=True)
    destination = resolved_root.joinpath(*STAGED_FIXTURE_PARTS)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _ensure_no_symlink_components(destination.parent, field="destination")
    if destination.parent.resolve(strict=True) != resolved_root.joinpath(
        *STAGED_FIXTURE_PARTS[:-1]
    ):
        raise ValueError("LEAN parity destination escapes the selected data directory")
    if destination.is_symlink():
        raise ValueError("LEAN parity destination must not be a symlink")
    return destination


def _atomic_write_new_or_verify(path: Path, payload: bytes) -> None:
    if path.exists():
        if not path.is_file() or path.is_symlink() or path.read_bytes() != payload:
            raise ValueError("refusing to overwrite a differing LEAN parity fixture")
        return

    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            if not path.is_file() or path.is_symlink() or path.read_bytes() != payload:
                raise ValueError("refusing to overwrite a concurrently created parity fixture")
            return
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_lean_parity_data(
    data_directory: str | Path = DEFAULT_DATA_DIRECTORY,
    fixture_path: str | Path = CANONICAL_FIXTURE_PATH,
) -> PreparedLeanParityData:
    """Atomically stage exact committed fixture bytes beneath one safe data root."""

    source = _reject_ambiguous_non_native_path(fixture_path, field="fixture path")
    _ensure_no_symlink_components(source, field="fixture path")
    if not source.is_file():
        raise ValueError("canonical parity fixture is missing or is not a regular file")
    payload = source.read_bytes()
    rows = validate_fixture_bytes(payload)
    destination = _safe_destination(data_directory)
    _atomic_write_new_or_verify(destination, payload)
    return PreparedLeanParityData(
        fixture_path=destination,
        relative_destination=STAGED_FIXTURE_DISPLAY_PATH,
        fixture_sha256=sha256_bytes(payload),
        fixture_schema_version=FIXTURE_SCHEMA_VERSION,
        scenario_manifest_version=SCENARIO_MANIFEST_VERSION,
        row_count=len(rows),
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description=("Stage the exact committed parity fixture beneath ignored LEAN custom data.")
    )


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    try:
        prepared = prepare_lean_parity_data()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"Prepared synthetic LEAN data: {prepared.relative_destination}")
    print(f"Source fixture SHA-256: {prepared.fixture_sha256}")
    print(f"Scenario manifest version: {prepared.scenario_manifest_version}")
    print("Network activity: none; Object Store activity: none; QCC spend: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
