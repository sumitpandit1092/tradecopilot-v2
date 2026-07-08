from services.structure_engine import get_swings, get_external_structure


def build_market_context(candles):
    external = get_external_structure(candles, lookback=5)

    if external:
        swing_high = external["external_high"]
        swing_low = external["external_low"]
    else:
        highs, lows = get_swings(candles, lookback=3)

        if len(highs) < 1 or len(lows) < 1:
            return None

        swing_high = highs[-1]
        swing_low = lows[-1]

    equilibrium = (swing_high + swing_low) / 2
    price = candles[-1]["close"]

    if price > equilibrium:
        zone = "Premium"
    elif price < equilibrium:
        zone = "Discount"
    else:
        zone = "Equilibrium"

    return {
        "price": round(price, 2),
        "swing_high": round(swing_high, 2),
        "swing_low": round(swing_low, 2),
        "equilibrium": round(equilibrium, 2),
        "dealing_range_high": round(swing_high, 2),
        "dealing_range_low": round(swing_low, 2),
        "zone": zone,
    }