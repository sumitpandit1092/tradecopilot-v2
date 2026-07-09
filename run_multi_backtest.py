import os

from tvDatafeed import Interval

from config import ACCOUNT_BALANCE, RISK_PERCENT
from services.market_data import get_xauusd_candles
from services.multi_backtester import run_strategy_backtest
import services.strategy_session_breakout as session_breakout
import services.strategy_ema_pullback as ema_pullback


STRATEGIES = [
    ("Session_Breakout", session_breakout.build_signal, True),
    ("EMA_Pullback", ema_pullback.build_signal, True),
]


def _print_results(name, result):
    summary = result["summary"]

    print("=" * 50)
    print(f"BACKTEST RESULTS -- {name}")
    print("=" * 50)
    print(f"Trading days observed:     {result['trading_days']}")
    print(f"Candles scanned:           {result['candles_scanned']}")
    print()
    print(f"Signals fired:             {result['signals_fired']}  ({result['signals_fired_per_day']}/day)")
    print()
    print(f"Total orders placed:       {summary['total_trades']}")
    print(f"  Filled & resolved:       {summary['wins'] + summary['partial_wins'] + summary['losses']}")
    print(f"  Wins (full TP2):         {summary['wins']}")
    print(f"  Partial wins (TP1 hit):  {summary['partial_wins']}  (TP1 hit on {summary['tp1_hits']} trades total)")
    print(f"  Losses:                  {summary['losses']}")
    print(f"  Still open at end:       {result['trades_still_open']}")
    print(f"  Still pending (unfilled):{result['trades_pending']}")
    print(f"  Cancelled (never filled):{result['trades_cancelled']}")
    print(f"Win rate (resolved only):  {result['resolved_winrate']}%")
    print(f"Net PnL:                   {summary['net_pnl']}")
    print("=" * 50)
    print()


def main():
    print("Fetching historical data...")
    m15 = get_xauusd_candles(interval=Interval.in_15_minute, n_bars=30000, retries=3, retry_delay=2)
    m5 = get_xauusd_candles(interval=Interval.in_5_minute, n_bars=30000, retries=3, retry_delay=2)

    if m15:
        print(f"M15: {len(m15)} bars ({m15[0]['time']} -> {m15[-1]['time']})")
    if m5:
        print(f"M5:  {len(m5)} bars ({m5[0]['time']} -> {m5[-1]['time']})")

    if not m5:
        print("No M5 data available -- cannot run backtest.")
        return

    results = {}

    for name, strategy_fn, needs_15m in STRATEGIES:
        journal_file = f"backtest_journal_{name}.json"
        if os.path.exists(journal_file):
            os.remove(journal_file)

        print(f"Running walk-forward backtest for {name}...")

        result = run_strategy_backtest(
            strategy_fn, m5, m15_candles=m15 if needs_15m else None,
            account_balance=ACCOUNT_BALANCE, risk_percent=RISK_PERCENT,
            needs_15m=needs_15m, log_file=journal_file, progress_every=1000,
        )
        results[name] = result
        print()

    print()
    for name, result in results.items():
        _print_results(name, result)


if __name__ == "__main__":
    main()
