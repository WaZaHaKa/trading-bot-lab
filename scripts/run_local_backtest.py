"""Backward-compatible entry point for the package backtest command."""

from __future__ import annotations

import sys

from trading_bot_lab.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["backtest", *sys.argv[1:]]))
