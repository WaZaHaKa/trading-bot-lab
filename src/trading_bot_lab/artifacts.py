"""Atomic local artifact writes shared by reports and replay manifests."""

from __future__ import annotations

import csv
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path, PureWindowsPath


def atomic_write_text(path: str | Path, text: str) -> Path:
    """Flush a same-directory temporary file before atomically replacing a path."""

    destination = _prepare_destination(path)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise
    return destination


def atomic_write_csv(
    path: str | Path,
    fieldnames: Sequence[str],
    rows: Iterable[Mapping[str, object]],
) -> Path:
    """Write a deterministic CSV to a temporary sibling and replace on success."""

    destination = _prepare_destination(path)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(fieldnames),
                extrasaction="raise",
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise
    return destination


def stable_csv_value(value: object) -> object:
    """Use an explicit round-trippable representation for finite float fields."""

    if isinstance(value, float):
        return format(value, ".17g")
    return value


def artifact_filename(path: str | Path) -> str:
    """Return only a safe artifact basename for manifests."""

    selected = PureWindowsPath(str(path).replace("/", "\\")).name.strip()
    if selected in {"", ".", ".."}:
        raise ValueError("artifact path must include a safe filename")
    return selected


def _prepare_destination(path: str | Path) -> Path:
    destination = Path(path)
    if not destination.name:
        raise ValueError("artifact path must include a filename")
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination


__all__ = [
    "artifact_filename",
    "atomic_write_csv",
    "atomic_write_text",
    "stable_csv_value",
]
