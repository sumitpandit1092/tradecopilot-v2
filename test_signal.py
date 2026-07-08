from services.market_data import get_xauusd_candles
from services.signal_engine import build_signal

candles = get_xauusd_candles()

signal = build_signal(candles)

print("=" * 50)
print("MARKET BIAS")
print("=" * 50)

print(signal["market_bias"])

print()

print("=" * 50)
print("TIMEFRAMES")
print("=" * 50)

for tf, data in signal["timeframes"].items():
    print(tf, data)

print()

print("=" * 50)
print("SIGNAL")
print("=" * 50)

for k, v in signal.items():

    if k not in ["market_bias", "timeframes"]:
        print(k, ":", v)