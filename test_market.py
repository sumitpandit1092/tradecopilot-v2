from tvDatafeed import Interval
from services.market_data import get_xauusd_candles

print("Testing Daily...")
daily = get_xauusd_candles(interval=Interval.in_daily, n_bars=10)
print("Daily:", len(daily))

print("Testing H4...")
h4 = get_xauusd_candles(interval=Interval.in_4_hour, n_bars=10)
print("H4:", len(h4))

print("Testing H1...")
h1 = get_xauusd_candles(interval=Interval.in_1_hour, n_bars=10)
print("H1:", len(h1))

print("Testing M15...")
m15 = get_xauusd_candles(interval=Interval.in_15_minute, n_bars=10)
print("M15:", len(m15))

print("Finished")