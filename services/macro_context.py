from services.market_data import get_macro_candles


def _trend(candles, lookback):
    if not candles or len(candles) <= lookback:
        return None, None

    now = candles[-1]["close"]
    then = candles[-lookback]["close"]

    if now > then:
        trend = "Rising"
    elif now < then:
        trend = "Falling"
    else:
        trend = "Flat"

    return trend, round(now, 2)


def build_macro_context(lookback=10):
    """
    Correlated-market context for Gold (XAUUSD):

    - DXY (US Dollar Index): inverse correlation with Gold.
    - US10Y (10-Year Treasury Yield): inverse correlation with Gold.

    Returns trend direction only -- signal_engine.py decides what that
    means relative to Gold's own bias. Never raises; any fetch failure
    just means that instrument is left out of the returned dict.
    """

    context = {"available": False}

    try:
        dxy_candles = get_macro_candles("DXY", n_bars=lookback + 5)
        dxy_trend, dxy_price = _trend(dxy_candles, lookback)
        if dxy_trend:
            context["dxy_trend"] = dxy_trend
            context["dxy_price"] = dxy_price
    except Exception as e:
        context["dxy_error"] = str(e)

    try:
        us10y_candles = get_macro_candles("US10Y", n_bars=lookback + 5)
        us10y_trend, us10y_price = _trend(us10y_candles, lookback)
        if us10y_trend:
            context["us10y_trend"] = us10y_trend
            context["us10y_price"] = us10y_price
    except Exception as e:
        context["us10y_error"] = str(e)

    context["available"] = "dxy_trend" in context or "us10y_trend" in context

    return context
