# Scripts

Small local utility scripts only.

- `preflight_check.py`: validates that required starter files exist and obvious live-trading flags are not enabled.
- `print_tree.py`: prints the repository tree.
- `run_local_backtest.py`: delegates to the package's deterministic `backtest` command.
- `run_historical_paper_replay.py`: canonical entirely local `paper-replay` entry point.
- `run_paper_replay.py`: compatible alias for the same local replay command.
- `export_local_parity.py`: exports a normalized v1 local-oracle trace offline.
- `prepare_lean_parity_data.py`: converts only the committed v1 synthetic fixture into
  ignored LEAN daily-equity files without network activity or overwrite.
- `compare_lean_parity.py`: validates and compares two existing v1 traces offline.
