def build_report(signal, entry, risk, trade_ready):
    """
    TradeCopilot Report Engine V2

    Converts all engine outputs into one structured report.

    The LLM must ONLY explain this report.
    """

    execution = signal.get("execution", {})
    macro = signal.get("macro") or {}

    macro_section = ""
    if macro.get("available"):
        macro_section = f"""

MACRO CONTEXT
-------------
DXY Trend: {macro.get("dxy_trend", "N/A")}
US10Y Trend: {macro.get("us10y_trend", "N/A")}
"""

    report = f"""
==================================================
TRADECOPILOT EXECUTION REPORT
==================================================

STATUS
------
{"READY TO EXECUTE" if trade_ready else "WAIT FOR SETUP"}

MARKET
------
Price: {signal.get("price")}

Bias: {signal.get("bias")}

Action: {signal.get("action")}

Recommendation: {signal.get("recommendation")}

Confidence: {signal.get("confidence")}/100


MARKET STRUCTURE
----------------
Structure: {signal.get("structure")}

BOS: {signal.get("bos")}

CHoCH: {signal.get("choc")}
{macro_section}

EXECUTION FILTERS
-----------------
HTF Alignment:
{execution.get("htf_aligned")}

Premium / Discount Zone:
{execution.get("premium_discount_zone")}

Bullish Confluence:
{execution.get("bullish_confluence")}

Bearish Confluence:
{execution.get("bearish_confluence")}

Institutional Filter Passed:
{execution.get("institutional_filter_passed")}


ENTRY
-----
Valid:
{entry.get("valid")}

Entry Type:
{entry.get("entry_type")}

Entry Price:
{entry.get("entry")}

Invalidation:
{entry.get("invalidation")}


RISK
----
Valid:
{risk.get("valid")}

Stop Loss:
{risk.get("stop_loss")}

Take Profit 1:
{risk.get("take_profit_1")}

Take Profit 2:
{risk.get("take_profit_2")}

Risk %:
{risk.get("risk_pct")}

Risk Amount:
{risk.get("risk_amount")}

Position Size:
{risk.get("position_size")}

Risk Reward:
{risk.get("risk_reward")}

ATR:
{risk.get("atr")}


ENGINE REASONS
--------------
"""

    reasons = signal.get("reasons", [])

    if reasons:
        for reason in reasons:
            report += f"- {reason}\n"
    else:
        report += "- None\n"

    report += """

==================================================
STRICT AI RULES
==================================================

The report above is the ONLY source of truth.

DO NOT:

- invent trades
- invent prices
- invent stop losses
- invent take profits
- invent invalidation
- invent liquidity
- invent confidence
- invent reasons

If STATUS is WAIT FOR SETUP:
Explain ONLY why the filters blocked the trade.

If STATUS is READY TO EXECUTE:
Explain ONLY why the filters passed.

Never contradict the report.

Maximum response length: 180 words.
"""

    return report