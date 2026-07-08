def get_swings(candles, lookback=3):
    highs = []
    lows = []

    if len(candles) < (2 * lookback + 1):
        return highs, lows

    for i in range(lookback, len(candles) - lookback):

        high = candles[i]["high"]
        low = candles[i]["low"]

        is_swing_high = all(
            high > candles[j]["high"]
            for j in range(i - lookback, i + lookback + 1)
            if j != i
        )

        is_swing_low = all(
            low < candles[j]["low"]
            for j in range(i - lookback, i + lookback + 1)
            if j != i
        )

        if is_swing_high:
            highs.append(high)

        if is_swing_low:
            lows.append(low)

    return highs, lows


def detect_structure(highs, lows):

    if len(highs) < 2 or len(lows) < 2:
        return "Insufficient data"

    if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
        return "Bullish Structure (HH/HL)"

    if highs[-1] < highs[-2] and lows[-1] < lows[-2]:
        return "Bearish Structure (LH/LL)"

    return "Ranging / Indecision"


def detect_bos_choc(highs, lows):
    bullish_bos = len(highs) >= 2 and highs[-1] > highs[-2]
    bearish_bos = len(lows) >= 2 and lows[-1] < lows[-2]

    if bullish_bos and bearish_bos:
        bos = "Both (Range Expansion)"
    elif bullish_bos:
        bos = "Bullish BOS"
    elif bearish_bos:
        bos = "Bearish BOS"
    else:
        bos = None

    choc = None

    bearish_choc = len(highs) >= 3 and highs[-3] < highs[-2] and highs[-1] < highs[-2]
    bullish_choc = len(lows) >= 3 and lows[-3] > lows[-2] and lows[-1] > lows[-2]

    if bullish_choc and bearish_choc:
        choc = "Both (Conflicting CHoCH -- treat as neutral)"
    elif bearish_choc:
        choc = "Bearish CHoCH"
    elif bullish_choc:
        choc = "Bullish CHoCH"

    return bos, choc


def detect_liquidity(highs, lows, tolerance=0.001):
    buy_side = []
    sell_side = []

    for i in range(1, len(highs)):
        if abs(highs[i] - highs[i - 1]) <= highs[i] * tolerance:
            buy_side.append({"level": highs[i], "swept": False})

    for i in range(1, len(lows)):
        if abs(lows[i] - lows[i - 1]) <= lows[i] * tolerance:
            sell_side.append({"level": lows[i], "swept": False})

    return {
        "buy_side_liquidity": buy_side,
        "sell_side_liquidity": sell_side
    }


def detect_liquidity_sweep(candles, highs, lows, recent_bars=5):
    """
    A sweep is scored if it happened within the last `recent_bars`
    candles, not only on the single most recent one. A real sniper
    entry doesn't require the sweep, the order block, and the FVG to
    all land on the exact same candle -- it waits for the sweep to
    have *just* happened, then looks for OB/FVG confluence to enter
    shortly after. Restricting this to candles[-1] only made that
    combination almost never occur.
    """

    if len(candles) < 2:
        return []

    sweeps = []
    recent = candles[-recent_bars:] if len(candles) >= recent_bars else candles

    for c in recent:
        for h in highs:
            if c["high"] > h and c["close"] < h:
                msg = f"Buy-side sweep above {h}"
                if msg not in sweeps:
                    sweeps.append(msg)

        for l in lows:
            if c["low"] < l and c["close"] > l:
                msg = f"Sell-side sweep below {l}"
                if msg not in sweeps:
                    sweeps.append(msg)

    return sweeps


def mark_swept_liquidity(liquidity, candles):
    if not liquidity or not candles:
        return liquidity

    for pool in liquidity.get("buy_side_liquidity", []):
        level = pool["level"]
        for c in candles:
            if c["high"] > level:
                pool["swept"] = True
                break

    for pool in liquidity.get("sell_side_liquidity", []):
        level = pool["level"]
        for c in candles:
            if c["low"] < level:
                pool["swept"] = True
                break

    return liquidity


def get_external_structure(candles, lookback=5):
    highs, lows = get_swings(candles, lookback=lookback)

    if not highs or not lows:
        return None

    return {
        "external_high": max(highs),
        "external_low": min(lows),
    }