import os
import sys

from tvDatafeed import Interval

from config import ACCOUNT_BALANCE, RISK_PERCENT
from services.market_data import get_xauusd_candles
from services.backtester import run_backtest


ENTRY_TIMEFRAMES = {
    "M5": Interval.in_5_minute,
}


def _print_range(label, candles):
    if candles:
        print(f"{label}: {len(candles)} bars ({candles[0]['time']} -> {candles[-1]['time']})")
    else:
        print(f"{label}: NO DATA")


def _print_results(label, candles, result):
    summary = result["summary"]

    print("=" * 50)
    print(f"BACKTEST RESULTS -- XAUUSD {label}")
    print("=" * 50)
    print(f"Period covered:            {candles[0]['time']} -> {candles[-1]['time']}")
    print(f"Trading days observed:     {result['trading_days']}")
    print(f"Candles scanned:           {result['candles_scanned']}")
    print()
    print(f"Confidence >= 85 count:    {result['high_confidence_count']}  ({result['high_confidence_per_day']}/day)")
    print(f"Trade-ready signals:       {result['signals_fired']}  ({result['signals_fired_per_day']}/day)")
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
    requested = [a.upper() for a in sys.argv[1:]] or list(ENTRY_TIMEFRAMES.keys())
    requested = [tf for tf in requested if tf in ENTRY_TIMEFRAMES]

    print("Fetching historical HTF data (Daily/H4/H1, shared across all entry timeframes)...")
    daily = get_xauusd_candles(interval=Interval.in_daily, n_bars=800, retries=3, retry_delay=2)
    h4 = get_xauusd_candles(interval=Interval.in_4_hour, n_bars=3000, retries=3, retry_delay=2)
    h1 = get_xauusd_candles(interval=Interval.in_1_hour, n_bars=10000, retries=3, retry_delay=2)

    htf_data = {"Daily": daily, "H4": h4, "H1": h1}

    _print_range("Daily", daily)
    _print_range("H4", h4)
    _print_range("H1", h1)
    print()

    results = {}

    for label in requested:
        interval = ENTRY_TIMEFRAMES[label]

        print(f"Fetching {label} candles (TradingView anonymous access caps lower-timeframe depth)...")
        candles = get_xauusd_candles(interval=interval, n_bars=30000, retries=3, retry_delay=2)
        _print_range(label, candles)

        if not candles:
            print(f"Skipping {label} -- no data available.\n")
            continue

        print(f"Running walk-forward backtest on {label} ({len(candles) - 100} candles to scan)...")

        # Each run is a fresh, independent simulation -- clear any
        # journal left over from a previous run so old trades don't
        # bleed into this one's stats.
        journal_file = f"backtest_journal_{label}.json"
        if os.path.exists(journal_file):
            os.remove(journal_file)

        result = run_backtest(
            candles, htf_data,
            account_balance=ACCOUNT_BALANCE, risk_percent=RISK_PERCENT,
            entry_label=label, lookback=100, log_file=journal_file,
        )
        results[label] = (candles, result)
        print()

    print()
    for label, (candles, result) in results.items():
        _print_results(label, candles, result)


if __name__ == "__main__":
    main()
