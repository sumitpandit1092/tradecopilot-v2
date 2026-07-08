from services.market_data import get_xauusd_candles
from services.context_engine import build_market_context

candles = get_xauusd_candles()

context = build_market_context(candles)

print("=" * 40)
print("MARKET CONTEXT")
print("=" * 40)

for k, v in context.items():
    print(k, ":", v)