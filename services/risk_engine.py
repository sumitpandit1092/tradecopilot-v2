from services.atr import calculate_atr
from services.structure_engine import get_swings


def _nearest_below(levels, price):
    below = [lvl for lvl in levels if lvl < price]
    return max(below) if below else None


def _nearest_above(levels, price):
    above = [lvl for lvl in levels if lvl > price]
    return min(above) if above else None


def build_risk_plan(
    candles,
    signal,
    entry_data,
    account_balance=1000,
    risk_percent=1.0,
):
    """
    TradeCopilot Risk Engine V3

    Responsibilities:
    - Validate trade
    - Calculate ATR
    - Calculate Stop Loss
    - Calculate TP1 / TP2 / TP3
    - Calculate Position Size
    - Calculate Risk : Reward
    - Grade trade quality

    Does NOT decide BUY / SELL.
    """

    # =====================================================
    # BASIC VALIDATION
    # =====================================================

    if not candles:
        return {
            "valid": False,
            "reason": "No candle data"
        }

    if not signal:
        return {
            "valid": False,
            "reason": "No signal"
        }

    if not entry_data:
        return {
            "valid": False,
            "reason": "No entry data"
        }

    if signal.get("action") == "WAIT":
        return {
            "valid": False,
            "reason": "Signal Engine returned WAIT"
        }

    if not signal.get("entry_allowed"):
        return {
            "valid": False,
            "reason": "Institutional execution filter failed"
        }

    if not entry_data.get("valid"):
        return {
            "valid": False,
            "reason": "Entry Engine rejected trade"
        }

    # =====================================================
    # MARKET DATA
    # =====================================================

    bias = signal["bias"]
    entry = entry_data["entry"]
    entry_type = entry_data["entry_type"]

    atr = calculate_atr(candles)

    if atr is None:
        return {
            "valid": False,
            "reason": "ATR unavailable"
        }

    # =====================================================
    # RISK SETTINGS
    # =====================================================

    risk_amount = account_balance * (risk_percent / 100)

    sl_multiplier = 1.5
    tp1_multiplier = 2.0
    tp2_multiplier = 3.0
    tp3_multiplier = 4.0

    # =====================================================
    # STOP LOSS
    # =====================================================
    # FIXED: entry_engine.py already computes a real structural
    # invalidation level (the order block's low/high -- actual
    # support/resistance) whenever one is available, but it was being
    # thrown away here in favor of a pure entry +/- 1.5*ATR distance
    # that ignores where real structure actually sits. A stop placed
    # purely by ATR can land inside normal noise range well short of
    # the level price would actually need to break to invalidate the
    # setup, causing stop-outs on moves that never really threatened
    # the trade idea.
    #
    # Fallback chain (each tier only used if the one above isn't
    # available): Order Block invalidation -> nearest liquidity pool or
    # swing point beyond entry (both are real support/resistance, just
    # a coarser read than an order block) -> ATR multiple as the last
    # resort when the window has no structure at all yet. Liquidity
    # pools and swing points are merged into one tier and the NEAREST
    # of either is used -- both are equally valid S/R, and the closer
    # one gives the tightest stop that's still anchored to something
    # real, rather than arbitrarily preferring one source.
    #
    # A small ATR buffer is placed beyond the liquidity/swing level
    # (not exactly at it) since price frequently wicks precisely to a
    # prior swing/pool before reversing -- stopping exactly on the
    # level would get clipped by the very wick that confirms it.

    invalidation = entry_data.get("invalidation")

    liquidity = signal.get("liquidity") or {}
    sell_side_levels = [p["level"] for p in liquidity.get("sell_side_liquidity", [])]
    buy_side_levels = [p["level"] for p in liquidity.get("buy_side_liquidity", [])]

    swing_highs, swing_lows = get_swings(candles)

    structure_buffer = atr * 0.15
    stop_loss_source = "ATR (no structure available)"

    if bias == "Bullish":

        if invalidation is not None and invalidation < entry:
            stop_loss = invalidation
            stop_loss_source = "Order Block"
        else:
            nearest_structural = _nearest_below(sell_side_levels + swing_lows, entry)
            if nearest_structural is not None:
                stop_loss = nearest_structural - structure_buffer
                stop_loss_source = "Liquidity Pool / Swing Low"
            else:
                stop_loss = entry - (atr * sl_multiplier)

    elif bias == "Bearish":

        if invalidation is not None and invalidation > entry:
            stop_loss = invalidation
            stop_loss_source = "Order Block"
        else:
            nearest_structural = _nearest_above(buy_side_levels + swing_highs, entry)
            if nearest_structural is not None:
                stop_loss = nearest_structural + structure_buffer
                stop_loss_source = "Liquidity Pool / Swing High"
            else:
                stop_loss = entry + (atr * sl_multiplier)

    else:

        return {
            "valid": False,
            "reason": "Neutral bias"
        }

    # =====================================================
    # TAKE PROFITS
    # =====================================================

    if bias == "Bullish":

        tp1 = entry + (atr * tp1_multiplier)
        tp2 = entry + (atr * tp2_multiplier)
        tp3 = entry + (atr * tp3_multiplier)

    else:

        tp1 = entry - (atr * tp1_multiplier)
        tp2 = entry - (atr * tp2_multiplier)
        tp3 = entry - (atr * tp3_multiplier)

    # =====================================================
    # POSITION SIZE
    # =====================================================

    sl_distance = abs(entry - stop_loss)

    if sl_distance <= 0:

        return {
            "valid": False,
            "reason": "Invalid Stop Loss distance"
        }

    position_size = risk_amount / sl_distance

    # =====================================================
    # RISK : REWARD
    # =====================================================

    reward = abs(tp2 - entry)
    risk = abs(entry - stop_loss)

    rr = round(reward / risk, 2)

    risk_reward = f"1:{rr}"

    # =====================================================
    # TRADE QUALITY
    # =====================================================

    if rr >= 3:

        trade_quality = "A"

    elif rr >= 2:

        trade_quality = "B"

    else:

        trade_quality = "C"

    # =====================================================
    # SAFETY FILTERS
    # =====================================================

    if sl_distance < atr * 0.5:

        return {
            "valid": False,
            "reason": "Stop Loss too tight"
        }

    if sl_distance > atr * 3:

        return {
            "valid": False,
            "reason": "Stop Loss too wide"
        }

    # =====================================================
    # FINAL OUTPUT
    # =====================================================

    return {

        "valid": True,

        "entry": round(entry, 2),

        "entry_type": entry_type,

        "stop_loss": round(stop_loss, 2),

        "take_profit_1": round(tp1, 2),

        "take_profit_2": round(tp2, 2),

        "take_profit_3": round(tp3, 2),

        "risk_pct": risk_percent,

        "risk_amount": round(risk_amount, 2),

        "position_size": round(position_size, 4),

        "sl_distance": round(sl_distance, 2),

        "risk_reward": risk_reward,

        "trade_quality": trade_quality,

        "atr": round(atr, 2),

        "execution_ready": True,

        "reasons": [
            f"Stop loss anchored to: {stop_loss_source}",
            "Dynamic position sizing",
            "Institutional risk management"
        ]
    }