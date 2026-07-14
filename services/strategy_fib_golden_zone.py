from services.atr import calculate_atr
from services.indicators import calculate_ema
from services.structure_engine import get_swings, detect_structure

STRATEGY_NAME = "Fib Golden Zone Pullback"

H1_TREND_EMA = 50
M5_CONFLUENCE_EMA = 50
MIN_LEG_ATR_MULT = 1.5
PIP_SIZE = 0.1                  # XAUUSD convention: 1 pip = $0.10
SL_BUFFER_PIPS = 5
MIN_RR_TO_TP1 = 1.5
MAX_TRADES_PER_SESSION = 2
MAX_CONSECUTIVE_LOSSES = 2
ROUND_NUMBER_STEP = 10          # e.g. 3350, 3360 -- treated as "round" levels

LONDON_START_MIN = 7 * 60
LONDON_END_MIN = 11 * 60
NY_START_MIN = 12 * 60 + 30
NY_END_MIN = 16 * 60 + 30


def _parse_time(time_str):
    date_part, time_part = time_str.split(" ")
    h, m = time_part.split(":")[0], time_part.split(":")[1]
    return date_part, int(h) * 60 + int(m)


def _session_name(time_str):
    """
    London (07:00-11:00 UTC) and New York (12:30-16:30 UTC) only, per
    the spec -- everything else is not a valid trading window. Same
    UTC-assumption caveat as config.py's SESSION_START_HOUR: the hour
    is read directly off tvDatafeed's candle timestamp, whose actual
    timezone isn't independently confirmed.
    """
    date_part, minutes = _parse_time(time_str)
    if LONDON_START_MIN <= minutes < LONDON_END_MIN:
        return date_part, "London"
    if NY_START_MIN <= minutes < NY_END_MIN:
        return date_part, "NewYork"
    return date_part, None


def _get_swing_points(candles, lookback=3):
    """
    Unlike structure_engine.get_swings() (which returns highs/lows as
    two separate value-only lists), this keeps bar index and type
    together in one chronological list -- needed to pair "the most
    recent swing low with the swing high that follows it" into an
    actual tradeable leg, which requires knowing WHICH high came after
    WHICH low, not just two independent lists of price levels.
    """
    points = []

    if len(candles) < 2 * lookback + 1:
        return points

    for i in range(lookback, len(candles) - lookback):
        high = candles[i]["high"]
        low = candles[i]["low"]

        if all(high > candles[j]["high"] for j in range(i - lookback, i + lookback + 1) if j != i):
            points.append({"index": i, "price": high, "type": "high"})

        if all(low < candles[j]["low"] for j in range(i - lookback, i + lookback + 1) if j != i):
            points.append({"index": i, "price": low, "type": "low"})

    points.sort(key=lambda p: p["index"])
    return points


def _find_valid_leg(points, end_type, atr):
    """
    Walks backward from the most recent swing point of `end_type`
    (high for an uptrend leg, low for a downtrend leg), pairs it with
    the swing point of the opposite type immediately before it, and
    returns that pair if the leg clears the minimum-size filter
    (1.5x ATR(14) on H1, per the spec's "ignore smaller legs").

    "Ignore" is implemented as "keep looking further back" rather than
    "give up" -- if the most recent leg is too small, the search moves
    to the next-older end-type point instead of failing outright, so a
    small final wiggle right at the current high/low doesn't block an
    otherwise-valid larger leg just behind it.
    """
    start_type = "low" if end_type == "high" else "high"

    for i in range(len(points) - 1, -1, -1):
        if points[i]["type"] != end_type:
            continue

        for j in range(i - 1, -1, -1):
            if points[j]["type"] == start_type:
                leg_size = abs(points[i]["price"] - points[j]["price"])
                if leg_size >= MIN_LEG_ATR_MULT * atr:
                    return points[j], points[i]
                break  # nearest opposite point was too small -- try an older end point

    return None, None


def _fib_level(leg_start, leg_end, ratio):
    """Retracement price at `ratio` back from leg_end toward leg_start."""
    return leg_end - (leg_end - leg_start) * ratio


def _fib_extension(leg_start, leg_end, ratio):
    """Extension price at `ratio` (>1.0 projects beyond leg_end)."""
    return leg_start + (leg_end - leg_start) * ratio


def _is_bullish_engulfing(prev, curr):
    return (
        prev["close"] < prev["open"]
        and curr["close"] > curr["open"]
        and curr["open"] <= prev["close"]
        and curr["close"] >= prev["open"]
    )


def _is_bearish_engulfing(prev, curr):
    return (
        prev["close"] > prev["open"]
        and curr["close"] < curr["open"]
        and curr["open"] >= prev["close"]
        and curr["close"] <= prev["open"]
    )


def _is_bullish_pin_bar(c):
    body = abs(c["close"] - c["open"])
    lower_wick = min(c["open"], c["close"]) - c["low"]
    upper_wick = c["high"] - max(c["open"], c["close"])
    total = c["high"] - c["low"]
    if total <= 0:
        return False
    return lower_wick >= body * 2 and lower_wick > upper_wick and (c["close"] - c["low"]) / total >= 0.6


def _is_bearish_pin_bar(c):
    body = abs(c["close"] - c["open"])
    upper_wick = c["high"] - max(c["open"], c["close"])
    lower_wick = min(c["open"], c["close"]) - c["low"]
    total = c["high"] - c["low"]
    if total <= 0:
        return False
    return upper_wick >= body * 2 and upper_wick > lower_wick and (c["high"] - c["close"]) / total >= 0.6


def analyze(h1_candles, m5_candles):
    """
    Full per-cycle analysis matching the spec's required output (TREND /
    FIB LEG / GOLDEN ZONE / STATUS / CONFLUENCE FACTORS), independent of
    whether a trade actually fires this bar. build_signal() below calls
    this and only builds a tradeable plan when status == "CONFIRMED".

    Step 5's "1H closes beyond the fib origin" and "5m closes beyond
    78.6% before entry" invalidation rules need no separate state: this
    re-derives the current trend/leg/zone fresh from the latest candles
    every call, so an invalidated or redrawn setup is just whatever this
    call computes next -- there's no stale fib object to explicitly
    delete. Major-news blackout (FOMC/NFP/CPI) is NOT implemented: this
    codebase has no economic-calendar data source, and faking one would
    be worse than admitting the gap.
    """

    result = {
        "trend": "RANGING",
        "bias": None,
        "fib_leg": None,
        "leg_start": None,
        "leg_end": None,
        "level_786": None,
        "golden_zone": None,
        "status": "NO TRADE",
        "confluence_factors": [],
        "atr_h1": None,
        "reason": None,
    }

    if len(h1_candles) < 30 or len(m5_candles) < 3:
        result["reason"] = "Insufficient candle data"
        return result

    last_m5 = m5_candles[-1]
    date_part, session = _session_name(last_m5["time"])

    if session is None:
        result["reason"] = "Outside London/NY session window"
        return result

    ema50_h1 = calculate_ema(h1_candles, H1_TREND_EMA)
    if ema50_h1 is None:
        result["reason"] = "H1 EMA50 unavailable (insufficient data)"
        return result

    highs, lows = get_swings(h1_candles)
    structure = detect_structure(highs, lows)
    h1_close = h1_candles[-1]["close"]

    if h1_close > ema50_h1 and "Bullish" in structure:
        trend = "UP"
    elif h1_close < ema50_h1 and "Bearish" in structure:
        trend = "DOWN"
    else:
        result["trend"] = "RANGING"
        result["status"] = "RANGING -- STAND ASIDE"
        return result

    result["trend"] = trend
    bias = "Bullish" if trend == "UP" else "Bearish"
    result["bias"] = bias

    atr_h1 = calculate_atr(h1_candles, 14)
    if atr_h1 is None:
        result["reason"] = "H1 ATR unavailable"
        return result
    result["atr_h1"] = atr_h1

    points = _get_swing_points(h1_candles)
    end_type = "high" if trend == "UP" else "low"
    start_point, end_point = _find_valid_leg(points, end_type, atr_h1)

    if start_point is None:
        result["status"] = "NO TRADE"
        result["reason"] = "No valid-sized swing leg found (>= 1.5x ATR)"
        return result

    leg_start, leg_end = start_point["price"], end_point["price"]
    result["leg_start"] = leg_start
    result["leg_end"] = leg_end
    result["fib_leg"] = f"{round(leg_start, 2)} -> {round(leg_end, 2)} (${round(abs(leg_end - leg_start), 2)})"

    level_50 = _fib_level(leg_start, leg_end, 0.5)
    level_618 = _fib_level(leg_start, leg_end, 0.618)
    level_786 = _fib_level(leg_start, leg_end, 0.786)
    zone_low, zone_high = min(level_50, level_618), max(level_50, level_618)

    result["golden_zone"] = f"{round(zone_low, 2)} - {round(zone_high, 2)}"
    result["level_786"] = level_786

    last, prev = m5_candles[-1], m5_candles[-2]

    invalidated = (last["close"] < level_786) if trend == "UP" else (last["close"] > level_786)
    if invalidated:
        result["status"] = "INVALIDATED"
        result["reason"] = "5m candle closed beyond the 78.6% level"
        return result

    in_zone = last["low"] <= zone_high and last["high"] >= zone_low
    if not in_zone:
        result["status"] = "WAITING"
        return result

    result["status"] = "IN ZONE"

    if trend == "UP":
        pattern_ok = _is_bullish_engulfing(prev, last) or _is_bullish_pin_bar(last)
        confirmed = pattern_ok and last["close"] > last["open"]
    else:
        pattern_ok = _is_bearish_engulfing(prev, last) or _is_bearish_pin_bar(last)
        confirmed = pattern_ok and last["close"] < last["open"]

    # Confluence bonus -- computed regardless of `confirmed` so WAITING/
    # IN ZONE cycles can still report what confluence is present.
    confluence_factors = []

    ema50_m5 = calculate_ema(m5_candles, M5_CONFLUENCE_EMA)
    if ema50_m5 is not None and zone_low <= ema50_m5 <= zone_high:
        confluence_factors.append(f"50 EMA (5m) at {round(ema50_m5, 2)} sits inside the zone")

    other_levels = [p["price"] for p in points if p is not start_point and p is not end_point]
    overlapping = [lvl for lvl in other_levels if zone_low <= lvl <= zone_high]
    if overlapping:
        confluence_factors.append(f"Zone overlaps prior 1H swing level at {round(overlapping[0], 2)}")

    n = int(zone_low // ROUND_NUMBER_STEP) * ROUND_NUMBER_STEP
    while n <= zone_high:
        if zone_low <= n <= zone_high:
            confluence_factors.append(f"Round number {n} inside zone")
            break
        n += ROUND_NUMBER_STEP

    result["confluence_factors"] = confluence_factors

    if not confirmed:
        return result  # still "IN ZONE" -- no confirmation candle yet

    if not confluence_factors:
        result["reason"] = "Confirmation candle present but no confluence factor"
        return result  # still "IN ZONE" -- confluence requirement not met

    result["status"] = "CONFIRMED"
    result["session_label"] = f"{date_part}_{session}"
    return result


def build_signal(h1_candles, m5_candles, account_balance=1000, risk_percent=1.0, pip_size=PIP_SIZE):
    """
    Wraps analyze() and, only when status == "CONFIRMED", builds the
    full entry/risk plan per Step 4:

    - SL: 5 pips beyond the 78.6% level, OR beyond the confirmation
      candle's extreme -- whichever is WIDER (further from entry).
      `pip_size` defaults to XAUUSD's $0.10 convention -- pass the
      instrument's actual pip size (see market_data.INSTRUMENTS) when
      backtesting a different pair; a JPY cross or silver at gold's
      pip size would give a nonsensical SL buffer.
    - TP1 = the 100% level, i.e. the leg's original endpoint (prior
      swing high for an uptrend BUY, prior swing low for a downtrend
      SELL) -- closes half the position and moves the stop to
      breakeven via ExecutionEngine's existing TP1 partial-close
      mechanism, reused as-is.
    - TP2 = the 127.2% extension beyond the leg.
    - Minimum 1.5 R:R to TP1, else the trade is skipped entirely (not
      just flagged) -- this is a hard entry filter per the spec.

    Session-trade-count and consecutive-loss caps are NOT enforced
    here -- build_signal() is stateless by convention in this codebase
    (matches every other strategy module) and has no visibility into
    trade history. Those two "non-negotiable" Step 4 rules are instead
    enforced by ExecutionEngine.open_trade()'s max_per_session /
    max_consecutive_losses kwargs (see execution_engine.py), using the
    `session_label` this function stamps onto the signal.
    """

    a = analyze(h1_candles, m5_candles)
    if a["status"] != "CONFIRMED":
        return None

    trend, bias = a["trend"], a["bias"]
    leg_start, leg_end, level_786 = a["leg_start"], a["leg_end"], a["level_786"]
    last = m5_candles[-1]
    entry = last["close"]

    sl_buffer = SL_BUFFER_PIPS * pip_size

    if trend == "UP":
        sl_from_fib = level_786 - sl_buffer
        sl_from_candle = last["low"] - sl_buffer
        stop_loss = min(sl_from_fib, sl_from_candle)  # lower = wider for a BUY
        take_profit_1 = leg_end
        take_profit_2 = _fib_extension(leg_start, leg_end, 1.272)
        entry_type = "BUY_MARKET"
        recommendation = "BUY"
    else:
        sl_from_fib = level_786 + sl_buffer
        sl_from_candle = last["high"] + sl_buffer
        stop_loss = max(sl_from_fib, sl_from_candle)  # higher = wider for a SELL
        take_profit_1 = leg_end
        take_profit_2 = _fib_extension(leg_start, leg_end, 1.272)
        entry_type = "SELL_MARKET"
        recommendation = "SELL"

    sl_distance = abs(entry - stop_loss)
    if sl_distance <= 0:
        return None

    rr_to_tp1 = abs(take_profit_1 - entry) / sl_distance
    if rr_to_tp1 < MIN_RR_TO_TP1:
        return None  # Step 4: hard minimum R:R filter, skip (not just flag)

    risk_amount = account_balance * (risk_percent / 100)
    position_size = risk_amount / sl_distance

    rr_to_tp2 = round(abs(take_profit_2 - entry) / sl_distance, 2)
    confidence = min(100, 55 + 15 * len(a["confluence_factors"]))

    reasons = [
        f"1H trend {trend}, fib leg {a['fib_leg']}",
        f"Confirmed in Golden Zone {a['golden_zone']}",
        f"R:R to TP1 = 1:{round(rr_to_tp1, 2)}",
    ] + a["confluence_factors"]

    signal = {
        "strategy": STRATEGY_NAME,
        "price": entry,
        "bias": bias,
        "action": recommendation,
        "entry_allowed": True,
        "confidence": confidence,
        "recommendation": recommendation,
        "reasons": reasons,
        "session_label": a["session_label"],
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
        "risk_reward": f"1:{rr_to_tp2}",
        "atr": round(a["atr_h1"], 2) if a["atr_h1"] else None,
        "reasons": reasons,
        "session_label": a["session_label"],
    }

    return signal, entry_data, risk_data


def format_report(h1_candles, m5_candles, account_balance=1000, risk_percent=1.0):
    """Renders the exact per-cycle OUTPUT FORMAT the strategy spec requires."""

    a = analyze(h1_candles, m5_candles)

    lines = [
        f"TREND: {a['trend']}",
        f"FIB LEG: {a['fib_leg'] or 'N/A'}",
        f"GOLDEN ZONE: {a['golden_zone'] or 'N/A'}",
        f"STATUS: {a['status']}",
    ]

    if a["status"] == "CONFIRMED":
        result = build_signal(h1_candles, m5_candles, account_balance, risk_percent)
        if result:
            _, entry, risk = result
            lines.append(
                f"Entry: {entry['entry']}  SL: {risk['stop_loss']}  "
                f"TP1: {risk['take_profit_1']}  TP2: {risk['take_profit_2']}  "
                f"R:R: {risk['risk_reward']}  Position size: {risk['position_size']}"
            )

    factors = a["confluence_factors"]
    lines.append(f"CONFLUENCE FACTORS: {', '.join(factors) if factors else 'None'}")

    if a.get("reason"):
        lines.append(f"NOTE: {a['reason']}")

    return "\n".join(lines)
