def build_scalp_htf_bias(timeframes):
    """
    Fast alignment reference for scalping entry timeframes (M3/M5),
    used by signal_engine.py's htf_aligned gate -- deliberately
    separate from build_market_bias() below.

    build_market_bias() weights Daily=4/H4=3/H1=2, so Daily+H4 alone
    carry 7 of 10 points and anchor the composite to a multi-day/week
    view. During a sustained multi-week trend that composite locks to
    one direction with ~90% confidence almost by construction, and
    using it as a hard gate vetoed every counter-trend M3/M5 scalp
    regardless of how strong the actual intraday move was -- including
    genuine fast reversals, not just noise. Tried removing the gate
    entirely instead: that let through far more failed countertrend
    bounces than real reversals and net PnL got worse.

    This is the middle ground: H1 alone. It's still meaningfully
    "higher" than the M3/M5 noise floor, but it's a single timeframe
    that can flip within hours as a real intraday move develops,
    instead of waiting on the daily/weekly picture to turn. The full
    Daily/H4/H1 composite from build_market_bias() is kept as-is for
    display and as a scoring input (still worth +30 confluence points)
    -- just no longer the alignment gate.
    """

    structure = timeframes.get("H1", {}).get("structure", "")

    if "Bullish" in structure:
        return "Bullish"
    if "Bearish" in structure:
        return "Bearish"
    return "Neutral"


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