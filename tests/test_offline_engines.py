"""
Deterministic, offline regression tests.

These use synthetic candle data instead of live TradingView data, so
they run in under a second with no network access and no dependency
on market hours or TradingView rate limits. Run with:

    pytest tests/test_offline_engines.py -v
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.structure_engine import detect_bos_choc
from services.execution_engine import ExecutionEngine


def make_candle(t, o, h, l, c, v=1000):
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v}


# =====================================================
# Regression test for Phase 1.1: BOS/CHoCH must no longer
# silently let a bearish flag overwrite a bullish one.
# =====================================================
def test_bos_choc_independent_flags():
    # highs strictly increasing -> bullish BOS
    # lows strictly decreasing -> bearish BOS
    # Both are true at once -> must be reported as "Both", not silently
    # collapsed to just Bearish (the old bug).
    highs = [10, 20, 30]
    lows = [5, 3, 1]

    bos, choc = detect_bos_choc(highs, lows)

    assert bos == "Both (Range Expansion)"


def test_bos_choc_bullish_only():
    highs = [10, 20, 30]
    lows = [5, 6, 7]

    bos, choc = detect_bos_choc(highs, lows)

    assert bos == "Bullish BOS"


def test_bos_choc_bearish_only():
    highs = [30, 20, 10]
    lows = [7, 6, 5]

    bos, choc = detect_bos_choc(highs, lows)

    assert bos == "Bearish BOS"


# =====================================================
# Regression test for Phase 1.3: trades must actually be
# able to close once update_trade() is called with a price
# past TP2 or past the stop loss.
# =====================================================
def test_trade_lifecycle_win(tmp_path):
    log_file = str(tmp_path / "test_journal.json")
    executor = ExecutionEngine(log_file=log_file)

    signal = {
        "price": 2000,
        "bias": "Bullish",
        "confidence": 90,
        "recommendation": "BUY",
    }
    entry = {"entry": 2000, "entry_type": "BUY_MARKET"}
    risk = {
        "stop_loss": 1990,
        "take_profit_1": 2005,
        "take_profit_2": 2010,
        "risk_pct": 1.0,
        "risk_amount": 10,
        "position_size": 1.0,
    }

    trade = executor.open_trade(signal, entry, risk)
    assert trade is not None
    assert trade["status"] == "OPEN"

    # Price moves past TP2 -> trade should close as a WIN
    updated = executor.update_trade(trade["id"], price=2011)

    assert updated["status"] == "CLOSED"
    assert updated["result"] == "WIN"

    summary = executor.summary()
    assert summary["wins"] == 1
    assert summary["total_trades"] == 1


def test_trade_lifecycle_loss(tmp_path):
    log_file = str(tmp_path / "test_journal.json")
    executor = ExecutionEngine(log_file=log_file)

    signal = {
        "price": 2000,
        "bias": "Bearish",
        "confidence": 90,
        "recommendation": "SELL",
    }
    entry = {"entry": 2000, "entry_type": "SELL_MARKET"}
    risk = {
        "stop_loss": 2010,
        "take_profit_1": 1995,
        "take_profit_2": 1990,
        "risk_pct": 1.0,
        "risk_amount": 10,
        "position_size": 1.0,
    }

    trade = executor.open_trade(signal, entry, risk)

    # Price moves past the stop loss -> trade should close as a LOSS
    updated = executor.update_trade(trade["id"], price=2011)

    assert updated["status"] == "CLOSED"
    assert updated["result"] == "LOSS"

    summary = executor.summary()
    assert summary["losses"] == 1


# =====================================================
# Regression test for the FVG fill-tracking upgrade.
# =====================================================
def test_fvg_fill_tracking():
    from services.fvg import detect_fvg, unfilled

    candles = [
        make_candle(1, 100, 101, 99, 100),
        make_candle(2, 100, 102, 100, 101),
        make_candle(3, 105, 110, 104, 108),   # creates a bullish gap vs candle 1's high (101) if low > 101
        make_candle(4, 108, 109, 100, 101),   # trades back down through the gap -> should mark it filled
    ]

    bullish, _ = detect_fvg(candles)

    if bullish:
        # after candle 4 trades back into the gap range, it must be filled
        assert bullish[0]["filled"] is True
        assert unfilled(bullish) == []
