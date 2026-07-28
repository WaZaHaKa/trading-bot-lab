# Historical paper replay

Historical paper replay is an entirely local, synchronous simulation. It is not
a broker paper account, background service, or live-trading mode and makes no
network calls.
There are no network calls in validation, replay, reporting, or logging.

## Reproducibility contract

Before the lifecycle becomes usable, the complete sequence is validated for
one symbol, ascending UTC timestamps, consistent timeframe, and executable
opens. CSV metadata includes the SHA-256 of the exact bytes read, a canonical
normalized-bar SHA-256, and a safe filename only. In-memory bars use the
canonical content hash. Session identity is
derived from content hash, data shape/interval, engine, strategy, assumptions,
and risk policy; it does not depend on a machine path or filename.

Replay speed affects scheduling only. The declared random seed is recorded even
though the current simulator uses no randomness. Neither value changes the
financial calculation.

## Future-row and state protection

`HistoricalReplaySession.step()` delivers one unread bar to the same
`SimulationEngine` used by batch backtests. Strategy code receives an immutable
tuple containing at most `strategy_history_limit` already-visible bars, ending
at the current bar. It receives no full dataframe, cursor, future row, portfolio,
or order-routing object. Signals must match the current bar timestamp, symbol,
and strategy name.

For the built-in moving-average strategy, construction fails if
`strategy_history_limit` is smaller than `slow_window`; it cannot silently run
forever without enough retained history. Custom strategies remain responsible
for their own declared warm-up behavior.

The shared engine preserves next-bar-open fills, fees, slippage, precision,
accounting, and risk halts. A final pending signal is explicitly expired because
no later executable bar exists.

## Typed lifecycle

```text
CREATED -> VALIDATED -> RUNNING -> PAUSED -> RUNNING -> COMPLETED
                       |   |         |          |
                       |   +-------> STOPPED <--+
                       +-----------> HALTED <----+
                       +-----------> FAILED <----+
```

Legal controls are start, pause, resume, stop, and manual kill switch. Pause
consumes no event. Resume begins with the next unread event. Stop ends cleanly.
The kill switch latches the shared risk halt and expires any pending signal so
new risk cannot execute. No automatic risk-reducing order is invented.

Illegal transitions raise `SessionStateError`. Repeated stop of a stopped
session and repeated kill of a halted session are idempotent. Stop, kill, or a
first-bar failure can produce a valid zero-bar summary and manifest. Processing
failure rolls back engine-owned bar state, records a stable failure category,
and enters terminal `FAILED`.

## Commands

```powershell
python scripts\run_historical_paper_replay.py
python scripts\run_historical_paper_replay.py --csv-path data\local\SPY_daily.csv
python scripts\run_historical_paper_replay.py --speed 0
python scripts\run_historical_paper_replay.py --pause-after-bars 5
python scripts\run_historical_paper_replay.py --stop-after-bars 8
python scripts\run_historical_paper_replay.py --kill-switch-after-bars 8
python scripts\run_historical_paper_replay.py `
  --speed 0 `
  --random-seed 0 `
  --export-manifest reports\paper-session.json `
  --export-equity-csv reports\paper-equity.csv `
  --export-trades-csv reports\paper-trades.csv `
  --export-rejections-csv reports\paper-rejections.csv `
  --export-risk-events-csv reports\paper-risk-events.csv `
  --log-jsonl logs\paper-session.jsonl
```

`run_paper_replay.py` remains a compatible alias. Speed is between 0 and 60
seconds per event; zero runs immediately. Unit tests inject a sleeper and never
use wall-clock delays.

## Structured local events

JSON-lines events use event schema `1.0.0` and a selected primitive field set.
The taxonomy includes:

```text
session_created, data_validated, session_started, bar_received,
signal_generated, intent_created, risk_accepted, risk_rejected, fill_created,
portfolio_updated, session_paused, session_resumed, kill_switch_activated,
session_stopped, session_completed, session_failed, session_halted,
pending_signal_expired
```

Relevant events include session/event IDs, event timestamp, symbol, strategy,
intent/fill IDs, typed risk reasons, equity, exposure, drawdown, and halt state.
The logger does not serialize arbitrary domain objects. Rotation is bounded at
2 MB with two backups by default, and at least one backup is required so the
size bound is effective. The CLI reserves the primary filename plus `.1` and
`.2` before opening the sink and rejects aliases or collisions with input data
and selected reports.

Event delivery is best-effort after financial state commits. A write, flush, or
close failure produces one typed `event_sink_failure` warning without rolling
back or splitting financial state. Logs inside the repository are accepted only
under ignored `logs/`. Repository-root `.pytest-*` and `.pytest_*` scratch
directories are accepted only as ignored automated-test workspaces.

## Manifest and artifacts

Schema `1.2.0` records content hash, configuration, versions, seed, event range,
final state, halt/failure reasons, benchmarks, rejections, and safe artifact
basenames. Reports are atomic and belong under ignored `reports/`. Session
checkpoints remain unimplemented; the reserved `checkpoints/` tree is ignored.

## Limits

Replay is single-symbol and in-process. There is no restart checkpoint,
external pause control, broker clock, order acknowledgement, partial fill,
reconciliation, corporate-action model, database, or daemon.

Results are hypothetical, committed sample data is synthetic, no profitability
is claimed, and nothing here is financial advice.
