from services.indicators import calculate_ema_series

STRATEGY_NAME = "EMA 20/50 Cross-Retest"

EMA_FAST = 20
EMA_SLOW = 50
BULLISH_RETEST_COUNT = 3       # long entry: 3rd retest of the 20-50 zone
BEARISH_RETEST_COUNT = 1       # short entry: 1st retest -- fastest momentum, deliberately aggressive
PIP_SIZE = 0.1                 # XAUUSD convention: 1 pip = $0.10
SL_BUFFER_PIPS = 20
TP_RR = 2.0                    # fixed 1:2 target


def _find_last_cross(ema_fast, ema_slow):
    """
    Most recent EMA20/50 crossover in the series. A "retest" only makes
    sense relative to the bias established by the LAST cross, so this
    has to be found fresh from the given window every call (this
    strategy is stateless like every other module in this codebase --
    it only sees candles, no memory of a previous cycle's cross).
    """
    last_idx, last_dir = None, None

    for i in range(1, len(ema_fast)):
        if None in (ema_fast[i - 1], ema_slow[i - 1], ema_fast[i], ema_slow[i]):
            continue

        prev_diff = ema_fast[i - 1] - ema_slow[i - 1]
        curr_diff = ema_fast[i] - ema_slow[i]

        if prev_diff <= 0 < curr_diff:
            last_idx, last_dir = i, "Bullish"
        elif prev_diff >= 0 > curr_diff:
            last_idx, last_dir = i, "Bearish"

    return last_idx, last_dir


def _retest_indices(candles, ema_fast, ema_slow, cross_idx):
    """
    Bar indices (after cross_idx) where price transitions from OUTSIDE
    the 20-50 EMA zone to INSIDE it -- each such transition is one
    distinct "retest" event. Requires leaving and re-entering the zone
    to count as a new retest (a single continuous stay inside the zone
    across several candles is one retest, not several).
    """
    indices = []
    was_in_zone = False

    for i in range(cross_idx + 1, len(candles)):
        if ema_fast[i] is None or ema_slow[i] is None:
            was_in_zone = False
            continue

        zone_low, zone_high = min(ema_fast[i], ema_slow[i]), max(ema_fast[i], ema_slow[i])
        c = candles[i]
        in_zone = c["low"] <= zone_high and c["high"] >= zone_low

        if in_zone and not was_in_zone:
            indices.append(i)

        was_in_zone = in_zone

    return indices


def analyze(candles):
    """
    Per-cycle analysis: finds the last cross, counts retests since it,
    and reports whether the CURRENT (last) bar is exactly the Nth
    retest bar required for entry (3rd for a bullish cross, 1st for a
    bearish one). "MISSED" means the Nth retest already happened on an
    earlier bar in this window -- the entry window for that setup has
    passed; a later, unrelated cross would be needed for a new signal.
    """

    result = {
        "bias": None, "cross_index": None, "retests": [],
        "status": "NO TRADE", "reason": None, "ema_fast": None, "ema_slow": None,
    }

    if len(candles) < EMA_SLOW + 5:
        result["reason"] = "Insufficient candle data"
        return result

    ema_fast = calculate_ema_series(candles, EMA_FAST)
    ema_slow = calculate_ema_series(candles, EMA_SLOW)

    cross_idx, direction = _find_last_cross(ema_fast, ema_slow)
    if cross_idx is None:
        result["reason"] = "No EMA20/50 cross found in the lookback window"
        return result

    result["bias"] = direction
    result["cross_index"] = cross_idx
    result["ema_fast"] = ema_fast
    result["ema_slow"] = ema_slow

    retests = _retest_indices(candles, ema_fast, ema_slow, cross_idx)
    result["retests"] = retests

    required = BULLISH_RETEST_COUNT if direction == "Bullish" else BEARISH_RETEST_COUNT
    last_idx = len(candles) - 1

    if len(retests) < required:
        result["status"] = "WAITING"
        return result

    if retests[required - 1] != last_idx:
        result["status"] = "MISSED"
        result["reason"] = f"The required {required}-retest already happened on an earlier bar"
        return result

    result["status"] = "CONFIRMED"
    return result


def build_signal(candles, account_balance=1000, risk_percent=1.0, pip_size=PIP_SIZE):
    """
    Entry: market order at the close of the Nth-retest confirmation
    candle (matches this codebase's convention elsewhere -- the
    confirmation candle IS the trigger, no waiting for a deeper
    retrace).

    SL: 20-pip buffer beyond the 50 EMA's value AT ENTRY (fixed once
    opened, same as every other strategy's SL -- ExecutionEngine has no
    concept of a moving stop outside the TP1-breakeven mechanism).

    TP: fixed 1:2 R:R. Implemented as take_profit_1 == take_profit_2 so
    ExecutionEngine's TP1-partial-close logic becomes a no-op single
    clean exit at the target (both "halves" resolve at the same price
    in the same update_trade() call) -- this strategy has no partial-
    profit step of its own.

    The strategy's real distinguishing exit rule -- closing beyond the
    LIVE (re-evaluated every bar) 50 EMA, close-confirmed only, no
    wick stops -- is NOT expressible as a fixed price level and is
    therefore NOT encoded here. It's enforced by the walk-forward
    backtest loop calling analyze() fresh each bar and manually closing
    via ExecutionEngine.close_trade_manual() -- see
    run_ema_cross_retest_backtest.py.
    """

    a = analyze(candles)
    if a["status"] != "CONFIRMED":
        return None

    bias = a["bias"]
    ema_slow = a["ema_slow"]
    last = candles[-1]
    entry = last["close"]
    ema_slow_now = ema_slow[-1]

    if ema_slow_now is None:
        return None

    sl_buffer = SL_BUFFER_PIPS * pip_size

    if bias == "Bullish":
        stop_loss = ema_slow_now - sl_buffer
        recommendation = "BUY"
        entry_type = "BUY_MARKET"
    else:
        stop_loss = ema_slow_now + sl_buffer
        recommendation = "SELL"
        entry_type = "SELL_MARKET"

    sl_distance = abs(entry - stop_loss)
    if sl_distance <= 0:
        return None

    take_profit_2 = entry + sl_distance * TP_RR if bias == "Bullish" else entry - sl_distance * TP_RR
    take_profit_1 = take_profit_2

    risk_amount = account_balance * (risk_percent / 100)
    position_size = risk_amount / sl_distance

    retest_n = len(a["retests"])
    ordinal = {1: "1st", 2: "2nd", 3: "3rd"}.get(retest_n, f"{retest_n}th")

    reasons = [
        f"20/50 EMA {bias} cross, {ordinal} retest of the zone",
        f"SL: 20-pip buffer beyond 50 EMA ({round(ema_slow_now, 2)})",
        f"TP: fixed 1:{TP_RR} R:R",
    ]

    signal = {
        "strategy": STRATEGY_NAME,
        "price": entry,
        "bias": bias,
        "action": recommendation,
        "entry_allowed": True,
        "confidence": 70,
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
        "risk_reward": f"1:{TP_RR}",
        "atr": None,
        "reasons": reasons,
    }

    return signal, entry_data, risk_data
