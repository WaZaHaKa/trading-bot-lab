from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from scripts.prepare_lean_parity_data import (
    build_lean_daily_payload,
    prepare_lean_parity_data,
)


def test_daily_payload_uses_lean_decicent_format() -> None:
    lines = build_lean_daily_payload().decode("ascii").splitlines()

    assert lines[0] == "20240102 00:00,1000000,1010000,990000,1000000,1000"
    assert lines[-1] == "20240111 00:00,1010000,1060000,1000000,1050000,1070"
    assert len(lines) == 8


def test_prepare_is_deterministic_idempotent_and_non_overwriting(tmp_path: Path) -> None:
    first = prepare_lean_parity_data(tmp_path, require_workspace_support=False)
    first_bytes = first.daily_zip.read_bytes()
    second = prepare_lean_parity_data(tmp_path, require_workspace_support=False)

    assert second == first
    assert second.daily_zip.read_bytes() == first_bytes
    assert first.map_file.read_text(encoding="utf-8") == ("20240102,parity,Q\n20501231,parity,Q\n")
    assert first.factor_file.read_text(encoding="utf-8") == ("20240102,1,1,1\n20501231,1,1,0\n")
    with zipfile.ZipFile(first.daily_zip) as archive:
        assert archive.namelist() == ["parity.csv"]
        assert archive.read("parity.csv") == build_lean_daily_payload()

    first.map_file.write_text("different\n", encoding="utf-8")
    with pytest.raises(ValueError, match="refusing to overwrite"):
        prepare_lean_parity_data(tmp_path, require_workspace_support=False)


def test_workspace_support_files_are_required_for_operator_run(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="support databases are missing"):
        prepare_lean_parity_data(tmp_path)
