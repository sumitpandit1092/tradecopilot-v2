from services.market_data import get_xauusd_candles
from services.signal_engine import build_signal
from services.entry_engine import build_entry
from services.risk_engine import build_risk_plan

candles = get_xauusd_candles()
signal = build_signal(candles)
print(signal)

entry = build_entry(signal, candles)
print(entry)

risk = build_risk_plan(candles, signal, entry)
print(risk)