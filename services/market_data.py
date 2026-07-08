import os
import time

from tvDatafeed import TvDatafeed, Interval

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TV_USERNAME = os.getenv("TV_USERNAME")
TV_PASSWORD = os.getenv("TV_PASSWORD")

def _connect():
    if TV_USERNAME and TV_PASSWORD:
        return TvDatafeed(TV_USERNAME, TV_PASSWORD)
    return TvDatafeed()


tv = _connect()

SYMBOL = "XAUUSD"
EXCHANGE = "FOREXCOM"
FALLBACK_EXCHANGE = "OANDA"

# Correlated instruments used for macro confluence (services/macro_context.py).
# All served by TradingView under the TVC exchange -- no extra API keys needed.
MACRO_SYMBOLS = {
    "DXY": ("DXY", "TVC"),
    "US10Y": ("US10Y", "TVC"),
}


def get_candles(symbol, exchange, interval=Interval.in_15_minute, n_bars=100,
                 retries=4, retry_delay=2, fallback_exchange=None):
    global tv

    exchanges = (exchange, fallback_exchange) if fallback_exchange else (exchange,)

    for ex in exchanges:
        for attempt in range(1, retries + 1):
            try:
                df = tv.get_hist(symbol=symbol, exchange=ex, interval=interval, n_bars=n_bars)
                if df is not None and not df.empty:
                    return _to_candles(df)
                print(f"No data from TradingView ({symbol}/{ex}, attempt {attempt}/{retries})")
            except Exception as e:
                print(f"TV Error on {symbol}/{ex}, attempt {attempt}/{retries}: {e}")

            if attempt < retries:
                # A dropped websocket ("Connection to remote host was
                # lost") doesn't recover on its own -- tvDatafeed keeps
                # reusing the same dead socket until the process
                # restarts. Reconnect before the next attempt instead
                # of retrying on a connection we already know is bad.
                try:
                    tv = _connect()
                except Exception as e:
                    print(f"Reconnect failed: {e}")
                time.sleep(retry_delay * attempt)

    print(f"All retries exhausted -- no market data available for {symbol}.")
    return []


def get_xauusd_candles(interval=Interval.in_15_minute, n_bars=100, retries=4, retry_delay=2):
    return get_candles(
        SYMBOL, EXCHANGE, interval=interval, n_bars=n_bars,
        retries=retries, retry_delay=retry_delay, fallback_exchange=FALLBACK_EXCHANGE,
    )


def get_macro_candles(key, interval=Interval.in_15_minute, n_bars=50, retries=3, retry_delay=2):
    if key not in MACRO_SYMBOLS:
        raise ValueError(f"Unknown macro symbol: {key}")
    symbol, exchange = MACRO_SYMBOLS[key]
    return get_candles(symbol, exchange, interval=interval, n_bars=n_bars, retries=retries, retry_delay=retry_delay)


def _to_candles(df):
    candles = []
    for i in range(len(df)):
        candles.append({
            "time": str(df.index[i]),
            "open": float(df["open"].iloc[i]),
            "high": float(df["high"].iloc[i]),
            "low": float(df["low"].iloc[i]),
            "close": float(df["close"].iloc[i]),
            "volume": float(df["volume"].iloc[i]) if "volume" in df else 0
        })
    return candles


def get_latest_price(candles):
    if not candles:
        return None
    return candles[-1]["close"]