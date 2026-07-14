from services.execution_engine import ExecutionEngine


def run_strategy_backtest(strategy_fn, m5_candles, m15_candles=None, account_balance=1000,
                           risk_percent=1.0, lookback_5m=100, lookback_15m=100,
                           log_file="backtest_journal.json", needs_15m=True,
                           max_wait_bars=20, progress_every=1000, instrument="XAUUSD"):
    """
    Generic walk-forward backtester for the indicator-based strategies
    (EMA Pullback, BB Reversion, Session Breakout). Unlike the SMC
    backtester (services/backtester.py) which only ever handles one
    timeframe, this drives everything off 5m candles -- the finest
    granularity all three strategies share -- and, for strategies that
    need HTF context (`needs_15m=True`), maintains a synchronized 15m
    window ending at or before the current 5m timestamp (no lookahead:
    the 15m pointer only ever advances to candles that have already
    closed as of "now").

    Reuses the exact same ExecutionEngine (including PENDING/limit-
    order handling) as the SMC backtester, so results are directly
    comparable and a strategy's own duplicate-trade protection just
    works without extra code here.
    """

    executor = ExecutionEngine(log_file=log_file)

    signals_fired = 0
    days_seen = set()

    m15_idx = 0
    total = len(m5_candles) - lookback_5m

    for i in range(lookback_5m, len(m5_candles)):
        current_candle = m5_candles[i]

        executor.refresh_pending(current_candle, bar_index=i, max_wait_bars=max_wait_bars)

        # Pass the full candle (not just close) so update_trade() can
        # check SL/TP2 against the actual high/low range intrabar.
        for trade in executor.active_trades():
            executor.update_trade(trade["id"], current_candle)

        window5 = m5_candles[i - lookback_5m + 1: i + 1]
        as_of = window5[-1]["time"]
        days_seen.add(as_of.split(" ")[0])

        if needs_15m:
            if not m15_candles:
                continue

            while m15_idx + 1 < len(m15_candles) and m15_candles[m15_idx + 1]["time"] <= as_of:
                m15_idx += 1

            window15 = m15_candles[max(0, m15_idx - lookback_15m + 1): m15_idx + 1]

            if len(window15) < 20 or window15[-1]["time"] > as_of:
                continue

            result = strategy_fn(window15, window5, account_balance=account_balance, risk_percent=risk_percent)
        else:
            result = strategy_fn(window5, account_balance=account_balance, risk_percent=risk_percent)

        if result is not None:
            signal, entry, risk = result
            trade_record = executor.open_trade(signal=signal, entry=entry, risk=risk, bar_index=i, instrument=instrument)
            if trade_record:
                signals_fired += 1

        if progress_every and (i - lookback_5m + 1) % progress_every == 0:
            print(f"  ...scanned {i - lookback_5m + 1}/{total} candles")

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
        "trades_pending": summary["pending"],
        "trades_cancelled": summary["cancelled"],
        "resolved_winrate": resolved_winrate,
        "summary": summary,
    }
