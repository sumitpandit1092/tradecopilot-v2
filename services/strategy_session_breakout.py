from services.atr import calculate_atr

STRATEGY_NAME = "Session Breakout"

# Opening range definition -- first 1-2 15m candles of London open.
RANGE_HOUR = 7
RANGE_END_MINUTE = 30


def _today(candles):
    return candles[-1]["time"].split(" ")[0]


def _time_of_day(candle):
    return candle["time"].split(" ")[1]


def _find_opening_range(m15_candles, today):
    range_candles = []

    for c in m15_candles:
        date_part, time_part = c["time"].split(" ")
        if date_part != today:
            continue

        hour, minute = int(time_part.split(":")[0]), int(time_part.split(":")[1])

        if hour == RANGE_HOUR and minute < RANGE_END_MINUTE:
            range_candles.append(c)

    if not range_candles:
        return None

    return {
        "high": max(c["high"] for c in range_candles),
        "low": min(c["low"] for c in range_candles),
    }


def _after_range_window(candle, today):
    date_part, time_part = candle["time"].split(" ")
    if date_part != today:
        return False

    hour, minute = int(time_part.split(":")[0]), int(time_part.split(":")[1])
    return hour > RANGE_HOUR or (hour == RANGE_HOUR and minute >= RANGE_END_MINUTE)


def build_signal(m15_candles, m5_candles, account_balance=1000, risk_percent=1.0):
    """
    Opening range breakout: the first 1-2 15m candles of the London
    session (07:00-07:30 GMT -- see config.py's caveat on candle
    timestamp timezone) define the range; a 5m close beyond that range
    is the breakout signal.

    Entry is a direct market order at the breakout candle's close, NOT
    the "wait for a retest" variant from the original spec. Backtesting
    the retest version showed it was actively harmful: cancelled
    (never-filled) retest orders would have won 95.7% of the time if
    entered directly, versus 32.4% for the ones that *did* retest and
    fill. Geometrically, a retest order can only get cancelled via
    staleness (price never comes back) since its invalidation level
    sits beyond the entry on the same side -- so "cancelled" here
    almost always meant "the breakout just kept running without
    looking back," which is precisely the strongest continuation
    pattern, systematically filtered out by waiting for a pullback.

    Simplification vs the written spec: volume confirmation is
    skipped -- tvDatafeed's forex volume field is unreliable/often
    zero, so requiring "above-average volume" would silently kill
    every signal on this data source. The trailing-stop exit (higher
    lows/lower highs) also isn't implemented -- same static SL/TP2
    limitation as the other two strategies.
    """

    if len(m15_candles) < 20 or len(m5_candles) < 20:
        return None

    today = _today(m5_candles)

    opening_range = _find_opening_range(m15_candles, today)
    if opening_range is None:
        return None

    range_high = opening_range["high"]
    range_low = opening_range["low"]
    range_height = range_high - range_low

    if range_height <= 0:
        return None

    last = m5_candles[-1]

    if not _after_range_window(last, today):
        return None

    bias = None
    reasons = []

    if last["close"] > range_high:
        bias = "Bullish"
        reasons = [
            f"London opening range: {range_low}-{range_high}",
            f"5m candle closed above range high ({range_high})",
        ]

    elif last["close"] < range_low:
        bias = "Bearish"
        reasons = [
            f"London opening range: {range_low}-{range_high}",
            f"5m candle closed below range low ({range_low})",
        ]

    if bias is None:
        return None

    atr_15m = calculate_atr(m15_candles, 14)
    if atr_15m is None:
        return None

    if bias == "Bullish":
        entry = last["close"]  # direct entry at the breakout close
        opposite_side_distance = entry - range_low
        sl_distance = min(opposite_side_distance, atr_15m)
        if sl_distance <= 0:
            return None
        stop_loss = entry - sl_distance
        take_profit_1 = entry + range_height
        take_profit_2 = entry + range_height * 2
        entry_type = "BUY_MARKET"
        recommendation = "BUY"

    else:
        entry = last["close"]
        opposite_side_distance = range_high - entry
        sl_distance = min(opposite_side_distance, atr_15m)
        if sl_distance <= 0:
            return None
        stop_loss = entry + sl_distance
        take_profit_1 = entry - range_height
        take_profit_2 = entry - range_height * 2
        entry_type = "SELL_MARKET"
        recommendation = "SELL"

    reward = abs(take_profit_2 - entry)
    rr = round(reward / sl_distance, 2) if sl_distance else 0

    risk_amount = account_balance * (risk_percent / 100)
    position_size = risk_amount / sl_distance
    confidence = round(min(100, 60 + (range_height / atr_15m) * 10))

    signal = {
        "strategy": STRATEGY_NAME,
        "price": last["close"],
        "bias": bias,
        "action": recommendation,
        "entry_allowed": True,
        "confidence": confidence,
        "recommendation": recommendation,
        "reasons": reasons,
    }

    entry_data = {
        "valid": True,
        "entry": round(entry, 2),
        "entry_type": entry_type,
        "reasons": reasons,
    }

    risk_data = {
        "valid": True,
        "entry": round(entry, 2),
        "entry_type": entry_type,
        "stop_loss": round(stop_loss, 2),
        "take_profit_1": round(take_profit_1, 2),
        "take_profit_2": round(take_profit_2, 2),
        "risk_pct": risk_percent,
        "risk_amount": round(risk_amount, 2),
        "position_size": round(position_size, 4),
        "sl_distance": round(sl_distance, 2),
        "risk_reward": f"1:{rr}",
        "atr": round(atr_15m, 2),
        "reasons": reasons,
    }

    return signal, entry_data, risk_data
