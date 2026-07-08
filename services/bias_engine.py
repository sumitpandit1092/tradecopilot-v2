def build_market_bias(timeframes):
    """
    Builds institutional higher-timeframe bias.
    """

    weights = {
        "Daily": 4,
        "H4": 3,
        "H1": 2,
        "M15": 1
    }

    bullish = 0
    bearish = 0

    summary = []

    total_weight = sum(weights.values())

    for tf, data in timeframes.items():

        structure = data.get("structure", "")

        weight = weights.get(tf, 1)

        if "Bullish" in structure:

            bullish += weight

            summary.append(f"{tf}: Bullish")

        elif "Bearish" in structure:

            bearish += weight

            summary.append(f"{tf}: Bearish")

        else:

            summary.append(f"{tf}: Neutral")

    # -----------------------------
    # Overall Bias
    # -----------------------------

    if bullish > bearish:

        bias = "Bullish"

        action = "BUY"

        dominant = bullish

    elif bearish > bullish:

        bias = "Bearish"

        action = "SELL"

        dominant = bearish

    else:

        bias = "Mixed"

        action = "WAIT"

        dominant = max(bullish, bearish)

    confidence = round((dominant / total_weight) * 100)

    # -----------------------------
    # Entry Filter
    # -----------------------------

    entry_allowed = False

    if confidence >= 80 and bias != "Mixed":

        entry_allowed = True

    recommendation = {

        "BUY": "Look for BUY setups",

        "SELL": "Look for SELL setups",

        "WAIT": "Wait for timeframe alignment"

    }[action]

    return {

        "bias": bias,

        "action": action,

        "confidence": confidence,

        "entry_allowed": entry_allowed,

        "bullish_score": bullish,

        "bearish_score": bearish,

        "summary": summary,

        "recommendation": recommendation

    }