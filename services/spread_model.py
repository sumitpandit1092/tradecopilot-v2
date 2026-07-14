from services.session_filter import in_session
from services.market_data import INSTRUMENTS
from config import SPREAD_LONDON_NY, SPREAD_ASIAN, SPREAD_SESSION_START_HOUR, SPREAD_SESSION_END_HOUR


def get_spread(time_str, instrument="XAUUSD"):
    """
    Conservative flat spread estimate (in the instrument's own quote
    units), wider outside London/NY hours. Charged as a single
    round-trip cost at trade close (see ExecutionEngine.update_trade())
    rather than separately at entry and exit -- mathematically
    equivalent for a linear PnL model, and avoids needing to track the
    entry candle's session separately from the exit candle's.

    `instrument` defaults to XAUUSD and uses config.py's SPREAD_LONDON_NY
    / SPREAD_ASIAN (env-overridable) for it specifically, preserving
    exact prior behavior for gold. Other instruments read their
    estimate from INSTRUMENTS in market_data.py instead -- see that
    dict's comment for the "not calibrated, just directionally
    reasonable" caveat.
    """

    is_liquid_session = in_session(time_str, SPREAD_SESSION_START_HOUR, SPREAD_SESSION_END_HOUR)

    if instrument == "XAUUSD":
        return SPREAD_LONDON_NY if is_liquid_session else SPREAD_ASIAN

    cfg = INSTRUMENTS.get(instrument, INSTRUMENTS["XAUUSD"])
    return cfg["spread_london_ny"] if is_liquid_session else cfg["spread_asian"]
