import os

from tvDatafeed import Interval

from config import ACCOUNT_BALANCE, RISK_PERCENT
from services.market_data import INSTRUMENTS, get_instrument_candles
from services.multi_backtester import run_strategy_backtest
import services.strategy_session_breakout as session_breakout
import services.strategy_ema_pullback as ema_pullback
import run_fib_backtest
from run_multi_pair_backtest import run_pair, _row, results

"""
One-off resume script: the first multi-pair batch died partway through
(session teardown, not a code bug -- see the surviving journal files).
XAGUSD finished fully; EURJPY finished its 4 SMC timeframes but its
Session_Breakout journal got corrupted mid-write (process killed
during a save) and EMA_Pullback/Fib never ran; GBPJPY never started.

This reruns only what's missing: EURJPY's 3 remaining pieces, then all
of GBPJPY via the normal run_pair() from run_multi_pair_backtest.py.
"""


def resume_eurjpy():
    pair = "EURJPY"
    cfg = INSTRUMENTS[pair]
    print("#" * 70)
    print(f"# {pair} (resume -- Session_Breakout, EMA_Pullback, Fib_Golden_Zone only)")
    print("#" * 70)

    h1 = get_instrument_candles(pair, interval=Interval.in_1_hour, n_bars=10000, retries=3, retry_delay=2)
    m15 = get_instrument_candles(pair, interval=Interval.in_15_minute, n_bars=30000, retries=3, retry_delay=2)
    m5 = get_instrument_candles(pair, interval=Interval.in_5_minute, n_bars=30000, retries=3, retry_delay=2)
    print(f"H1={len(h1)} M15={len(m15)} M5={len(m5)} bars")

    pair_results = {}

    for name, strategy_fn in (("Session_Breakout", session_breakout.build_signal),
                               ("EMA_Pullback", ema_pullback.build_signal)):
        journal = f"backtest_journal_{pair}_{name}.json"
        if os.path.exists(journal):
            os.remove(journal)
        result = run_strategy_backtest(
            strategy_fn, m5, m15_candles=m15,
            account_balance=ACCOUNT_BALANCE, risk_percent=RISK_PERCENT,
            needs_15m=True, log_file=journal, progress_every=0, instrument=pair,
        )
        pair_results[name] = result
        _row(name, result)

    journal = f"backtest_journal_{pair}_Fib_Golden_Zone.json"
    if os.path.exists(journal):
        os.remove(journal)
    result = run_fib_backtest.run(
        h1, m5, ACCOUNT_BALANCE, RISK_PERCENT, journal,
        progress_every=0, instrument=pair, pip_size=cfg["pip_size"],
    )
    pair_results["Fib_Golden_Zone"] = result
    _row("Fib_Golden_Zone", result, extra=f"(pip_size={cfg['pip_size']})")

    return pair_results


eurjpy_resumed = resume_eurjpy()
print()
run_pair("GBPJPY")

print()
print("=" * 70)
print("RESUME COMPLETE")
print("=" * 70)
print("EURJPY resumed pieces:", list(eurjpy_resumed.keys()))
print("GBPJPY:", list(results.get("GBPJPY", {}).keys()))
