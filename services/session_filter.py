def in_session(time_str, start_hour, end_hour):
    """
    Whether a candle's hour falls in [start_hour, end_hour). Wraps
    around midnight if start_hour > end_hour.

    CAVEAT: the hour is read directly off the candle timestamp, and
    the timezone it's actually in isn't confirmed (see config.py).
    """

    try:
        hour = int(time_str.split(" ")[1].split(":")[0])
    except (IndexError, ValueError):
        return True  # fail-open if the timestamp format is unexpected

    if start_hour <= end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour
