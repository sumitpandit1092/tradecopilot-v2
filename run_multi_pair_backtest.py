import os
import traceback

from tvDatafeed import Interval

from config import ACCOUNT_BALANCE, RISK_PERCENT
from services.market_data import INSTRUMENTS, get_instrument_candles
from services.backtester import run_backtest
from services.multi_backtester import run_strategy_backtest
import services.strategy_session_breakout as session_breakout
import services.strategy_ema_pullback as ema_pullback
import services.strategy_fib_golden_zone as fib
import run_fib_backtest

"""
Runs every strategy across every relevant timeframe for the 3 new
pairs (XAGUSD, EURJPY, GBPJPY) -- deliberately does NOT touch XAUUSD or
any of its existing journals/config. Each strategy keeps its own
natural timeframe design instead of being force-fit onto a shared
"all timeframes" sweep:

- SMC (structure/liquidity/FVG/OB): swept across M3/M5/M15/M30, since
  the best entry timeframe is instrument-specific (gold's turned out
  to be M5 -- no reason to assume that transfers to silver or a JPY
  cross without checking).
- Session Breakout, EMA Pullback: fixed M15 (context) + M5 (entry) by
  design, run once each.
- Fib Golden Zone Pullback: fixed H1 (anchor) + M5 (execution) by
  design, run once, with the pair's own pip_size threaded through.

Each run gets its own dedicated journal file
(backtest_journal_{pair}_{strategy_or_timeframe}.json) so nothing
collides with the gold journals or with each other.
"""

NEW_PAIRS = ["XAGUSD", "EURJPY", "GBPJPY"]

SMC_TIMEFRAMES = {
    "M3": Interval.in_3_minute,
    "M5": Interval.in_5_minute,
    "M15": Interval.in_15_minute,
    "M30": Interval.in_30_minute,
}

results = {}  # pair -> {label: result_dict_or_error_string}


def _row(label, summary, extra=""):
    resolved_wr = summary.get("resolved_winrate", "-")
    wins = summary["summary"]["wins"]
    partial = summary["summary"]["partial_wins"]
    losses = summary["summary"]["losses"]
    pnl = summary["summary"]["net_pnl"]
    signals = summary["signals_fired"]
    print(f"  {label:<16} signals={signals:<5} W={wins:<3} P={partial:<3} L={losses:<3} "
          f"WR={resolved_wr}%  PnL={pnl}  {extra}")


def run_pair(pair):
    print()
    print("#" * 70)
    print(f"# {pair}")
    print("#" * 70)

    cfg = INSTRUMENTS[pair]
    pip_size = cfg["pip_size"]
    pair_results = {}

    print("Fetching HTF (Daily/H4/H1)...")
    daily = get_instrument_candles(pair, interval=Interval.in_daily, n_bars=800, retries=3, retry_delay=2)
    h4 = get_instrument_candles(pair, interval=Interval.in_4_hour, n_bars=3000, retries=3, retry_delay=2)
    h1 = get_instrument_candles(pair, interval=Interval.in_1_hour, n_bars=10000, retries=3, retry_delay=2)
    htf_data = {"Daily": daily, "H4": h4, "H1": h1}
    print(f"  Daily={len(daily)} H4={len(h4)} H1={len(h1)} bars")

    if not h1:
        print(f"No H1 data for {pair} -- skipping entirely.")
        results[pair] = {"error": "no H1 data"}
        return

    # ---------------------------------------------------------------
    # SMC across M3/M5/M15/M30
    # ---------------------------------------------------------------
    print()
    print("SMC (structure/liquidity/FVG/OB):")
    for label, interval in SMC_TIMEFRAMES.items():
        try:
            candles = get_instrument_candles(pair, interval=interval, n_bars=30000, retries=3, retry_delay=2)
            if not candles:
                print(f"  {label:<16} NO DATA")
                pair_results[f"SMC_{label}"] = {"error": "no data"}
                continue

            journal = f"backtest_journal_{pair}_SMC_{label}.json"
            if os.path.exists(journal):
                os.remove(journal)

            result = run_backtest(
                candles, htf_data, account_balance=ACCOUNT_BALANCE, risk_percent=RISK_PERCENT,
                entry_label=label, lookback=100, log_file=journal, progress_every=0,
                instrument=pair,
            )
            pair_results[f"SMC_{label}"] = result
            _row(f"SMC/{label}", result, extra=f"({len(candles)} bars)")
        except Exception as e:
            print(f"  {label:<16} ERROR: {e}")
            traceback.print_exc()
            pair_results[f"SMC_{label}"] = {"error": str(e)}

    # ---------------------------------------------------------------
    # Session Breakout + EMA Pullback (M15 + M5, fixed by design)
    # ---------------------------------------------------------------
    print()
    print("Fixed-timeframe strategies:")

    m15 = get_instrument_candles(pair, interval=Interval.in_15_minute, n_bars=30000, retries=3, retry_delay=2)
    m5 = get_instrument_candles(pair, interval=Interval.in_5_minute, n_bars=30000, retries=3, retry_delay=2)
    print(f"  M15={len(m15)} M5={len(m5)} bars")

    for name, strategy_fn in (("Session_Breakout", session_breakout.build_signal),
                               ("EMA_Pullback", ema_pullback.build_signal)):
        try:
            if not m5 or not m15:
                print(f"  {name:<16} NO DATA")
                pair_results[name] = {"error": "no data"}
                continue

            journal = f"backtest_journal_{pair}_{name}.json"
            if os.path.exists(journal):
                os.remove(journal)

            result = run_strategy_backtest(
                strategy_fn, m5, m15_candles=m15,
                account_balance=ACCOUNT_BALANCE, risk_percent=RISK_PERCENT,
                needs_15m=True, log_file=journal, progress_every=0,
                instrument=pair,
            )
            pair_results[name] = result
            _row(name, result)
        except Exception as e:
            print(f"  {name:<16} ERROR: {e}")
            traceback.print_exc()
            pair_results[name] = {"error": str(e)}

    # ---------------------------------------------------------------
    # Fib Golden Zone Pullback (H1 + M5, fixed by design)
    # ---------------------------------------------------------------
    try:
        if not m5 or not h1:
            print("  Fib_Golden_Zone   NO DATA")
            pair_results["Fib_Golden_Zone"] = {"error": "no data"}
        else:
            journal = f"backtest_journal_{pair}_Fib_Golden_Zone.json"
            if os.path.exists(journal):
                os.remove(journal)

            result = run_fib_backtest.run(
                h1, m5, ACCOUNT_BALANCE, RISK_PERCENT, journal,
                progress_every=0, instrument=pair, pip_size=pip_size,
            )
            pair_results["Fib_Golden_Zone"] = result
            _row("Fib_Golden_Zone", result, extra=f"(pip_size={pip_size})")
    except Exception as e:
        print(f"  Fib_Golden_Zone   ERROR: {e}")
        traceback.print_exc()
        pair_results["Fib_Golden_Zone"] = {"error": str(e)}

    results[pair] = pair_results


def main():
    for pair in NEW_PAIRS:
        run_pair(pair)

    print()
    print("=" * 70)
    print("SUMMARY -- all pairs, all strategies")
    print("=" * 70)
    for pair, pair_results in results.items():
        print(f"\n{pair}:")
        for label, r in pair_results.items():
            if "error" in r:
                print(f"  {label:<18} ERROR/NO DATA: {r['error']}")
                continue
            s = r["summary"]
            print(f"  {label:<18} signals={r['signals_fired']:<5} "
                  f"WR={r['resolved_winrate']}%  PnL={s['net_pnl']}")


if __name__ == "__main__":
    main()
