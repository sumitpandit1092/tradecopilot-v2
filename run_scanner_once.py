"""
Single-pass scanner run, for hosting on a schedule (e.g. GitHub
Actions) instead of a process that stays resident 24/7 like
run_scanner.py. Nothing survives between invocations by default, so
this persists to state/ what the continuous scanner would otherwise
keep in memory: the trade journal and the per-timeframe "last seen
candle" dedup markers (services.scanner.run_cycle's last_seen /
last_seen_extra) -- without that, every scheduled run would re-alert
on whatever candle is currently latest.
"""

import json
import os

from services.execution_engine import ExecutionEngine
from services.router import SignalRouter
from services.scanner import run_cycle, SCAN_TIMEFRAMES, _log

STATE_DIR = "state"
JOURNAL_FILE = os.path.join(STATE_DIR, "trade_journal.json")
SCAN_STATE_FILE = os.path.join(STATE_DIR, "scan_state.json")


def _load_scan_state():
    try:
        with open(SCAN_STATE_FILE, "r") as f:
            data = json.load(f)
        return data.get("last_seen", {}), data.get("last_seen_extra", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, {}


def _save_scan_state(last_seen, last_seen_extra):
    with open(SCAN_STATE_FILE, "w") as f:
        json.dump({"last_seen": last_seen, "last_seen_extra": last_seen_extra}, f, indent=2)


def main():
    os.makedirs(STATE_DIR, exist_ok=True)

    executor = ExecutionEngine(log_file=JOURNAL_FILE)
    router = SignalRouter()
    last_seen, last_seen_extra = _load_scan_state()

    _log("Single-pass scan starting.")

    try:
        run_cycle(executor, router, SCAN_TIMEFRAMES, last_seen, last_seen_extra)
    except Exception as e:
        _log(f"Error in scan cycle: {e}")
    finally:
        _save_scan_state(last_seen, last_seen_extra)

    _log("Single-pass scan complete.")


if __name__ == "__main__":
    main()
