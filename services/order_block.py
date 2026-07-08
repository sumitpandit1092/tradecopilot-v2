from services.atr import calculate_atr


def detect_order_blocks(candles, displacement_multiplier=1.2):
    bullish = []
    bearish = []

    atr = calculate_atr(candles) or 0

    for i in range(1, len(candles) - 1):
        prev = candles[i]
        nxt = candles[i + 1]

        breakout_range = abs(nxt["close"] - nxt["open"])
        displacement_ok = (atr == 0) or (breakout_range >= atr * displacement_multiplier)

        if (
            prev["close"] < prev["open"] and
            nxt["close"] > nxt["open"] and
            nxt["close"] > prev["high"] and
            displacement_ok
        ):
            bullish.append({
                "low": prev["low"],
                "high": prev["high"],
                "time": prev["time"],
                "mitigated": False,
            })

        if (
            prev["close"] > prev["open"] and
            nxt["close"] < nxt["open"] and
            nxt["close"] < prev["low"] and
            displacement_ok
        ):
            bearish.append({
                "low": prev["low"],
                "high": prev["high"],
                "time": prev["time"],
                "mitigated": False,
            })

    _mark_mitigated(bullish, candles)
    _mark_mitigated(bearish, candles)

    return bullish, bearish


def _mark_mitigated(order_blocks, candles):
    for ob in order_blocks:
        for c in candles:
            if c["time"] <= ob["time"]:
                continue
            if c["low"] <= ob["high"] and c["high"] >= ob["low"]:
                ob["mitigated"] = True
                break


def unmitigated(order_blocks):
    return [ob for ob in order_blocks if not ob.get("mitigated")]