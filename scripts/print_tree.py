from __future__ import annotations

from pathlib import Path

IGNORED = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}


def print_tree(path: Path, prefix: str = "") -> None:
    entries = sorted(
        [p for p in path.iterdir() if p.name not in IGNORED],
        key=lambda p: (p.is_file(), p.name.lower()),
    )
    for index, entry in enumerate(entries):
        connector = "└── " if index == len(entries) - 1 else "├── "
        print(prefix + connector + entry.name)
        if entry.is_dir():
            extension = "    " if index == len(entries) - 1 else "│   "
            print_tree(entry, prefix + extension)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    print(root.name)
    print_tree(root)


if __name__ == "__main__":
    main()
