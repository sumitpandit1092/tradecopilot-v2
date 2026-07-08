from services.atr import calculate_atr


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

    if bias == "Bullish":

        stop_loss = entry - (atr * sl_multiplier)

    elif bias == "Bearish":

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
            "ATR based stop loss",
            "Dynamic position sizing",
            "Institutional risk management"
        ]
    }