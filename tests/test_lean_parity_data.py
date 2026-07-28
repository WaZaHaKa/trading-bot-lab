from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path

import pytest

from scripts.prepare_lean_parity_data import (
    CANONICAL_FIXTURE_PATH,
    EXPECTED_ROW_COUNT,
    FIXTURE_SCHEMA_VERSION,
    FIXTURE_SHA256,
    STAGED_FIXTURE_DISPLAY_PATH,
    STAGED_FIXTURE_PARTS,
    prepare_lean_parity_data,
    validate_fixture_bytes,
)

CANONICAL_BYTES = CANONICAL_FIXTURE_PATH.read_bytes()


def _lines(payload: bytes = CANONICAL_BYTES) -> list[str]:
    return payload.decode("utf-8").splitlines()


def _payload(lines: list[str]) -> bytes:
    return ("\n".join(lines) + "\n").encode()


def test_fixture_identity_is_exact_lf_only_and_structurally_valid() -> None:
    rows = validate_fixture_bytes(CANONICAL_BYTES)

    assert sha256(CANONICAL_BYTES).hexdigest() == FIXTURE_SHA256
    assert b"\r" not in CANONICAL_BYTES
    assert CANONICAL_BYTES.endswith(b"\n")
    assert len(rows) == EXPECTED_ROW_COUNT
    assert rows[0].symbol == "PARITY"
    assert rows[0].session.isoformat() == "2024-01-02"
    assert rows[-1].session.isoformat() == "2024-01-11"


def test_fixture_mutation_and_crlf_are_rejected() -> None:
    mutation = CANONICAL_BYTES.replace(b",100.00,101.00,", b",100.01,101.00,", 1)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        validate_fixture_bytes(mutation)

    with pytest.raises(ValueError, match="LF line endings"):
        validate_fixture_bytes(CANONICAL_BYTES.replace(b"\n", b"\r\n"))


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda rows: ["day," + rows[0].split(",", 1)[1], *rows[1:]], "columns"),
        (lambda rows: [rows[0], rows[1].rsplit(",", 1)[0], *rows[2:]], "missing or extra"),
        (
            lambda rows: [rows[0], rows[1], rows[2].replace("2024-01-03", "2024-01-02"), *rows[3:]],
            "duplicated",
        ),
        (
            lambda rows: [rows[0], rows[2], rows[1], *rows[3:]],
            "strictly increasing",
        ),
        (
            lambda rows: [
                rows[0],
                rows[1].replace(",101.00,99.00,", ",90.00,99.00,"),
                *rows[2:],
            ],
            "low exceeds high",
        ),
        (
            lambda rows: [rows[0], rows[1].replace(",100.00,101.00,", ",NaN,101.00,"), *rows[2:]],
            "positive and finite",
        ),
        (
            lambda rows: [
                *rows,
                "2024-01-12,PARITY,105.0,106.0,104.0,105.0,1080",
            ],
            "exactly 8 rows",
        ),
        (lambda rows: rows[:-1], "exactly 8 rows"),
    ],
)
def test_malformed_fixture_rows_fail_closed(mutator, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_fixture_bytes(_payload(mutator(_lines())))


def test_missing_fixture_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing"):
        prepare_lean_parity_data(tmp_path, tmp_path / "missing.csv")


def test_staging_is_exact_atomic_idempotent_and_non_overwriting(tmp_path: Path) -> None:
    first = prepare_lean_parity_data(tmp_path)
    second = prepare_lean_parity_data(tmp_path)

    expected = tmp_path.joinpath(*STAGED_FIXTURE_PARTS)
    assert first == second
    assert first.fixture_path == expected
    assert first.fixture_path.read_bytes() == CANONICAL_BYTES
    assert first.fixture_sha256 == FIXTURE_SHA256
    assert first.fixture_schema_version == FIXTURE_SCHEMA_VERSION
    assert first.scenario_manifest_version == "1.0.0"
    assert first.relative_destination == STAGED_FIXTURE_DISPLAY_PATH
    assert first.row_count == EXPECTED_ROW_COUNT
    assert not list(expected.parent.glob(f".{expected.name}.*.tmp"))

    expected.write_bytes(b"different\n")
    with pytest.raises(ValueError, match="refusing to overwrite"):
        prepare_lean_parity_data(tmp_path)


def test_relative_and_foreign_platform_destinations_are_rejected() -> None:
    with pytest.raises(ValueError, match="absolute path"):
        prepare_lean_parity_data(Path("..") / "escape")

    if os.name != "nt":
        with pytest.raises(ValueError, match="native path"):
            prepare_lean_parity_data(r"C:\Users\owner\parity-data")


def test_symlink_source_and_destination_components_are_rejected(tmp_path: Path) -> None:
    source_link = tmp_path / "source.csv"
    try:
        source_link.symlink_to(CANONICAL_FIXTURE_PATH)
    except OSError:
        pytest.skip("symlink creation is unavailable on this platform")
    with pytest.raises(ValueError, match="symlink"):
        prepare_lean_parity_data(tmp_path / "source-data", source_link)

    data_root = tmp_path / "data"
    outside = tmp_path / "outside"
    data_root.mkdir()
    outside.mkdir()
    (data_root / "custom").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        prepare_lean_parity_data(data_root)


def test_destination_file_symlink_is_rejected(tmp_path: Path) -> None:
    destination = tmp_path.joinpath(*STAGED_FIXTURE_PARTS)
    destination.parent.mkdir(parents=True)
    outside = tmp_path / "outside.csv"
    outside.write_bytes(CANONICAL_BYTES)
    try:
        destination.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this platform")

    with pytest.raises(ValueError, match="symlink"):
        prepare_lean_parity_data(tmp_path)
