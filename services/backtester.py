from services.signal_engine import build_signal
from services.entry_engine import build_entry
from services.risk_engine import build_risk_plan
from services.execution_engine import ExecutionEngine

CONFIDENCE_THRESHOLD = 85


def run_backtest(candles, htf_data, account_balance, risk_percent,
                  entry_label="M15", lookback=100, log_file="backtest_journal.json",
                  progress_every=500, max_wait_bars=20,
                  max_positions=1, single_side=True, instrument="XAUUSD"):
    """
    Walk-forward backtest over historical `candles` (oldest-first).

    At each step i, only candles[:i+1] and HTF data closed at or before
    that point are visible to the pipeline -- this mirrors what the
    live scanner would have seen in real time, avoiding lookahead bias.

    Trade outcomes are simulated with the exact same ExecutionEngine
    used in production (open_trade/update_trade), checked against each
    subsequent candle's close price -- the same thing the live scanner
    does every poll cycle. Results write to `log_file`, a dedicated
    backtest journal, never the live trade_journal.json.

    Macro (DXY/US10Y) confluence is NOT included here: macro_context.py
    only fetches the *current* correlated-market trend, and there's no
    historical DXY/US10Y series wired up yet, so scoring it into every
    historical bar would leak today's macro state into the past. Live
    signals may therefore score slightly differently than this backtest.
    """

    executor = ExecutionEngine(log_file=log_file)

    scanned = 0
    high_confidence = 0
    signals_fired = 0
    days_seen = set()

    total = len(candles) - lookback

    for i in range(lookback, len(candles)):
        current_candle = candles[i]

        # Pending limit orders can fill or get invalidated intrabar --
        # check that against this candle's actual range before touching
        # anything already OPEN.
        executor.refresh_pending(current_candle, bar_index=i, max_wait_bars=max_wait_bars)

        # Pass the full candle (not just close) so update_trade() can
        # check SL/TP2 against the actual high/low range intrabar.
        for trade in executor.active_trades():
            executor.update_trade(trade["id"], current_candle)

        window = candles[i - lookback + 1: i + 1]
        as_of = window[-1]["time"]
        days_seen.add(as_of.split(" ")[0])

        signal = build_signal(window, macro=None, entry_label=entry_label, htf_data=htf_data, as_of=as_of)

        if signal.get("error"):
            continue

        scanned += 1

        if signal.get("confidence", 0) >= CONFIDENCE_THRESHOLD:
            high_confidence += 1

        entry = build_entry(signal, window)

        risk = build_risk_plan(
            window, signal, entry,
            account_balance=account_balance, risk_percent=risk_percent,
        )

        trade_ready = (
            signal.get("entry_allowed", False)
            and entry.get("valid", False)
            and risk.get("valid", False)
        )

        if trade_ready:
            # SMC opts into position limits: by default at most 1 live
            # trade per entry timeframe (matches the live scanner's cap,
            # tightened from 2 for the same overtrading-prevention
            # reasoning as Session Breakout's cap), one direction only
            # (no hedging). Pass max_positions=None to disable (broadcast
            # every signal) -- used by the cap-isolation experiment.
            trade = executor.open_trade(
                signal=signal, entry=entry, risk=risk, bar_index=i,
                timeframe=entry_label, max_positions=max_positions, single_side=single_side,
                instrument=instrument,
            )
            if trade:
                signals_fired += 1

        if progress_every and (i - lookback + 1) % progress_every == 0:
            print(f"  ...scanned {i - lookback + 1}/{total} candles")

    open_at_end = len(executor.active_trades())
    summary = executor.summary()

    trading_days = max(len(days_seen), 1)

    # Both executor.summary()'s winrate (wins/executed) and a
    # resolved-only figure (wins/(wins+losses)) still include trades
    # still OPEN at the end of the dataset (never resolved either way)
    # in the "executed" denominator, so report the strictest version
    # (resolved only) as the headline number. PARTIAL_WIN counts as a
    # win here too -- see ExecutionEngine.summary().
    resolved = summary["wins"] + summary["partial_wins"] + summary["losses"]
    resolved_winrate = round((summary["wins"] + summary["partial_wins"]) / resolved * 100, 2) if resolved else 0

    return {
        "candles_scanned": scanned,
        "trading_days": trading_days,
        "high_confidence_count": high_confidence,
        "high_confidence_per_day": round(high_confidence / trading_days, 2),
        "signals_fired": signals_fired,
        "signals_fired_per_day": round(signals_fired / trading_days, 2),
        "trades_still_open": open_at_end,
        "trades_pending": summary["pending"],
        "trades_cancelled": summary["cancelled"],
        "resolved_winrate": resolved_winrate,
        "summary": summary,
    }
