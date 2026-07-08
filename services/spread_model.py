from services.session_filter import in_session
from config import SPREAD_LONDON_NY, SPREAD_ASIAN, SPREAD_SESSION_START_HOUR, SPREAD_SESSION_END_HOUR


def get_spread(time_str):
    """
    Conservative flat spread estimate (dollars per ounce), wider
    outside London/NY hours. Charged as a single round-trip cost at
    trade close (see ExecutionEngine.update_trade()) rather than
    separately at entry and exit -- mathematically equivalent for a
    linear PnL model, and avoids needing to track the entry candle's
    session separately from the exit candle's.
    """

    if in_session(time_str, SPREAD_SESSION_START_HOUR, SPREAD_SESSION_END_HOUR):
        return SPREAD_LONDON_NY
    return SPREAD_ASIAN
