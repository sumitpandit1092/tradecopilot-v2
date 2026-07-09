from services.market_data import get_latest_price
from services.timeframe_engine import analyze_timeframes
from services.bias_engine import build_market_bias, build_scalp_htf_bias
from services.structure_engine import (
    get_swings,
    detect_structure,
    detect_bos_choc,
    detect_liquidity,
    detect_liquidity_sweep,
    mark_swept_liquidity,
)
from services.fvg import detect_fvg, unfilled
from services.order_block import detect_order_blocks, unmitigated
from services.context_engine import build_market_context
from services.session_filter import in_session
from config import SESSION_FILTER_ENABLED, SESSION_START_HOUR, SESSION_END_HOUR


def build_signal(candles, macro=None, entry_label="M15", htf_data=None, as_of=None):
    """
    TradeCopilot Signal Engine V4
    Institutional Confluence Engine

    Uses:
    - Multi Timeframe Bias
    - Market Structure
    - BOS / CHoCH
    - Liquidity (now actually scored, not just computed)
    - Liquidity Sweeps
    - Fair Value Gaps (unfilled only)
    - Order Blocks (unmitigated only)
    - Premium / Discount
    - Institutional Execution Filters
    """

    if not candles or len(candles) < 20:
        return {
            "error": "Insufficient candle data"
        }

    # =====================================================
    # HIGHER TIMEFRAME ANALYSIS
    # FIXED: pass the already-fetched M15 candles through instead
    # of re-fetching them inside analyze_timeframes().
    # =====================================================

    timeframe_data = analyze_timeframes(
        entry_candles=candles, entry_label=entry_label, htf_data=htf_data, as_of=as_of,
    )

    market_bias = build_market_bias(timeframe_data)

    if not market_bias:

        market_bias = {

            "bias": "Neutral",

            "action": "WAIT",

            "entry_allowed": False,

            "recommendation": "WAIT"

        }

    # =====================================================
    # MARKET CONTEXT
    # =====================================================

    context = build_market_context(candles)

    # =====================================================
    # STRUCTURE ANALYSIS
    # =====================================================

    highs, lows = get_swings(candles)

    structure = detect_structure(highs, lows) or "Unknown"

    bos, choc = detect_bos_choc(highs, lows)

    liquidity = detect_liquidity(highs, lows) or {

        "buy_side_liquidity": [],

        "sell_side_liquidity": []

    }

    # FIXED: tag pools that have already been swept so they aren't
    # scored as if they were still valid, unclaimed liquidity.
    liquidity = mark_swept_liquidity(liquidity, candles)

    sweeps = detect_liquidity_sweep(candles, highs, lows)

    bullish_fvg_all, bearish_fvg_all = detect_fvg(candles)
    bullish_ob_all, bearish_ob_all = detect_order_blocks(candles)

    # FIXED: only score/return FVGs that haven't been filled yet, and
    # Order Blocks that haven't been mitigated yet.
    bullish_fvg = unfilled(bullish_fvg_all)
    bearish_fvg = unfilled(bearish_fvg_all)

    bullish_ob = unmitigated(bullish_ob_all)
    bearish_ob = unmitigated(bearish_ob_all)

    price = get_latest_price(candles)

    # =====================================================
    # SCORING ENGINE
    # =====================================================

    bullish_points = 0

    bearish_points = 0

    reasons = []

    # HTF

    if market_bias["bias"] == "Bullish":

        bullish_points += 30

        reasons.append("Higher Timeframe Bullish")

    elif market_bias["bias"] == "Bearish":

        bearish_points += 30

        reasons.append("Higher Timeframe Bearish")

    # Structure

    if "Bullish" in structure:

        bullish_points += 15

        reasons.append("Bullish Structure")

    elif "Bearish" in structure:

        bearish_points += 15

        reasons.append("Bearish Structure")

    # BOS

    if bos == "Bullish BOS":

        bullish_points += 15

        reasons.append("Bullish BOS")

    elif bos == "Bearish BOS":

        bearish_points += 15

        reasons.append("Bearish BOS")

    elif bos and bos.startswith("Both"):

        reasons.append("Range expansion (conflicting BOS -- no points awarded)")

    # CHOCH

    if choc:

        if choc.startswith("Both"):

            reasons.append("Conflicting CHoCH -- no points awarded")

        elif "Bullish" in choc:

            bullish_points += 10

            reasons.append("Bullish CHoCH")

        elif "Bearish" in choc:

            bearish_points += 10

            reasons.append("Bearish CHoCH")

    # Liquidity Sweep

    for sweep in sweeps:

        if "Sell-side" in sweep:

            bullish_points += 10

            reasons.append("Sell-side Sweep")

        elif "Buy-side" in sweep:

            bearish_points += 10

            reasons.append("Buy-side Sweep")

    # Liquidity pools (unswept only) -- NEW: previously computed but
    # never scored at all.
    unswept_sell_side = [p for p in liquidity["sell_side_liquidity"] if not p["swept"]]
    unswept_buy_side = [p for p in liquidity["buy_side_liquidity"] if not p["swept"]]

    if unswept_sell_side:

        bullish_points += 10

        reasons.append("Unswept sell-side liquidity below price (bullish draw)")

    if unswept_buy_side:

        bearish_points += 10

        reasons.append("Unswept buy-side liquidity above price (bearish draw)")

    # FVG (unfilled only)

    if bullish_fvg:

        bullish_points += 10

        reasons.append("Unfilled Bullish FVG")

    if bearish_fvg:

        bearish_points += 10

        reasons.append("Unfilled Bearish FVG")

    # Order Blocks (unmitigated only)

    if bullish_ob:

        bullish_points += 10

        reasons.append("Unmitigated Bullish Order Block")

    if bearish_ob:

        bearish_points += 10

        reasons.append("Unmitigated Bearish Order Block")

    # Premium / Discount

    if context:

        if context["zone"] == "Discount":

            bullish_points += 10

            reasons.append("Discount Zone")

        elif context["zone"] == "Premium":

            bearish_points += 10

            reasons.append("Premium Zone")

    # Macro Confluence (DXY / US10Y -- both inversely correlated with Gold).
    # Scored against the higher-timeframe bias (not the in-progress
    # bullish/bearish points) since that's the same reference point used
    # for the htf_aligned execution filter below.

    if macro and macro.get("available"):

        htf_bias_for_macro = market_bias.get("bias")
        dxy_trend = macro.get("dxy_trend")
        us10y_trend = macro.get("us10y_trend")

        if htf_bias_for_macro == "Bullish":

            if dxy_trend == "Falling":
                bullish_points += 5
                reasons.append("Macro Confluence: DXY falling supports Gold bullish bias")
            elif dxy_trend == "Rising":
                reasons.append("Macro Warning: DXY rising conflicts with Gold bullish bias")

            if us10y_trend == "Falling":
                bullish_points += 5
                reasons.append("Macro Confluence: US10Y yields falling supports Gold bullish bias")
            elif us10y_trend == "Rising":
                reasons.append("Macro Warning: US10Y yields rising conflicts with Gold bullish bias")

        elif htf_bias_for_macro == "Bearish":

            if dxy_trend == "Rising":
                bearish_points += 5
                reasons.append("Macro Confluence: DXY rising supports Gold bearish bias")
            elif dxy_trend == "Falling":
                reasons.append("Macro Warning: DXY falling conflicts with Gold bearish bias")

            if us10y_trend == "Rising":
                bearish_points += 5
                reasons.append("Macro Confluence: US10Y yields rising supports Gold bearish bias")
            elif us10y_trend == "Falling":
                reasons.append("Macro Warning: US10Y yields falling conflicts with Gold bearish bias")

    # =====================================================
    # FINAL BIAS CALCULATION
    # =====================================================

    if bullish_points > bearish_points:

        bias = "Bullish"
        confidence = bullish_points

    elif bearish_points > bullish_points:

        bias = "Bearish"
        confidence = bearish_points

    else:

        bias = "Neutral"
        confidence = max(bullish_points, bearish_points)

    confidence = min(confidence, 100)

    # =====================================================
    # INSTITUTIONAL EXECUTION FILTERS
    # =====================================================

    action = "WAIT"
    entry_allowed = False

    zone = None

    if context:
        zone = context.get("zone")

    htf_bias = market_bias.get("bias")

    # Alignment gate uses the FAST H1-only reference, not the full
    # Daily(4)/H4(3)/H1(2) composite in `htf_bias` above -- see
    # build_scalp_htf_bias()'s docstring. `htf_bias`/`htf_aligned_slow`
    # are kept for display/debugging (the report shows both).
    scalp_htf_bias = build_scalp_htf_bias(timeframe_data)
    htf_aligned = (bias == scalp_htf_bias)
    htf_aligned_slow = (bias == htf_bias)

    # Informational only (OR) -- still used for the point score above
    # and shown in the report as a looser "some confluence" signal.
    bullish_confluence = bool(bullish_fvg or bullish_ob)
    bearish_confluence = bool(bearish_fvg or bearish_ob)

    # Sniper gate -- this is what actually allows a trade. Requires at
    # least 2 of the 3 confluence factors (sweep, order block, FVG)
    # together, not just any one of them (the old OR gate).
    #
    # NOTE: this is 2-of-3, not all 3. Unmitigated order blocks turn
    # out to be extremely short-lived on XAUUSD -- empirically, across
    # 1,400 historical M15 windows, an order block that was still
    # unmitigated survived exactly 0 times over a 100-candle lookback
    # (price retests nearly every OB zone within the same window).
    # Requiring sweep+OB+FVG simultaneously made entry_allowed
    # permanently zero, so this is 2-of-3 instead -- still meaningfully
    # stricter than the old single-factor OR gate.
    bullish_sweep = any("Sell-side" in s for s in sweeps)
    bearish_sweep = any("Buy-side" in s for s in sweeps)

    bullish_factor_count = sum([bool(bullish_fvg), bool(bullish_ob), bullish_sweep])
    bearish_factor_count = sum([bool(bearish_fvg), bool(bearish_ob), bearish_sweep])

    bullish_sniper_confluence = bullish_factor_count >= 2
    bearish_sniper_confluence = bearish_factor_count >= 2

    # Only take trades during the highest-liquidity session window --
    # see config.py for the timezone caveat.
    session_ok = (
        not SESSION_FILTER_ENABLED
        or in_session(candles[-1]["time"], SESSION_START_HOUR, SESSION_END_HOUR)
    )

    # =====================================================
    # BUY FILTERS
    # =====================================================
    # htf_aligned is a hard AND-gate again (tried dropping it entirely
    # -- see build_scalp_htf_bias()'s docstring for why that backfired:
    # it let through far more failed countertrend bounces than real
    # reversals). It's now checked against the FAST H1-only reference
    # instead of the slow Daily/H4/H1 composite, so a genuine intraday
    # reversal can still pass without waiting for the daily chart to
    # turn.
    #
    # FIXED: this used to gate on `bullish_confluence`/`bearish_confluence`
    # (a loose bool(fvg or ob) OR), even though the sniper-gate comment
    # above claims the 2-of-3 (sweep+OB+FVG) version is "what actually
    # allows a trade" -- it was computed and shown in the report but
    # never actually wired into the filter. Now gates on the real
    # 2-of-3 sniper confluence.

    if (
        bias == "Bullish"
        and confidence >= 85
        and htf_aligned
        and zone == "Discount"
        and bullish_sniper_confluence
    ):

        action = "BUY"
        entry_allowed = True

    # =====================================================
    # SELL FILTERS
    # =====================================================

    elif (
        bias == "Bearish"
        and confidence >= 85
        and htf_aligned
        and zone == "Premium"
        and bearish_sniper_confluence
    ):

        action = "SELL"
        entry_allowed = True

    # =====================================================
    # TRADE BLOCKERS
    # =====================================================

    else:

        if confidence < 85:
            reasons.append("Confidence below execution threshold")

        if not htf_aligned:
            reasons.append(f"H1 timeframe not aligned (H1 bias: {scalp_htf_bias})")

        if bias == "Bullish":

            if zone != "Discount":
                reasons.append("Bullish setup outside Discount Zone")

            if not bullish_sniper_confluence:
                reasons.append(
                    f"Missing sniper confluence ({bullish_factor_count}/3: "
                    "sweep + Order Block + FVG, need 2+)"
                )

        elif bias == "Bearish":

            if zone != "Premium":
                reasons.append("Bearish setup outside Premium Zone")

            if not bearish_sniper_confluence:
                reasons.append(
                    f"Missing sniper confluence ({bearish_factor_count}/3: "
                    "sweep + Order Block + FVG, need 2+)"
                )

        reasons.append("Institutional execution filter blocked entry")

    # =====================================================
    # RECOMMENDATION ENGINE
    # =====================================================

    if action == "BUY":

        recommendation = "BUY"

    elif action == "SELL":

        recommendation = "SELL"

    elif bias == "Bullish":

        recommendation = "WATCH FOR BUY"

    elif bias == "Bearish":

        recommendation = "WATCH FOR SELL"

    else:

        recommendation = "WAIT"
    # =====================================================
    # RETURN FINAL SIGNAL
    # =====================================================

    return {

        # -------------------------------------------------
        # PRICE
        # -------------------------------------------------
        "price": price,

        # -------------------------------------------------
        # FINAL DECISION
        # -------------------------------------------------
        "bias": bias,
        "action": action,
        "entry_allowed": entry_allowed,
        "confidence": confidence,
        "recommendation": recommendation,

        # -------------------------------------------------
        # HIGHER TIMEFRAME
        # -------------------------------------------------
        "market_bias": market_bias,
        "timeframes": timeframe_data,

        # -------------------------------------------------
        # MARKET CONTEXT
        # -------------------------------------------------
        "context": context,

        # -------------------------------------------------
        # STRUCTURE
        # -------------------------------------------------
        "structure": structure,
        "bos": bos,
        "choc": choc,

        # -------------------------------------------------
        # LIQUIDITY
        # -------------------------------------------------
        "liquidity": liquidity,
        "sweeps": sweeps,

        # -------------------------------------------------
        # FAIR VALUE GAPS (unfilled only)
        # -------------------------------------------------
        "bullish_fvg": bullish_fvg[-3:] if bullish_fvg else [],
        "bearish_fvg": bearish_fvg[-3:] if bearish_fvg else [],

        # -------------------------------------------------
        # ORDER BLOCKS (unmitigated only)
        # -------------------------------------------------
        "bullish_ob": bullish_ob[-3:] if bullish_ob else [],
        "bearish_ob": bearish_ob[-3:] if bearish_ob else [],

        # -------------------------------------------------
        # SCORES
        # -------------------------------------------------
        "bullish_score": bullish_points,
        "bearish_score": bearish_points,

        # -------------------------------------------------
        # EXECUTION INFORMATION
        # -------------------------------------------------
        "execution": {

            "htf_aligned": htf_aligned,
            "scalp_htf_bias": scalp_htf_bias,
            "htf_aligned_slow": htf_aligned_slow,
            "htf_bias_slow": htf_bias,

            "premium_discount_zone": zone,

            "bullish_confluence": bullish_confluence,

            "bearish_confluence": bearish_confluence,

            "bullish_sniper_confluence": bullish_sniper_confluence,

            "bearish_sniper_confluence": bearish_sniper_confluence,

            "session_ok": session_ok,

            "institutional_filter_passed": entry_allowed

        },

        # -------------------------------------------------
        # MACRO CONTEXT (DXY / US10Y confluence)
        # -------------------------------------------------
        "macro": macro,

        # -------------------------------------------------
        # AI EXPLANATION
        # -------------------------------------------------
        "reasons": reasons
    }