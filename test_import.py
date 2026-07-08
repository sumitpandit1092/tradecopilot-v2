print("Step 1")

from services.market_data import get_xauusd_candles

print("Step 2")

from services.structure_engine import (
    get_swings,
    detect_structure,
    detect_bos_choc
)

print("Step 3")

from services.timeframe_engine import analyze_timeframes

print("Step 4")