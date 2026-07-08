from services.market_data import get_xauusd_candles
from services.atr import calculate_atr

candles = get_xauusd_candles()

atr = calculate_atr(candles)

print()

print("Current ATR:", atr)