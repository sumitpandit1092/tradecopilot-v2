import time
from datetime import datetime

from tvDatafeed import Interval

from config import (
    SCAN_INTERVAL_SECONDS, ENABLE_MACRO_CONTEXT, ACCOUNT_BALANCE, RISK_PERCENT,
    PENDING_MAX_WAIT_SECONDS,
)
from services.market_data import get_xauusd_candles
from services.signal_engine import build_signal
from services.entry_engine import build_entry
from services.risk_engine import build_risk_plan
from services.execution_engine import ExecutionEngine
from services.macro_context import build_macro_context
from services.router import SignalRouter

import services.strategy_session_breakout as session_breakout


# Scanned every cycle, fastest first so a quick M3/M5 setup isn't
# sitting behind a slower fetch. Daily/H4/H1 HTF bias is still computed
# for every one of these via timeframe_engine.py -- only the "entry"
# timeframe changes. This is the SMC (structure/liquidity/FVG/OB)
# engine, backtested separately from the strategies below.
#
# M15 dropped (backtested as the weak spot: -$65.14, 31.3% WR, just
# under its 33.3% breakeven). M1 also tried and dropped: high signal
# frequency (24.5/day) but win rate sat right at breakeven and turned
# net-negative (-$201.05 over 8 days) once spread/slippage were
# factored in -- worse than M3/M5, which are both showing a real edge.
SCAN_TIMEFRAMES = {
    "M3": Interval.in_3_minute,
    "M5": Interval.in_5_minute,
}

# Rule-based indicator strategies, run in parallel with SMC above.
# EMA Pullback and BB Reversion were tried and dropped -- backtested as
# net losers even after execution-engine corrections. Session Breakout
# stayed after its retest-entry bug was found and fixed (direct entry
# at breakout confirmation instead of waiting for a retracement).
# `needs_15m` controls whether the strategy function is called as
# fn(m15, m5, ...) or fn(m5, ...).
EXTRA_STRATEGIES = [
    ("Session_Breakout", session_breakout.build_signal, True),
]


LOG_FILE = "scanner.log"


def _log(message):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line)

    # Persisted so the dashboard (a separate process) can tail real
    # scanner activity -- stdout alone only exists for whatever
    # process/terminal launched this one.
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _get_macro():
    if not ENABLE_MACRO_CONTEXT:
        return None
    try:
        return build_macro_context()
    except Exception as e:
        _log(f"Macro context unavailable: {e}")
        return None


def _scan_smc_timeframe(label, candles, executor, router, macro, last_seen):
    if not candles:
        _log(f"[{label}] No market data -- skipping this cycle.")
        return

    latest_candle = candles[-1]

    # Checked every cycle, not gated by the new-candle dedup below --
    # a resting limit order can fill or get invalidated intrabar, and
    # shouldn't have to wait for the next candle close to notice. This
    # also covers PENDING orders placed by the extra strategies below,
    # since refresh_pending() doesn't care which strategy opened them.
    filled, cancelled = executor.refresh_pending(latest_candle, max_wait_seconds=PENDING_MAX_WAIT_SECONDS)

    for trade in filled:
        _log(f"[{label}] Pending order #{trade['id']} FILLED at {trade['limit_entry']}")
        router.fire_trade_filled(trade, timeframe=label)

    for trade in cancelled:
        _log(f"[{label}] Pending order #{trade['id']} CANCELLED (invalidated before fill)")
        router.fire_trade_cancelled(trade, timeframe=label)

    latest_candle_time = latest_candle["time"]

    if latest_candle_time == last_seen.get(label):
        return

    last_seen[label] = latest_candle_time

    signal = build_signal(candles, macro=macro, entry_label=label)

    if signal.get("error"):
        _log(f"[{label}] {signal['error']}")
        return

    entry = build_entry(signal, candles)

    risk = build_risk_plan(
        candles, signal, entry,
        account_balance=ACCOUNT_BALANCE, risk_percent=RISK_PERCENT,
    )

    trade_ready = (
        signal.get("entry_allowed", False)
        and entry.get("valid", False)
        and risk.get("valid", False)
    )

    _log(
        f"[SMC/{label}] Candle {latest_candle_time} -- {signal.get('recommendation')} "
        f"(confidence {signal.get('confidence')}) -- "
        f"{'READY TO EXECUTE' if trade_ready else 'WAIT'}"
    )

    if trade_ready:
        trade_record = executor.open_trade(signal=signal, entry=entry, risk=risk)

        if trade_record:
            _log(f"[SMC/{label}] Trade #{trade_record['id']} {trade_record['status']} ({trade_record['entry_type']})")
            router.fire_signal(signal, entry, risk, timeframe=f"SMC/{label}")
        else:
            _log(f"[SMC/{label}] Duplicate setup -- already have a matching OPEN/PENDING trade, skipping alert.")


def _scan_extra_strategies(executor, router, candles_cache, last_seen_extra):
    m15 = candles_cache.get("M15")
    m5 = candles_cache.get("M5")

    if not m5:
        return

    latest_m5_time = m5[-1]["time"]

    for name, strategy_fn, needs_15m in EXTRA_STRATEGIES:
        if last_seen_extra.get(name) == latest_m5_time:
            continue
        last_seen_extra[name] = latest_m5_time

        try:
            if needs_15m:
                if not m15:
                    continue
                result = strategy_fn(m15, m5, account_balance=ACCOUNT_BALANCE, risk_percent=RISK_PERCENT)
            else:
                result = strategy_fn(m5, account_balance=ACCOUNT_BALANCE, risk_percent=RISK_PERCENT)
        except Exception as e:
            _log(f"[{name}] Error: {e}")
            continue

        if result is None:
            continue

        signal, entry, risk = result

        _log(
            f"[{name}] {signal.get('recommendation')} setup detected "
            f"(confidence {signal.get('confidence')})"
        )

        trade_record = executor.open_trade(signal=signal, entry=entry, risk=risk)

        if trade_record:
            _log(f"[{name}] Trade #{trade_record['id']} {trade_record['status']} ({trade_record['entry_type']})")
            router.fire_signal(signal, entry, risk, timeframe=name)
        else:
            _log(f"[{name}] Duplicate setup -- already have a matching OPEN/PENDING trade, skipping alert.")


def run(poll_interval=None, router=None, executor=None, timeframes=None):
    """
    Continuously polls the market on a timer and runs 2 independent
    signal sources every cycle:

    - SMC (structure/liquidity/FVG/OB) across M3/M5/M15 -- the
      original OR-confluence engine, no session filter (both were
      tried as a stricter "sniper" variant and backtested worse once
      execution accounting was corrected, so reverted).
    - Session Breakout -- direct market entry at breakout confirmation
      (its original "wait for a retest" design was backtested and
      found to systematically filter out the strongest continuations;
      fixed to enter immediately instead).

    Both share one ExecutionEngine (one trade journal -- no duplicate-
    trade blocking; every qualifying signal opens its own trade, since
    this broadcasts to many subscribers rather than managing one
    account) and one SignalRouter (Telegram), so every alert is tagged
    with which system fired it.
    """

    interval = poll_interval or SCAN_INTERVAL_SECONDS
    executor = executor or ExecutionEngine()
    router = router or SignalRouter()
    timeframes = timeframes or SCAN_TIMEFRAMES

    last_seen = {}
    last_seen_extra = {}

    _log(
        f"TradeCopilot Scanner started -- polling every {interval}s. "
        f"SMC across {list(timeframes.keys())}, plus {[s[0] for s in EXTRA_STRATEGIES]} (XAUUSD)"
    )

    while True:
        try:
            closed_trades = executor.refresh_open_trades()
            for trade in closed_trades:
                _log(f"Trade #{trade['id']} closed -- {trade['result']}")
                router.fire_trade_closed(trade)

            macro = _get_macro()

            candles_cache = {}
            for label, tf_interval in timeframes.items():
                candles = get_xauusd_candles(interval=tf_interval, n_bars=100)
                candles_cache[label] = candles
                _scan_smc_timeframe(label, candles, executor, router, macro, last_seen)

            _scan_extra_strategies(executor, router, candles_cache, last_seen_extra)

        except Exception as e:
            _log(f"Error in scan loop: {e}")

        time.sleep(interval)


if __name__ == "__main__":
    run()
