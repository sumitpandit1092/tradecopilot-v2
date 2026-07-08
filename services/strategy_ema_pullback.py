from services.atr import calculate_atr
from services.indicators import calculate_ema_series, calculate_adx

STRATEGY_NAME = "EMA Pullback"

EMA_FAST = 20
EMA_SLOW = 50
ADX_TREND_MIN = 20          # M15 ADX floor -- below this the "trend" is too weak to trust
SL_BUFFER_ATR_MULT = 0.2    # buffer beyond the pullback swing, in ATR units
TP1_RR = 1.5
TP2_RR = 3.0                # matches the 1:3 target execution_engine.py's docstring already assumes


def _trend_bias(m15_candles):
    """
    HTF trend filter: EMA20 vs EMA50 on M15, gated by ADX so a flat/
    choppy market (EMA lines crossed but directionless) doesn't count
    as a trend just because one EMA is a few cents above the other.
    """

    ema20 = calculate_ema_series(m15_candles, EMA_FAST)
    ema50 = calculate_ema_series(m15_candles, EMA_SLOW)
    adx = calculate_adx(m15_candles, 14)

    if not ema20 or not ema50 or ema20[-1] is None or ema50[-1] is None or adx is None:
        return None, None

    if adx < ADX_TREND_MIN:
        return None, adx

    if ema20[-1] > ema50[-1]:
        return "Bullish", adx
    elif ema20[-1] < ema50[-1]:
        return "Bearish", adx
    return None, adx


def build_signal(m15_candles, m5_candles, account_balance=1000, risk_percent=1.0):
    """
    Trend-following pullback: M15 EMA20/EMA50 + ADX establish a trending
    regime, then the M5 entry trigger is the exact pattern
    services/indicators.py's calculate_ema_series() docstring calls
    out -- price dips through the M5 EMA20 against the trend, then
    closes back on the trend side of it (a one-candle reclaim, not
    just "price is above the EMA").

    Entry is a direct market order at the reclaim candle's close, not
    a limit order waiting for a deeper retrace -- the reclaim candle
    IS the confirmation; waiting further risks missing the move
    entirely, same reasoning already validated for Session Breakout's
    direct-entry choice.

    SL sits just beyond the pullback swing extreme (the reclaim
    candle's opposite-side wick) plus a small ATR buffer, so a
    same-level retest doesn't immediately stop the trade out. TP1/TP2
    are RR-based (1:1.5 / 1:3) since there's no structural target like
    Session Breakout's opening range -- 1:3 matches the target
    execution_engine.py's PnL-calibration docstring already assumed
    for this strategy before it existed.
    """

    if len(m15_candles) < max(EMA_SLOW, 30) or len(m5_candles) < EMA_SLOW + 2:
        return None

    trend, adx15 = _trend_bias(m15_candles)
    if trend is None:
        return None

    ema20_m5 = calculate_ema_series(m5_candles, EMA_FAST)
    ema50_m5 = calculate_ema_series(m5_candles, EMA_SLOW)

    if ema20_m5[-1] is None or ema20_m5[-2] is None or ema50_m5[-1] is None:
        return None

    # LTF structure must agree with the HTF trend -- otherwise this is
    # a pullback within a *counter*-trend move on M5, not a
    # continuation entry.
    if trend == "Bullish" and not (ema20_m5[-1] > ema50_m5[-1]):
        return None
    if trend == "Bearish" and not (ema20_m5[-1] < ema50_m5[-1]):
        return None

    prev = m5_candles[-2]
    last = m5_candles[-1]

    atr5 = calculate_atr(m5_candles, 14)
    if atr5 is None:
        return None

    bias = None
    reasons = []

    if trend == "Bullish" and prev["low"] <= ema20_m5[-2] and last["close"] > ema20_m5[-1]:
        bias = "Bullish"
        reasons = [
            f"M15 uptrend: EMA{EMA_FAST} above EMA{EMA_SLOW}, ADX {adx15}",
            f"M5 price dipped to EMA{EMA_FAST} and closed back above it",
        ]

    elif trend == "Bearish" and prev["high"] >= ema20_m5[-2] and last["close"] < ema20_m5[-1]:
        bias = "Bearish"
        reasons = [
            f"M15 downtrend: EMA{EMA_FAST} below EMA{EMA_SLOW}, ADX {adx15}",
            f"M5 price pushed to EMA{EMA_FAST} and closed back below it",
        ]

    if bias is None:
        return None

    entry = last["close"]

    if bias == "Bullish":
        stop_loss = prev["low"] - SL_BUFFER_ATR_MULT * atr5
        sl_distance = entry - stop_loss
        if sl_distance <= 0:
            return None
        take_profit_1 = entry + sl_distance * TP1_RR
        take_profit_2 = entry + sl_distance * TP2_RR
        entry_type = "BUY_MARKET"
        recommendation = "BUY"
    else:
        stop_loss = prev["high"] + SL_BUFFER_ATR_MULT * atr5
        sl_distance = stop_loss - entry
        if sl_distance <= 0:
            return None
        take_profit_1 = entry - sl_distance * TP1_RR
        take_profit_2 = entry - sl_distance * TP2_RR
        entry_type = "SELL_MARKET"
        recommendation = "SELL"

    risk_amount = account_balance * (risk_percent / 100)
    position_size = risk_amount / sl_distance
    confidence = round(min(100, 55 + (adx15 - ADX_TREND_MIN)))

    signal = {
        "strategy": STRATEGY_NAME,
        "price": last["close"],
        "bias": bias,
        "action": recommendation,
        "entry_allowed": True,
        "confidence": confidence,
        "recommendation": recommendation,
        "reasons": reasons,
    }

    entry_data = {
        "valid": True,
        "entry": round(entry, 2),
        "entry_type": entry_type,
        "reasons": reasons,
    }

    risk_data = {
        "valid": True,
        "entry": round(entry, 2),
        "entry_type": entry_type,
        "stop_loss": round(stop_loss, 2),
        "take_profit_1": round(take_profit_1, 2),
        "take_profit_2": round(take_profit_2, 2),
        "risk_pct": risk_percent,
        "risk_amount": round(risk_amount, 2),
        "position_size": round(position_size, 4),
        "sl_distance": round(sl_distance, 2),
        "risk_reward": f"1:{TP2_RR}",
        "atr": round(atr5, 2),
        "reasons": reasons,
    }

    return signal, entry_data, risk_data
