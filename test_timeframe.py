from services.timeframe_engine import analyze_timeframes

results = analyze_timeframes()

print(results)

for tf, data in results.items():
    print("=" * 40)
    print(tf)
    print(data)