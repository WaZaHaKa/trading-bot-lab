"""Convenience entry point for deterministic local historical paper replay."""

from __future__ import annotations

import sys

from trading_bot_lab.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["paper-replay", *sys.argv[1:]]))
