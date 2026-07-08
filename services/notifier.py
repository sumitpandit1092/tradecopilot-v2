import httpx

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


def _check(ok):
    return "✅" if ok else "❌"


def format_signal_message(signal, entry, risk, timeframe="M15"):
    """
    Builds the Telegram alert from the signal/entry/risk dicts -- no
    numbers are invented here, only formatted.

    The SMC engine and the 3 indicator-based strategies (EMA Pullback,
    BB Reversion, Session Breakout) produce differently-shaped signal
    dicts (SMC has structure/BOS/liquidity/OB/FVG/execution fields;
    the others just have bias/confidence/reasons), so this picks the
    right template based on whether `signal["strategy"]` is set.
    """

    if signal.get("strategy"):
        return _format_strategy_message(signal, entry, risk, timeframe)
    return _format_smc_message(signal, entry, risk, timeframe)


def _format_smc_message(signal, entry, risk, timeframe="M15"):
    """Institutional-desk style alert: a pass/fail confluence checklist."""

    execution = signal.get("execution", {})
    bias = signal.get("bias")

    is_bullish = bias == "Bullish"

    zone = execution.get("premium_discount_zone")
    zone_ok = (zone == "Discount") if is_bullish else (zone == "Premium")

    has_ob = bool(signal.get("bullish_ob") if is_bullish else signal.get("bearish_ob"))
    has_fvg = bool(signal.get("bullish_fvg") if is_bullish else signal.get("bearish_fvg"))
    has_sweep = bool(signal.get("sweeps"))

    lines = [
        "\U0001F6A8 <b>TRADECOPILOT SIGNAL</b> (SMC)",
        f"XAUUSD — {timeframe}",
        f"<b>{entry.get('entry_type', signal.get('recommendation'))}</b>",
        "",
        "<b>Confluence Checklist</b>",
        f"HTF Alignment       {_check(execution.get('htf_aligned'))}",
        f"Structure           {signal.get('structure')}",
        f"BOS                 {signal.get('bos') or 'None'}",
        f"Liquidity Sweep     {_check(has_sweep)}",
        f"Order Block         {_check(has_ob)}",
        f"FVG                 {_check(has_fvg)}",
        f"{'Discount' if is_bullish else 'Premium'} Zone       {_check(zone_ok)}",
        f"ATR                 {risk.get('atr')}",
        f"Risk                {_check(risk.get('valid'))}",
        "",
        f"<b>Entry</b>  {entry.get('entry')}",
        f"<b>SL</b>     {risk.get('stop_loss')}",
        f"<b>TP1</b>    {risk.get('take_profit_1')}",
        f"<b>TP2</b>    {risk.get('take_profit_2')}",
        f"<b>RR</b>     {risk.get('risk_reward')}",
        f"<b>Confidence</b>  {signal.get('confidence')}%",
        "",
        "<b>Reason</b>",
        " + ".join(signal.get("reasons", [])[:6]) or "None",
    ]

    return "\n".join(lines)


def _format_strategy_message(signal, entry, risk, timeframe="M15"):
    """Generic alert for the rule-based indicator strategies."""

    lines = [
        f"\U0001F4CA <b>TRADECOPILOT SIGNAL</b> ({signal['strategy']})",
        "XAUUSD",
        f"<b>{entry.get('entry_type', signal.get('recommendation'))}</b>",
        "",
        f"<b>Entry</b>  {entry.get('entry')}",
        f"<b>SL</b>     {risk.get('stop_loss')}",
        f"<b>TP1</b>    {risk.get('take_profit_1')}",
        f"<b>TP2</b>    {risk.get('take_profit_2')}",
        f"<b>RR</b>     {risk.get('risk_reward')}",
        f"<b>Confidence</b>  {signal.get('confidence')}%",
        "",
        "<b>Reason</b>",
        "\n".join(f"- {r}" for r in signal.get("reasons", [])) or "None",
    ]

    return "\n".join(lines)


def format_trade_filled_message(trade, timeframe="M15"):
    lines = [
        "\U0001F3AF <b>LIMIT ORDER FILLED</b>",
        f"XAUUSD — {timeframe} ({trade.get('strategy', 'SMC')})",
        f"Bias: {trade.get('bias')}",
        f"Entry: {trade.get('entry')}",
        f"Stop Loss: {trade.get('stop_loss')}",
        f"Take Profit 2: {trade.get('take_profit_2')}",
    ]

    return "\n".join(lines)


def format_trade_cancelled_message(trade, timeframe="M15"):
    lines = [
        "⚪ <b>PENDING ORDER CANCELLED</b>",
        f"XAUUSD — {timeframe} ({trade.get('strategy', 'SMC')})",
        f"Bias: {trade.get('bias')}",
        f"Limit entry never filled -- invalidated at {trade.get('invalidation')}",
    ]

    return "\n".join(lines)


def format_trade_closed_message(trade):
    result = trade.get("result")
    emoji = "✅" if result == "WIN" else "❌"

    lines = [
        f"{emoji} <b>TRADE CLOSED -- {result}</b>",
        f"XAUUSD ({trade.get('strategy', 'SMC')})",
        f"Bias: {trade.get('bias')}",
        f"Entry: {trade.get('entry')}",
        f"Stop Loss: {trade.get('stop_loss')}",
        f"Take Profit 2: {trade.get('take_profit_2')}",
        f"PnL: {trade.get('pnl')}",
    ]

    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, bot_token=None, chat_id=None):
        self.bot_token = bot_token or TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or TELEGRAM_CHAT_ID
        self.enabled = bool(self.bot_token and self.chat_id)

    def send(self, text):
        if not self.enabled:
            self._safe_print("[Telegram] Not configured -- set TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env. Skipping alert:")
            self._safe_print(text)
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

        try:
            response = httpx.post(
                url,
                data={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
            response.raise_for_status()
            return True
        except Exception as e:
            self._safe_print(f"[Telegram] Failed to send alert: {e}")
            return False

    @staticmethod
    def _safe_print(text):
        # Windows consoles are often cp1252, which can't encode the
        # emoji used in alert text -- fall back to an ASCII-safe print
        # rather than crashing the scan loop over a console echo.
        try:
            print(text)
        except UnicodeEncodeError:
            print(text.encode("ascii", errors="replace").decode("ascii"))
