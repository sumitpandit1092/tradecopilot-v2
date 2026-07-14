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
from services.indicators import calculate_ema_series

import services.strategy_session_breakout as session_breakout
import services.strategy_ema_cross_retest as ema_cross_retest


# Daily/H4/H1 HTF bias is still computed via timeframe_engine.py --
# only the "entry" timeframe changes. This is the SMC (structure/
# liquidity/FVG/OB) engine, backtested separately from the strategies
# below.
#
# M15 and M30 dropped: despite covering much longer, more mixed-regime
# backtest windows (82 and 156 days vs M3/M5's TradingView-anonymous-
# capped 15-20 days) both came back net-negative (M15: -$64.54, 29.73%
# WR; M30: -$87.23, 28.95% WR). M1 also tried and dropped earlier
# (net-negative once spread/slippage were factored in). M3 dropped
# last: net-positive but the weaker of the two survivors (+$25.34,
# 37.04% WR vs M5's +$147.01, 48.57% WR) on the same backtest run --
# M5 alone is the one timeframe with a real, consistent edge.
SCAN_TIMEFRAMES = {
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
        # Position cap: at most 2 live SMC trades per timeframe, one
        # direction only (no hedging) -- see ExecutionEngine.open_trade.
        trade_record = executor.open_trade(
            signal=signal, entry=entry, risk=risk,
            timeframe=label, max_positions=2, single_side=True,
        )

        if trade_record:
            _log(f"[SMC/{label}] Trade #{trade_record['id']} {trade_record['status']} ({trade_record['entry_type']})")
            router.fire_signal(signal, entry, risk, timeframe=f"SMC/{label}")
        else:
            _log(f"[SMC/{label}] Position cap reached (2/side) or opposite side live -- skipping to avoid overtrading.")


def _scan_extra_strategies(executor, router, last_seen_extra):
    """
    FIXED: this used to read m15/m5 out of `candles_cache`, which is
    only populated for whatever's currently in SMC's SCAN_TIMEFRAMES --
    so when M15 was dropped from SMC's own rotation (M3/M5, now M5
    only), `candles_cache.get("M15")` silently went permanently None
    and Session_Breakout (needs_15m=True) has been skipping every
    cycle ever since, with no error to notice it by. These strategies'
    data needs have nothing to do with what timeframe SMC happens to be
    scanning, so this now fetches M5 always, and M15 lazily (only if a
    registered strategy actually declares needs_15m=True), independent
    of SCAN_TIMEFRAMES entirely.
    """

    m5 = get_xauusd_candles(interval=Interval.in_5_minute, n_bars=100)

    if not m5:
        return

    latest_m5_time = m5[-1]["time"]

    m15 = None  # fetched lazily below, only if something actually needs it

    for name, strategy_fn, needs_15m in EXTRA_STRATEGIES:
        if last_seen_extra.get(name) == latest_m5_time:
            continue
        last_seen_extra[name] = latest_m5_time

        try:
            if needs_15m:
                if m15 is None:
                    m15 = get_xauusd_candles(interval=Interval.in_15_minute, n_bars=100)
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

        # Tagged "M5" (not `name`) so refresh_open_trades() checks it
        # against the right resolution -- these strategies enter on M5
        # candle closes, same as the SMC/M5 path.
        trade_record = executor.open_trade(signal=signal, entry=entry, risk=risk, timeframe="M5")

        if trade_record:
            _log(f"[{name}] Trade #{trade_record['id']} {trade_record['status']} ({trade_record['entry_type']})")
            router.fire_signal(signal, entry, risk, timeframe=name)
        else:
            _log(f"[{name}] Duplicate setup -- already have a matching OPEN/PENDING trade, skipping alert.")


def _scan_ema_cross_retest(executor, router, last_seen_ema_cross):
    """
    EMA 20/50 Cross-Retest, M5 only -- backtested M3/M5/M15/M30/H1, only
    M5 was profitable with the strategy's own close-only exit as
    specified (+$122.03); a 2.0x wick-based hard-loss-cap variant helped
    M15/M30 but made M5 worse (+$51.13), so M5 runs uncapped here.

    Fully self-contained trade management, NOT routed through
    refresh_open_trades()/update_trade() (see SELF_MANAGED_STRATEGIES in
    execution_engine.py) -- this strategy's stop is explicitly
    close-confirmed only ("wick stops kill the trade early"), which the
    standard intrabar-high/low SL checker would violate. Mirrors
    run_ema_cross_retest_backtest.py's per-bar exit logic exactly, just
    driven by the live latest candle instead of a walk-forward loop.
    """

    candles = get_xauusd_candles(interval=Interval.in_5_minute, n_bars=200)

    if not candles or len(candles) < ema_cross_retest.EMA_SLOW + 5:
        return

    latest_candle = candles[-1]
    strategy_name = ema_cross_retest.STRATEGY_NAME

    open_trades = [
        t for t in executor.active_trades()
        if t.get("strategy") == strategy_name
    ]

    # --- Manage any OPEN trade (close-only exit, checked every cycle --
    #     not gated by the new-candle dedup below, so an intrabar TP
    #     wick or a same-candle close-confirmation isn't missed while
    #     waiting for the next 5m candle to fully close). ---
    for trade in open_trades:
        bias = trade["bias"]
        entry = trade["entry"]
        stop_loss = trade["stop_loss"]
        tp = trade["take_profit_2"]
        close = latest_candle["close"]

        hit_tp = (bias == "Bullish" and latest_candle["high"] >= tp) or \
                 (bias == "Bearish" and latest_candle["low"] <= tp)
        if hit_tp:
            closed = executor.close_trade_manual(trade["id"], tp, latest_candle["time"])
            if closed:
                _log(f"[{strategy_name}] Trade #{closed['id']} closed -- TP hit ({closed['result']})")
                router.fire_trade_closed(closed)
            continue

        hit_fixed_sl = (bias == "Bullish" and close <= stop_loss) or \
                        (bias == "Bearish" and close >= stop_loss)
        if hit_fixed_sl:
            closed = executor.close_trade_manual(trade["id"], close, latest_candle["time"])
            if closed:
                _log(f"[{strategy_name}] Trade #{closed['id']} closed -- close-confirmed SL ({closed['result']})")
                router.fire_trade_closed(closed)
            continue

        ema_slow_series = calculate_ema_series(candles, ema_cross_retest.EMA_SLOW)
        ema_now = ema_slow_series[-1] if ema_slow_series else None
        if ema_now is not None:
            invalidated = (bias == "Bullish" and close < ema_now) or \
                          (bias == "Bearish" and close > ema_now)
            if invalidated:
                closed = executor.close_trade_manual(trade["id"], close, latest_candle["time"])
                if closed:
                    _log(f"[{strategy_name}] Trade #{closed['id']} closed -- 50 EMA close-invalidation ({closed['result']})")
                    router.fire_trade_closed(closed)

    # --- Look for a new entry -- one trade at a time, same as the backtest. ---
    latest_candle_time = latest_candle["time"]
    if last_seen_ema_cross.get(strategy_name) == latest_candle_time:
        return
    last_seen_ema_cross[strategy_name] = latest_candle_time

    if any(t.get("strategy") == strategy_name for t in executor.active_trades()):
        return  # still managing the trade closed/opened above this cycle

    try:
        result = ema_cross_retest.build_signal(candles, account_balance=ACCOUNT_BALANCE, risk_percent=RISK_PERCENT)
    except Exception as e:
        _log(f"[{strategy_name}] Error: {e}")
        return

    if result is None:
        return

    signal, entry, risk = result
    _log(f"[{strategy_name}] {signal.get('recommendation')} setup detected (confidence {signal.get('confidence')})")

    trade_record = executor.open_trade(signal=signal, entry=entry, risk=risk, timeframe="M5")

    if trade_record:
        _log(f"[{strategy_name}] Trade #{trade_record['id']} {trade_record['status']} ({trade_record['entry_type']})")
        router.fire_signal(signal, entry, risk, timeframe=strategy_name)


def run_cycle(executor, router, timeframes, last_seen, last_seen_extra, last_seen_ema_cross=None):
    """
    Runs one full scan cycle -- the body of the continuous loop in
    run() below, pulled out so a single-pass/cron-style caller (e.g.
    run_scanner_once.py, for hosting on a schedule that can't keep a
    process resident) can run exactly one cycle and persist
    `last_seen`/`last_seen_extra`/`last_seen_ema_cross` itself between
    invocations.

    Mutates `last_seen`/`last_seen_extra`/`last_seen_ema_cross` in
    place (same dedup role they play inside run()'s loop).
    """

    if last_seen_ema_cross is None:
        last_seen_ema_cross = {}

    # refresh_open_trades() returns any trade touched this cycle --
    # either newly CLOSED (SL/TP2 resolved) or still OPEN with
    # tp1_hit just flipped True (partial close at TP1, stop moved to
    # breakeven). Same list, distinguish by status. Skips
    # SELF_MANAGED_STRATEGIES trades (EMA Cross-Retest) -- those are
    # handled by _scan_ema_cross_retest() below instead.
    touched_trades = executor.refresh_open_trades()
    for trade in touched_trades:
        if trade["status"] == "CLOSED":
            _log(f"Trade #{trade['id']} closed -- {trade['result']}")
            router.fire_trade_closed(trade)
        elif trade.get("tp1_hit"):
            _log(f"Trade #{trade['id']} hit TP1 -- stop moved to breakeven")
            router.fire_trade_tp1_hit(trade)

    macro = _get_macro()

    for label, tf_interval in timeframes.items():
        candles = get_xauusd_candles(interval=tf_interval, n_bars=100)
        _scan_smc_timeframe(label, candles, executor, router, macro, last_seen)

    _scan_extra_strategies(executor, router, last_seen_extra)
    _scan_ema_cross_retest(executor, router, last_seen_ema_cross)


def run(poll_interval=None, router=None, executor=None, timeframes=None):
    """
    Continuously polls the market on a timer and runs 3 independent
    signal sources every cycle, all XAUUSD/M5 only:

    - SMC (structure/liquidity/FVG/OB) across SCAN_TIMEFRAMES (M5 only
      -- see that constant's comment for why M1/M3/M15/M30 were tried
      and dropped), gated on a real 2-of-3 sniper confluence
      requirement and a fast H1-only HTF alignment check.
    - Session Breakout -- direct market entry at breakout confirmation
      (its original "wait for a retest" design was backtested and
      found to systematically filter out the strongest continuations;
      fixed to enter immediately instead). Runs on its own M5/M15 fetch,
      independent of SMC's SCAN_TIMEFRAMES (see _scan_extra_strategies).
    - EMA 20/50 Cross-Retest -- asymmetric entry (3rd retest for longs,
      1st for shorts), close-confirmed-only exit. Backtested across
      M3/M5/M15/M30/H1; only M5 was profitable with the pure close-only
      exit as specified, so it runs M5-only here (see
      _scan_ema_cross_retest). Self-manages its own trades -- excluded
      from refresh_open_trades()'s standard wick-based SL/TP check (see
      SELF_MANAGED_STRATEGIES in execution_engine.py), since that would
      contradict its explicitly close-only stop design.

    EMA Pullback, BB Reversion, and Fib Golden Zone Pullback were all
    backtested (see project history) but are NOT wired in here --
    EMA Pullback/BB Reversion underperformed, and Fib Golden Zone's
    backtest sample was too small to trust yet.

    All three share one ExecutionEngine (one trade journal -- no
    duplicate-trade blocking for the broadcast strategies; every
    qualifying signal opens its own trade, since this broadcasts to
    many subscribers rather than managing one account) and one
    SignalRouter (Telegram), so every alert is tagged with which system
    fired it.
    """

    interval = poll_interval or SCAN_INTERVAL_SECONDS
    executor = executor or ExecutionEngine()
    router = router or SignalRouter()
    timeframes = timeframes or SCAN_TIMEFRAMES

    last_seen = {}
    last_seen_extra = {}
    last_seen_ema_cross = {}

    _log(
        f"TradeCopilot Scanner started -- polling every {interval}s. "
        f"SMC across {list(timeframes.keys())}, plus {[s[0] for s in EXTRA_STRATEGIES]} "
        f"and {ema_cross_retest.STRATEGY_NAME} (M5) (XAUUSD)"
    )

    while True:
        try:
            run_cycle(executor, router, timeframes, last_seen, last_seen_extra, last_seen_ema_cross)
        except Exception as e:
            _log(f"Error in scan loop: {e}")

        time.sleep(interval)


if __name__ == "__main__":
    run()
