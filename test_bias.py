from services.timeframe_engine import analyze_timeframes
from services.bias_engine import build_market_bias

timeframes = analyze_timeframes()

bias = build_market_bias(timeframes)

print("\n========== MARKET BIAS ==========")
print("Bias:", bias["bias"])
print("Bullish Score:", bias["bullish_score"])
print("Bearish Score:", bias["bearish_score"])

print("\nTimeframes:")
for item in bias["summary"]:
    print("-", item)

print("\nRecommendation:")
print(bias["recommendation"])