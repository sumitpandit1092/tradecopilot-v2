import ollama

from config import MODEL_NAME, SYSTEM_PROMPT, ACCOUNT_BALANCE, RISK_PERCENT, ENABLE_MACRO_CONTEXT

from services.market_data import get_xauusd_candles
from services.signal_engine import build_signal
from services.entry_engine import build_entry
from services.risk_engine import build_risk_plan
from services.execution_engine import ExecutionEngine
from services.report_engine import build_report
from services.macro_context import build_macro_context


class AI:

    def __init__(self):

        self.executor = ExecutionEngine()

        self.messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            }
        ]

    def ask(self, prompt):

        # =====================================================
        # 0. REFRESH EXISTING TRADES
        # FIXED: previously nothing ever called update_trade(), so
        # every open trade stayed OPEN forever and summary()/winrate
        # always reported 0. Now we check open trades against the
        # latest price before doing anything else.
        # =====================================================

        self.executor.refresh_open_trades()

        # =====================================================
        # 1. MARKET DATA
        # =====================================================

        candles = get_xauusd_candles()

        if not candles:
            return "No market data available. Check your internet connection and TradingView session."

        # =====================================================
        # 2. SIGNAL ENGINE
        # =====================================================

        macro = None

        if ENABLE_MACRO_CONTEXT:
            try:
                macro = build_macro_context()
            except Exception:
                macro = None

        signal = build_signal(candles, macro=macro)

        if signal.get("error"):
            return signal["error"]

        # =====================================================
        # 3. ENTRY ENGINE
        # =====================================================

        entry = build_entry(signal, candles)

        # =====================================================
        # 4. RISK ENGINE
        # FIXED: account balance / risk % now come from config.py
        # instead of silently falling back to hardcoded defaults.
        # =====================================================

        risk = build_risk_plan(
            candles,
            signal,
            entry,
            account_balance=ACCOUNT_BALANCE,
            risk_percent=RISK_PERCENT,
        )

        # =====================================================
        # 5. TRADE VALIDATION
        # =====================================================

        trade_ready = (
            signal.get("entry_allowed", False)
            and entry.get("valid", False)
            and risk.get("valid", False)
        )

        status = "READY TO EXECUTE" if trade_ready else "WAIT FOR SETUP"

        # =====================================================
        # 6. EXECUTION ENGINE
        # =====================================================

        trade_record = None

        if trade_ready:

            trade_record = self.executor.open_trade(
                signal=signal,
                entry=entry,
                risk=risk
            )

        # =====================================================
        # 7. BUILD STRUCTURED REPORT
        # =====================================================

        report = build_report(
            signal=signal,
            entry=entry,
            risk=risk,
            trade_ready=trade_ready
        )

        # =====================================================
        # 8. LLM PROMPT
        # =====================================================

        enriched_prompt = f"""
{report}

You are TradeCopilot.

Explain ONLY the report above.

Rules:

- Never invent prices.
- Never invent liquidity.
- Never invent stop losses.
- Never invent take profits.
- Never invent invalidation.
- Never change BUY into WAIT.
- Never change WAIT into BUY.
- Never change confidence.
- Never create your own trade idea.

If the report says WAIT,
explain exactly which execution filters failed.

If the report says READY TO EXECUTE,
explain why all execution filters passed.

Keep the answer professional and under 250 words.
"""

        # =====================================================
        # 9. CALL LLM
        # FIXED: previously this call had no error handling at all --
        # if Ollama wasn't running, AI.ask() would crash instead of
        # still giving the user the raw report (which is the actual
        # source of truth and should always reach the user).
        # =====================================================

        self.messages.append(
            {
                "role": "user",
                "content": enriched_prompt
            }
        )

        try:
            response = ollama.chat(
                model=MODEL_NAME,
                messages=self.messages
            )
            answer = response["message"]["content"]

        except Exception as e:
            answer = (
                f"[AI explanation unavailable -- could not reach Ollama ({e}).\n"
                f"Make sure Ollama is running and that you've pulled the model:\n"
                f"  ollama pull {MODEL_NAME}\n"
                f"Showing the raw structured report instead:]\n\n{report}"
            )

        self.messages.append(
            {
                "role": "assistant",
                "content": answer
            }
        )

        return answer