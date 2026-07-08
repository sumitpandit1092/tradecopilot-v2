def build_entry(signal, candles=None):
    """
    Entry Engine V3

    Determines:
    - Whether an entry is valid
    - Entry type (MARKET / LIMIT / WAIT)
    - Invalidation level
    - Entry confidence
    """

    if not signal:
        return {
            "valid": False,
            "reason": "No signal provided"
        }

    # =====================================================
    # MASTER SIGNAL CHECK
    # =====================================================

    if signal.get("action") == "WAIT":
        return {
            "valid": False,
            "reason": "Signal is WAIT"
        }

    bias = signal.get("bias")
    price = signal.get("price")
    context = signal.get("context", {})

    bullish_fvg = signal.get("bullish_fvg", [])
    bearish_fvg = signal.get("bearish_fvg", [])

    bullish_ob = signal.get("bullish_ob", [])
    bearish_ob = signal.get("bearish_ob", [])

    confidence = signal.get("confidence", 0)

    entry = None
    entry_type = "WAIT"
    invalidation = None
    reasons = []
    valid = False

    # =====================================================
    # BUY SETUP
    # =====================================================

    if bias == "Bullish":

        if context and context.get("zone") == "Discount":

            valid = True
            entry = price

            if bullish_ob:
                entry_type = "BUY_LIMIT"
                invalidation = bullish_ob[-1]["low"]

            else:
                entry_type = "BUY_MARKET"
                invalidation = price - 10

            reasons.append("Bullish bias aligned with Discount zone")

            if bullish_fvg:
                reasons.append("Bullish FVG present")

            if bullish_ob:
                reasons.append("Bullish Order Block present")

    # =====================================================
    # SELL SETUP
    # =====================================================

    elif bias == "Bearish":

        if context and context.get("zone") == "Premium":

            valid = True
            entry = price

            if bearish_ob:
                entry_type = "SELL_LIMIT"
                invalidation = bearish_ob[-1]["high"]

            else:
                entry_type = "SELL_MARKET"
                invalidation = price + 10

            reasons.append("Bearish bias aligned with Premium zone")

            if bearish_fvg:
                reasons.append("Bearish FVG present")

            if bearish_ob:
                reasons.append("Bearish Order Block present")

    # =====================================================
    # INVALID
    # =====================================================

    if not valid:
        return {
            "valid": False,
            "reason": "No high-probability entry available"
        }

    # =====================================================
    # RETURN
    # =====================================================

    return {
        "valid": True,
        "entry": round(entry, 2),
        "entry_type": entry_type,
        "invalidation": round(invalidation, 2) if invalidation else None,
        "entry_confidence": confidence,
        "reasons": reasons
    }