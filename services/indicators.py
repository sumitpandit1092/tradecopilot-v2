def calculate_ema_series(candles, period):
    """
    Returns the full EMA series (one value per candle from `period-1`
    onward, None before that), not just the latest value -- strategies
    need to check EMA relationships (e.g. "closed back above EMA20")
    across multiple recent candles, not just the last one.
    """

    closes = [c["close"] for c in candles]

    if len(closes) < period:
        return [None] * len(closes)

    multiplier = 2 / (period + 1)
    series = [None] * (period - 1)

    ema = sum(closes[:period]) / period
    series.append(ema)

    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
        series.append(ema)

    return series


def calculate_ema(candles, period):
    series = calculate_ema_series(candles, period)
    return round(series[-1], 2) if series and series[-1] is not None else None


def calculate_rsi(candles, period=14):
    closes = [c["close"] for c in candles]

    if len(closes) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return round(rsi, 2)


def calculate_bollinger_bands(candles, period=20, std_dev=2.0):
    if len(candles) < period:
        return None

    closes = [c["close"] for c in candles[-period:]]

    sma = sum(closes) / period
    variance = sum((c - sma) ** 2 for c in closes) / period
    std = variance ** 0.5

    return {
        "upper": round(sma + std_dev * std, 2),
        "middle": round(sma, 2),
        "lower": round(sma - std_dev * std, 2),
    }


def _wilder_smooth(values, period):
    if len(values) < period:
        return []

    smoothed = [sum(values[:period])]

    for v in values[period:]:
        smoothed.append(smoothed[-1] - (smoothed[-1] / period) + v)

    return smoothed


def calculate_adx(candles, period=14):
    """
    Standard Wilder's ADX. Needs roughly 2x `period` candles of history
    to produce a meaningful (fully-smoothed) value -- returns None if
    there isn't enough data rather than a noisy early estimate.
    """

    if len(candles) < period * 2:
        return None

    plus_dm = []
    minus_dm = []
    trs = []

    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_high = candles[i - 1]["high"]
        prev_low = candles[i - 1]["low"]
        prev_close = candles[i - 1]["close"]

        up_move = high - prev_high
        down_move = prev_low - low

        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0)

        trs.append(max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        ))

    smoothed_tr = _wilder_smooth(trs, period)
    smoothed_plus_dm = _wilder_smooth(plus_dm, period)
    smoothed_minus_dm = _wilder_smooth(minus_dm, period)

    if not smoothed_tr:
        return None

    plus_di = [100 * (pdm / tr) if tr else 0 for pdm, tr in zip(smoothed_plus_dm, smoothed_tr)]
    minus_di = [100 * (mdm / tr) if tr else 0 for mdm, tr in zip(smoothed_minus_dm, smoothed_tr)]

    dx = [
        100 * abs(p - m) / (p + m) if (p + m) else 0
        for p, m in zip(plus_di, minus_di)
    ]

    if len(dx) < period:
        return None

    adx = sum(dx[:period]) / period

    for d in dx[period:]:
        adx = (adx * (period - 1) + d) / period

    return round(adx, 2)
