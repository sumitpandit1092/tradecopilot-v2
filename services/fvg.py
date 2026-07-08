def detect_fvg(candles):
    bullish = []
    bearish = []

    if len(candles) < 3:
        return bullish, bearish

    for i in range(2, len(candles)):
        c1 = candles[i - 2]
        c3 = candles[i]

        if c1["high"] < c3["low"]:
            bullish.append({
                "start": c1["high"],
                "end": c3["low"],
                "time": c3["time"],
                "filled": False,
            })

        if c1["low"] > c3["high"]:
            bearish.append({
                "start": c3["high"],
                "end": c1["low"],
                "time": c3["time"],
                "filled": False,
            })

    _mark_filled(bullish, candles)
    _mark_filled(bearish, candles)

    return bullish, bearish


def _mark_filled(gaps, candles):
    for gap in gaps:
        low = min(gap["start"], gap["end"])
        high = max(gap["start"], gap["end"])

        for c in candles:
            if c["time"] <= gap["time"]:
                continue
            if c["low"] <= high and c["high"] >= low:
                gap["filled"] = True
                break


def unfilled(gaps):
    return [g for g in gaps if not g.get("filled")]