import os

from tvDatafeed import Interval

from config import ACCOUNT_BALANCE, RISK_PERCENT
from services.market_data import get_xauusd_candles
from services.execution_engine import ExecutionEngine
from services.indicators import calculate_ema_series
import services.strategy_ema_cross_retest as ema_strat

"""
Dedicated backtest for the EMA 20/50 Cross-Retest strategy. Kept
separate from every other runner (SMC, Session Breakout, EMA Pullback,
Fib Golden Zone, multi-pair) -- this one needs a fully custom
trade-management loop instead of the shared ExecutionEngine.update_trade()
SL/TP2 checker, because its exit rule is explicitly close-confirmed
only ("wick stops kill the trade early"), and its stop moves with the
live 50 EMA rather than sitting at a fixed price set at entry.
Reusing update_trade()'s intrabar high/low SL check here would
contradict the strategy's own stated design.
"""

LOOKBACK = 120  # >= EMA_SLOW(50) + enough history to find a cross and count retests


def run(candles, account_balance, risk_percent, log_file, lookback=LOOKBACK,
        progress_every=1000, instrument="XAUUSD", pip_size=ema_strat.PIP_SIZE,
        hard_cap_mult=None):
    """
    `hard_cap_mult` (None by default -- the original, pure close-only
    behavior already backtested): when set, adds a WICK-based backstop
    at `hard_cap_mult` x the trade's own nominal SL distance beyond
    entry, e.g. 2.0 caps worst-case loss around 2R. This is NOT part of
    the original spec -- the spec is explicit that stops are
    close-confirmed only ("wick stops kill the trade early"). It exists
    because the backtest showed WHY that matters: on M15/M30, the
    close-confirmed exit let losses overshoot to ~2-3x the nominal 1R
    (a big bar can travel a long way before a close finally confirms
    the breakdown). This keeps close-only as the PRIMARY exit for
    ordinary noise -- it only fires on the rare bar where price wicks
    catastrophically far past the intended risk, same "SL takes
    priority over TP if a candle touches both" conservative convention
    used elsewhere in this codebase (services/execution_engine.py).
    """
    executor = ExecutionEngine(log_file=log_file)

    signals_fired = 0
    days_seen = set()
    total = len(candles) - lookback

    for i in range(lookback, len(candles)):
        current_candle = candles[i]
        window = candles[i - lookback + 1: i + 1]
        as_of = window[-1]["time"]
        days_seen.add(as_of.split(" ")[0])

        # --- Manage any OPEN trade first ---
        for trade in executor.active_trades():
            bias = trade["bias"]
            entry = trade["entry"]
            stop_loss = trade["stop_loss"]
            tp = trade["take_profit_2"]
            close = current_candle["close"]

            # 1) Hard-cap wick backstop (opt-in) -- most conservative,
            #    checked first, same priority convention as SL-over-TP
            #    elsewhere in this codebase.
            if hard_cap_mult is not None:
                sl_distance = abs(entry - stop_loss)
                hard_cap = entry - sl_distance * hard_cap_mult if bias == "Bullish" \
                    else entry + sl_distance * hard_cap_mult
                hit_hard_cap = (bias == "Bullish" and current_candle["low"] <= hard_cap) or \
                               (bias == "Bearish" and current_candle["high"] >= hard_cap)
                if hit_hard_cap:
                    executor.close_trade_manual(trade["id"], hard_cap, current_candle["time"])
                    continue

            # 2) TP -- ordinary intrabar wick touch, same as every other
            #    strategy's profit target.
            hit_tp = (bias == "Bullish" and current_candle["high"] >= tp) or \
                     (bias == "Bearish" and current_candle["low"] <= tp)
            if hit_tp:
                executor.close_trade_manual(trade["id"], tp, current_candle["time"])
                continue

            # 3) Fixed SL (20-pip buffer beyond the 50 EMA AT ENTRY),
            #    CLOSE-confirmed only -- a wick through it doesn't count.
            hit_fixed_sl = (bias == "Bullish" and close <= stop_loss) or \
                            (bias == "Bearish" and close >= stop_loss)
            if hit_fixed_sl:
                executor.close_trade_manual(trade["id"], close, current_candle["time"])
                continue

            # 4) Trend-invalidation exit: the LIVE 50 EMA, recomputed
            #    fresh on the current window (it moves every bar, unlike
            #    the fixed SL above) -- close-confirmed only.
            ema_slow_series = calculate_ema_series(window, ema_strat.EMA_SLOW)
            ema_now = ema_slow_series[-1] if ema_slow_series else None
            if ema_now is not None:
                invalidated = (bias == "Bullish" and close < ema_now) or \
                              (bias == "Bearish" and close > ema_now)
                if invalidated:
                    executor.close_trade_manual(trade["id"], close, current_candle["time"])

        # --- Look for a new entry -- one trade at a time, per the spec's
        #     "Cross -> 3rd retest -> close. One trade." framing.
        if not executor.active_trades() and len(window) >= ema_strat.EMA_SLOW + 5:
            result = ema_strat.build_signal(
                window, account_balance=account_balance, risk_percent=risk_percent, pip_size=pip_size,
            )
            if result is not None:
                signal, entry, risk = result
                trade = executor.open_trade(
                    signal=signal, entry=entry, risk=risk, bar_index=i, instrument=instrument,
                )
                if trade:
                    signals_fired += 1

        if progress_every and (i - lookback + 1) % progress_every == 0:
            print(f"  ...scanned {i - lookback + 1}/{total} candles")

    summary = executor.summary()
    trading_days = max(len(days_seen), 1)
    resolved = summary["wins"] + summary["partial_wins"] + summary["losses"]
    resolved_winrate = round((summary["wins"] + summary["partial_wins"]) / resolved * 100, 2) if resolved else 0

    return {
        "candles_scanned": total,
        "trading_days": trading_days,
        "signals_fired": signals_fired,
        "signals_fired_per_day": round(signals_fired / trading_days, 2),
        "trades_still_open": len(executor.active_trades()),
        "resolved_winrate": resolved_winrate,
        "summary": summary,
    }


TIMEFRAMES = {
    "M3": Interval.in_3_minute,
    "M5": Interval.in_5_minute,
    "M15": Interval.in_15_minute,
    "M30": Interval.in_30_minute,
    "H1": Interval.in_1_hour,
}


def main():
    requested = list(TIMEFRAMES.keys())
    results = {}

    for label in requested:
        print(f"Fetching {label} XAUUSD candles...")
        candles = get_xauusd_candles(interval=TIMEFRAMES[label], n_bars=30000, retries=3, retry_delay=2)
        if not candles:
            print(f"  No data for {label} -- skipping.")
            continue
        print(f"  {label}: {len(candles)} bars ({candles[0]['time']} -> {candles[-1]['time']})")

        journal = f"backtest_journal_EMA_Cross_Retest_{label}.json"
        if os.path.exists(journal):
            os.remove(journal)

        print(f"  Running walk-forward backtest for {label}...")
        result = run(candles, ACCOUNT_BALANCE, RISK_PERCENT, journal, progress_every=0)
        results[label] = result

        s = result["summary"]
        print(f"  {label}: signals={result['signals_fired']} ({result['signals_fired_per_day']}/day)  "
              f"W={s['wins']} P={s['partial_wins']} L={s['losses']}  "
              f"WR={result['resolved_winrate']}%  PnL={s['net_pnl']}")
        print()

    print("=" * 70)
    print("SUMMARY -- EMA 20/50 Cross-Retest, XAUUSD, all timeframes")
    print("=" * 70)
    for label, r in results.items():
        s = r["summary"]
        print(f"  {label:<6} signals={r['signals_fired']:<5} WR={r['resolved_winrate']}%  PnL={s['net_pnl']}")


if __name__ == "__main__":
    main()
