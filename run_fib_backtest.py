import os

from tvDatafeed import Interval

from config import ACCOUNT_BALANCE, RISK_PERCENT
from services.market_data import get_xauusd_candles
from services.execution_engine import ExecutionEngine
import services.strategy_fib_golden_zone as fib

"""
Dedicated walk-forward backtest for the Fib Golden Zone Pullback
strategy -- kept separate from run_multi_backtest.py (Session Breakout,
EMA Pullback, BB Reversion) and run_backtest.py (the SMC engine) so
running this doesn't touch or require any of those, per "hold all the
other strategies for now."

Anchor timeframe is H1 (not the M15 the other indicator strategies
use), so this can't reuse services/multi_backtester.py's window-sync
loop as-is without renaming its `m15_candles` parameter in a way that
would be misleading for every other caller -- simpler to keep this
self-contained, especially since it also needs to pass
session_label/max_per_session/max_consecutive_losses through to
open_trade(), which the shared multi-strategy runner doesn't do.
"""


def run(h1_candles, m5_candles, account_balance, risk_percent, log_file,
        lookback_h1=100, lookback_m5=50, progress_every=1000,
        instrument="XAUUSD", pip_size=fib.PIP_SIZE):
    executor = ExecutionEngine(log_file=log_file)

    signals_fired = 0
    days_seen = set()
    h1_idx = 0
    total = len(m5_candles) - lookback_m5

    for i in range(lookback_m5, len(m5_candles)):
        current_candle = m5_candles[i]

        for trade in executor.active_trades():
            executor.update_trade(trade["id"], current_candle)

        window5 = m5_candles[i - lookback_m5 + 1: i + 1]
        as_of = window5[-1]["time"]
        days_seen.add(as_of.split(" ")[0])

        while h1_idx + 1 < len(h1_candles) and h1_candles[h1_idx + 1]["time"] <= as_of:
            h1_idx += 1

        window_h1 = h1_candles[max(0, h1_idx - lookback_h1 + 1): h1_idx + 1]

        if len(window_h1) < 30 or window_h1[-1]["time"] > as_of:
            continue

        result = fib.build_signal(
            window_h1, window5, account_balance=account_balance, risk_percent=risk_percent, pip_size=pip_size,
        )

        if result is not None:
            signal, entry, risk = result
            trade = executor.open_trade(
                signal=signal, entry=entry, risk=risk, bar_index=i,
                timeframe="M5", session_label=signal.get("session_label"),
                max_per_session=fib.MAX_TRADES_PER_SESSION,
                max_consecutive_losses=fib.MAX_CONSECUTIVE_LOSSES,
                instrument=instrument,
            )
            if trade:
                signals_fired += 1

        if progress_every and (i - lookback_m5 + 1) % progress_every == 0:
            print(f"  ...scanned {i - lookback_m5 + 1}/{total} candles")

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


def main():
    print("Fetching H1 and M5 historical data...")
    h1 = get_xauusd_candles(interval=Interval.in_1_hour, n_bars=5000, retries=3, retry_delay=2)
    m5 = get_xauusd_candles(interval=Interval.in_5_minute, n_bars=30000, retries=3, retry_delay=2)

    if h1:
        print(f"H1: {len(h1)} bars ({h1[0]['time']} -> {h1[-1]['time']})")
    if m5:
        print(f"M5: {len(m5)} bars ({m5[0]['time']} -> {m5[-1]['time']})")

    if not m5 or not h1:
        print("Missing H1 or M5 data -- cannot run backtest.")
        return

    journal_file = "backtest_journal_Fib_Golden_Zone.json"
    if os.path.exists(journal_file):
        os.remove(journal_file)

    print("Running walk-forward backtest for Fib Golden Zone Pullback...")
    result = run(h1, m5, ACCOUNT_BALANCE, RISK_PERCENT, journal_file)

    summary = result["summary"]
    print()
    print("=" * 50)
    print("BACKTEST RESULTS -- Fib Golden Zone Pullback")
    print("=" * 50)
    print(f"Trading days observed:     {result['trading_days']}")
    print(f"Candles scanned:           {result['candles_scanned']}")
    print()
    print(f"Signals fired:             {result['signals_fired']}  ({result['signals_fired_per_day']}/day)")
    print()
    print(f"Total orders placed:       {summary['total_trades']}")
    print(f"  Wins (full TP2):         {summary['wins']}")
    print(f"  Partial wins (TP1 hit):  {summary['partial_wins']}")
    print(f"  Losses:                  {summary['losses']}")
    print(f"  Still open at end:       {result['trades_still_open']}")
    print(f"Win rate (resolved only):  {result['resolved_winrate']}%")
    print(f"Net PnL:                   {summary['net_pnl']}")
    print("=" * 50)


if __name__ == "__main__":
    main()
