import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# =====================================================
# TELEGRAM ALERTS
# Set these in a .env file (see .env.example) -- never commit real
# tokens. If either is missing, the notifier logs a warning and skips
# sending instead of crashing the scanner.
# =====================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# =====================================================
# SCANNER
# =====================================================
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "60"))

# How long a PENDING limit order can sit unfilled before it's cancelled
# as stale. Without this, a retest-style limit order that price never
# returns to would block every future signal in that direction forever
# (found live: an orphaned Session Breakout order sat PENDING for
# hours). Default 2 hours.
PENDING_MAX_WAIT_SECONDS = int(os.getenv("PENDING_MAX_WAIT_SECONDS", "7200"))

# Correlated-market confluence (DXY / US10Y via TradingView). Set to
# "false" in .env to disable if it's adding noise or the extra network
# calls are unwanted.
ENABLE_MACRO_CONTEXT = os.getenv("ENABLE_MACRO_CONTEXT", "true").lower() == "true"

# =====================================================
# SESSION FILTER
# Only take trades during the highest-liquidity window (London/NY
# overlap) instead of scanning all 24 hours equally -- low-liquidity
# hours produce noisier structure and more fakeouts.
#
# Disabled by default -- backtesting showed the pre-session-filter SMC
# engine performed better on the available data, so this is now opt-in
# rather than on by default. signal_engine.py still computes
# execution["session_ok"] for informational display either way.
#
# CAVEAT: the hour is read directly off tvDatafeed's candle timestamp,
# and it's NOT confirmed what timezone that actually is (it may be
# UTC, or it may be shifted by whatever timezone the machine running
# this is in). Default below assumes UTC (12:00-16:00 = London/NY
# overlap). If live alerts seem to cluster at the wrong time of day,
# adjust these two hours in .env until they line up with your actual
# market hours.
# =====================================================
SESSION_FILTER_ENABLED = os.getenv("SESSION_FILTER_ENABLED", "false").lower() == "true"
SESSION_START_HOUR = int(os.getenv("SESSION_START_HOUR", "12"))
SESSION_END_HOUR = int(os.getenv("SESSION_END_HOUR", "16"))

# =====================================================
# SPREAD MODEL (backtesting realism)
# XAUUSD spread is tighter during London/NY hours and widens overnight
# / during the Asian session (and further during news). These are
# conservative flat estimates in dollars per ounce, charged as a
# round-trip cost on every closed trade -- see execution_engine.py's
# update_trade(). Same timezone caveat as SESSION_FILTER above.
# =====================================================
SPREAD_LONDON_NY = float(os.getenv("SPREAD_LONDON_NY", "0.20"))
SPREAD_ASIAN = float(os.getenv("SPREAD_ASIAN", "0.40"))
SPREAD_SESSION_START_HOUR = int(os.getenv("SPREAD_SESSION_START_HOUR", "7"))
SPREAD_SESSION_END_HOUR = int(os.getenv("SPREAD_SESSION_END_HOUR", "21"))


MODEL_NAME = "qwen2.5-coder:3b"
# NOTE: this is a code-tuned model being used to explain trading
# reports. It may follow the "explain-only, never invent numbers"
# system prompt less reliably than a general-instruct model. If you
# have `ollama pull llama3.1:8b-instruct` (or similar) available,
# try switching MODEL_NAME to that and compare output quality.

# =====================================================
# ACCOUNT / RISK SETTINGS
# FIXED: previously these were hardcoded defaults inside
# risk_engine.py (account_balance=1000, risk_percent=1.0) and never
# actually overridden from anywhere -- every risk plan silently
# assumed a fixed $1,000 account no matter what. Set your real
# values here; ai.py now reads them from this file.
# =====================================================
ACCOUNT_BALANCE = 1000      # your real account balance in USD
RISK_PERCENT = 1.0          # percent of account risked per trade


SYSTEM_PROMPT = """
You are TradeCopilot.

The trading engine is always correct.

Never modify:

- Bias
- Action
- Confidence
- Entry
- Stop Loss
- Take Profit
- Invalidation
- Recommendation

Never invent:

- Market analysis
- BOS explanations
- CHoCH explanations
- Liquidity explanations

Your only job is to rewrite the supplied report into a clean professional format.

Never output anything that is not present in the report.

Never contradict the report.

Never add educational content.

Keep responses under 250 words.
"""
