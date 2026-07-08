from services.market_data import get_xauusd_candles

candles = get_xauusd_candles()

print("Candles:", len(candles))
print("Last candle:", candles[-1])