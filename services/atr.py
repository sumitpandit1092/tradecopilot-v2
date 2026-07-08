def calculate_atr(candles, period=14):
    """
    Calculate Average True Range (ATR)
    """

    if len(candles) < period + 1:
        return None

    true_ranges = []

    for i in range(1, len(candles)):

        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )

        true_ranges.append(tr)

    atr = sum(true_ranges[-period:]) / period

    return round(atr, 2)