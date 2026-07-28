"""Derive ignored LEAN-format daily equity data from parity contract v1.

This script is deliberately offline. It never invokes LEAN, Docker, a data
provider, or a network client, and it refuses to overwrite differing files.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import zipfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from trading_bot_lab.parity import DEFAULT_SCENARIO_PATH
from trading_bot_lab.parity.contract import load_scenario_bundle, sha256_bytes

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIRECTORY = ROOT / "lean-workspace" / "data"
PRICE_SCALE = Decimal("10000")


@dataclass(frozen=True)
class PreparedLeanParityData:
    """Paths and identities of deterministic derived LEAN data files."""

    daily_zip: Path
    map_file: Path
    factor_file: Path
    daily_zip_sha256: str
    source_fixture_sha256: str


def _scaled_price(raw: str, *, field: str, row_number: int) -> int:
    try:
        value = Decimal(raw)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"row {row_number} {field} must be a decimal") from exc
    scaled = value * PRICE_SCALE
    if not value.is_finite() or value <= 0 or scaled != scaled.to_integral_value():
        raise ValueError(f"row {row_number} {field} must be positive with at most four decimals")
    return int(scaled)


def build_lean_daily_payload(scenario_path: str | Path = DEFAULT_SCENARIO_PATH) -> bytes:
    """Return deterministic LEAN daily CSV bytes for the selected scenario."""

    scenario = load_scenario_bundle(scenario_path)
    expected_symbol = str(scenario.manifest["symbol"])
    if expected_symbol != "PARITY":
        raise ValueError("v1 LEAN parity preparation requires the synthetic PARITY symbol")

    with scenario.fixture_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        expected_fields = ["date", "symbol", "open", "high", "low", "close", "volume"]
        if reader.fieldnames != expected_fields:
            raise ValueError("parity CSV columns do not match the v1 fixture contract")

        output: list[str] = []
        previous_date: date | None = None
        for row_number, row in enumerate(reader, start=2):
            selected_date = date.fromisoformat(row["date"])
            if previous_date is not None and selected_date <= previous_date:
                raise ValueError("parity CSV dates must be strictly increasing")
            if selected_date.weekday() >= 5:
                raise ValueError("parity CSV must contain weekday sessions only")
            if row["symbol"] != expected_symbol:
                raise ValueError(f"row {row_number} symbol differs from the scenario")
            try:
                volume = int(row["volume"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"row {row_number} volume must be an integer") from exc
            if volume < 0:
                raise ValueError(f"row {row_number} volume must be non-negative")
            prices = [
                _scaled_price(row[field], field=field, row_number=row_number)
                for field in ("open", "high", "low", "close")
            ]
            open_price, high_price, low_price, close_price = prices
            if high_price < max(open_price, close_price) or low_price > min(
                open_price, close_price
            ):
                raise ValueError(f"row {row_number} has inconsistent OHLC values")
            if low_price > high_price:
                raise ValueError(f"row {row_number} low exceeds high")
            encoded_prices = ",".join(str(value) for value in prices)
            output.append(f"{selected_date:%Y%m%d} 00:00,{encoded_prices},{volume}\n")
            previous_date = selected_date

    if not output:
        raise ValueError("parity CSV must contain at least one row")
    return "".join(output).encode("ascii")


def _deterministic_zip(member_name: str, payload: bytes) -> bytes:
    buffer = io.BytesIO()
    info = zipfile.ZipInfo(member_name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return buffer.getvalue()


def _write_new_or_verify(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != payload:
            raise ValueError(f"refusing to overwrite differing LEAN parity file: {path}")
        return
    try:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
    except FileExistsError as exc:
        raise ValueError(f"LEAN parity file appeared concurrently: {path}") from exc


def prepare_lean_parity_data(
    data_directory: str | Path = DEFAULT_DATA_DIRECTORY,
    scenario_path: str | Path = DEFAULT_SCENARIO_PATH,
    *,
    require_workspace_support: bool = True,
) -> PreparedLeanParityData:
    """Create or verify the three synthetic files LEAN needs for PARITY."""

    selected_data = Path(data_directory).resolve()
    if require_workspace_support:
        required = (
            selected_data / "market-hours" / "market-hours-database.json",
            selected_data / "symbol-properties" / "symbol-properties-database.csv",
        )
        missing = [path for path in required if not path.is_file()]
        if missing:
            raise ValueError(
                "LEAN workspace support databases are missing; complete the reviewed one-time "
                "lean init bootstrap first (never run a data-download command)"
            )

    scenario = load_scenario_bundle(scenario_path)
    daily_payload = build_lean_daily_payload(scenario_path)
    daily_zip_payload = _deterministic_zip("parity.csv", daily_payload)
    fixture_lines = scenario.fixture_path.read_text(encoding="utf-8").splitlines()
    first_date = date.fromisoformat(fixture_lines[1].split(",", 1)[0])
    map_payload = f"{first_date:%Y%m%d},parity,Q\n20501231,parity,Q\n".encode()
    factor_payload = f"{first_date:%Y%m%d},1,1,1\n20501231,1,1,0\n".encode()

    daily_zip = selected_data / "equity" / "usa" / "daily" / "parity.zip"
    map_file = selected_data / "equity" / "usa" / "map_files" / "parity.csv"
    factor_file = selected_data / "equity" / "usa" / "factor_files" / "parity.csv"
    _write_new_or_verify(daily_zip, daily_zip_payload)
    _write_new_or_verify(map_file, map_payload)
    _write_new_or_verify(factor_file, factor_payload)
    return PreparedLeanParityData(
        daily_zip=daily_zip,
        map_file=map_file,
        factor_file=factor_file,
        daily_zip_sha256=sha256_bytes(daily_zip_payload),
        source_fixture_sha256=scenario.fixture_sha256,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare ignored LEAN daily data from the committed parity fixture offline."
    )
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO_PATH)
    parser.add_argument("--data-directory", type=Path, default=DEFAULT_DATA_DIRECTORY)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        prepared = prepare_lean_parity_data(args.data_directory, args.scenario)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"Prepared synthetic LEAN data: {prepared.daily_zip}")
    print(f"Source fixture SHA-256: {prepared.source_fixture_sha256}")
    print(f"Derived daily ZIP SHA-256: {prepared.daily_zip_sha256}")
    print("Network activity: none; QCC spend: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
