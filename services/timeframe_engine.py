from tvDatafeed import Interval

from services.market_data import get_xauusd_candles
from services.structure_engine import (
    get_swings,
    detect_structure,
    detect_bos_choc,
)


# Higher timeframes always analyzed for HTF bias, regardless of which
# entry timeframe the scanner is currently checking.
HIGHER_TIMEFRAMES = {
    "Daily": Interval.in_daily,
    "H4": Interval.in_4_hour,
    "H1": Interval.in_1_hour,
}


def _analyze_one(name, candles):
    if not candles:
        return {
            "price": None,
            "structure": "No Data",
            "bos": None,
            "choc": None,
            "highs": [],
            "lows": []
        }

    highs, lows = get_swings(candles)
    structure = detect_structure(highs, lows)
    bos, choc = detect_bos_choc(highs, lows)

    return {
        "price": candles[-1]["close"],
        "structure": structure,
        "bos": bos,
        "choc": choc,
        "highs": highs,
        "lows": lows
    }


def _slice_as_of(full_series, as_of, n_bars=100):
    if as_of is None:
        return full_series[-n_bars:]
    return [c for c in full_series if c["time"] <= as_of][-n_bars:]


def analyze_timeframes(m15_candles=None, entry_candles=None, entry_label="M15",
                        entry_interval=Interval.in_15_minute, htf_data=None, as_of=None):
    """
    Builds HTF (Daily/H4/H1) structure plus one "entry" timeframe.

    `entry_label`/`entry_candles`/`entry_interval` let the scanner plug
    in a faster timeframe (M5, M3) for the entry slot without it being
    mislabeled as M15 in market_bias's per-timeframe summary. The
    `m15_candles` kwarg is kept for backward compatibility (ai.py and
    existing tests) and is just entry_candles/entry_label="M15" under
    the hood.

    `htf_data`/`as_of` are for backtesting: pass in full pre-fetched
    Daily/H4/H1 candle series (`{"Daily": [...], "H4": [...], "H1": [...]}`)
    and this slices each to only bars closed at or before `as_of` --
    otherwise a backtest would score every historical bar against
    today's HTF structure (lookahead bias). Live callers leave these
    None and get a fresh fetch, same as before.
    """

    if entry_candles is None and m15_candles is not None:
        entry_candles = m15_candles
        entry_label = "M15"

    results = {}

    for name, interval in HIGHER_TIMEFRAMES.items():
        if htf_data and name in htf_data:
            candles = _slice_as_of(htf_data[name], as_of)
        else:
            candles = get_xauusd_candles(interval=interval, n_bars=100)
        results[name] = _analyze_one(name, candles)

    if entry_candles is None:
        entry_candles = get_xauusd_candles(interval=entry_interval, n_bars=100)

    results[entry_label] = _analyze_one(entry_label, entry_candles)

    return results