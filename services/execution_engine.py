import json
from datetime import datetime

from services.spread_model import get_spread


def _parse_time(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


class ExecutionEngine:
    """
    Execution Engine V2
    -------------------
    Upgrades:
    - Trade lifecycle management (OPEN -> ACTIVE -> CLOSED)
    - Duplicate trade protection
    - Basic PnL tracking support
    - Cleaner structured journaling
    """

    def __init__(self, log_file="trade_journal.json"):
        self.log_file = log_file

        try:
            with open(self.log_file, "r") as f:
                self.trades = json.load(f)
        except:
            self.trades = []

    # =====================================================
    # 1. OPEN TRADE (EXECUTION ENTRY)
    # =====================================================
    def open_trade(self, signal, entry, risk, bar_index=None):
        """
        Create a new trade if valid.

        No duplicate-trade blocking: this system broadcasts alerts to
        many subscribers, not a single account managing one position,
        so a repeat signal in the same direction should still open (and
        alert) again -- someone who missed the first entry can still
        catch the next one.

        *_LIMIT entries (a sniper entry waiting for a retracement into
        an order block) open as PENDING, not OPEN -- no capital is at
        risk and no WIN/LOSS is possible until price actually trades to
        the limit level (see refresh_pending()). *_MARKET entries fill
        immediately, same as before.

        `bar_index` is optional and only used by the backtester, to let
        a pending order expire after sitting unfilled too long.
        """

        entry_type = entry.get("entry_type")
        is_limit = isinstance(entry_type, str) and entry_type.endswith("_LIMIT")

        trade = {
            "id": len(self.trades) + 1,
            "time": str(datetime.now()),

            # Market Context
            "strategy": signal.get("strategy", "SMC"),
            "price": signal["price"],
            "bias": signal["bias"],
            "confidence": signal["confidence"],
            "recommendation": signal["recommendation"],

            # Entry
            "entry": entry.get("entry"),
            "entry_type": entry_type,
            "limit_entry": entry.get("entry") if is_limit else None,
            "invalidation": risk.get("stop_loss") if is_limit else None,
            "placed_at_bar": bar_index,

            # Risk
            "stop_loss": risk.get("stop_loss"),
            "take_profit_1": risk.get("take_profit_1"),
            "take_profit_2": risk.get("take_profit_2"),
            "risk_pct": risk.get("risk_pct"),
            "risk_amount": risk.get("risk_amount"),
            "position_size": risk.get("position_size"),

            # Lifecycle
            "status": "PENDING" if is_limit else "OPEN",
            "result": None,
            "pnl": None,

            # Metadata
            "notes": []
        }

        self.trades.append(trade)
        self._save()

        return trade

    # =====================================================
    # 2. UPDATE TRADE STATUS
    # =====================================================
    def update_trade(self, trade_id, price):
        """
        Check an OPEN trade against SL/TP2 and close it if either has
        been hit, booking realized PnL from the actual exit price.

        `price` accepts either a plain price (legacy -- older
        callers and the offline tests pass a single float, treated as
        if open=high=low=close=that price, so no intrabar distinction)
        or a full candle dict for real intrabar checking.

        FIXED (two bugs found via backtest calibration, see project
        history): (1) the previous version only checked the candle's
        *close* against SL/TP2, so a fast intrabar push through the
        stop that recovered by the close was invisible -- a trade could
        get marked a WIN in the backtest that would have been stopped
        out in reality. Now checks high/low, and if a candle's range
        touches BOTH SL and TP2 (a wide bar), SL takes priority as the
        conservative assumption (can't know the intrabar order from
        OHLC alone). A gap that opens beyond SL is booked as a worse
        fill (exit = open), not the stop level itself.
        (2) PnL used to be a hardcoded +2x/-1x risk_amount regardless
        of the trade's *actual* TP2 distance -- correct for the SMC
        engine's fixed 1:2 target, but silently wrong for any strategy
        with a different real RR (e.g. EMA Pullback's 1:3, or the
        liquidity-based/measured-move targets used elsewhere), and
        wrong for any loss that overshot the stop. PnL is now computed
        from (exit_price - entry) * position_size, direction-aware.

        Also: direction-aware for bias (a losing SELL rallying through
        its stop no longer gets numerically misread as a WIN, which was
        the original V2 fix this docstring used to describe).
        """

        for t in self.trades:
            if t["id"] == trade_id and t["status"] == "OPEN":

                if isinstance(price, dict):
                    open_ = price.get("open", price["close"])
                    high = price.get("high", price["close"])
                    low = price.get("low", price["close"])
                else:
                    open_ = high = low = price

                sl = t["stop_loss"]
                tp2 = t["take_profit_2"]
                bias = t.get("bias")

                if bias not in ("Bullish", "Bearish"):
                    # Unknown bias -- don't guess direction, leave the
                    # trade open rather than risk a wrong result.
                    return None

                result = None
                exit_price = None

                if bias == "Bullish":
                    if sl is not None and open_ <= sl:
                        result, exit_price = "LOSS", open_        # gapped through at the open
                    elif sl is not None and low <= sl:
                        result, exit_price = "LOSS", sl           # stop-order fill assumption
                    elif tp2 is not None and high >= tp2:
                        result, exit_price = "WIN", tp2

                else:  # Bearish
                    if sl is not None and open_ >= sl:
                        result, exit_price = "LOSS", open_
                    elif sl is not None and high >= sl:
                        result, exit_price = "LOSS", sl
                    elif tp2 is not None and low <= tp2:
                        result, exit_price = "WIN", tp2

                if result is None:
                    return None

                entry = t.get("entry")
                position_size = t.get("position_size")

                if entry is not None and position_size:
                    pnl = (exit_price - entry) * position_size if bias == "Bullish" \
                        else (entry - exit_price) * position_size
                else:
                    # Fallback for records missing entry/position_size
                    pnl = t["risk_amount"] * 2 if result == "WIN" else -t["risk_amount"]

                # Round-trip spread cost, charged once at close using
                # the exit candle's session (an approximation -- see
                # spread_model.py -- but entry and exit usually fall in
                # the same broad session for these short intraday
                # trades). Skipped for legacy bare-float callers with
                # no timestamp to key the session off of.
                spread_cost = 0
                if isinstance(price, dict) and "time" in price and position_size:
                    spread_cost = get_spread(price["time"]) * position_size
                    pnl -= spread_cost

                t["status"] = "CLOSED"
                t["result"] = result
                t["exit_price"] = round(exit_price, 2)
                t["spread_cost"] = round(spread_cost, 2)
                t["pnl"] = round(pnl, 2)

                self._save()
                return t

        return None

    # =====================================================
    # 2b. REFRESH PENDING (LIMIT) ORDERS
    # =====================================================
    def refresh_pending(self, candle, bar_index=None, max_wait_bars=None, max_wait_seconds=None):
        """
        Checks every PENDING limit order against one candle's high/low
        range (a limit order fills the instant price *trades through*
        the level, not just on a close). If price reaches the limit
        entry first, it fills (-> OPEN). If price reaches the
        invalidation level (the structural stop) before ever filling,
        the order is CANCELLED -- no trade, no PnL, exactly what a real
        sniper limit order does when the setup never comes back.

        `max_wait_bars`/`bar_index` are for the backtester: a pending
        order older than `max_wait_bars` candles gets cancelled as
        stale. `max_wait_seconds` is the live-scanner equivalent (wall-
        clock based, since there's no bar index in real time) -- added
        after a Session Breakout retest order sat PENDING indefinitely
        in the live journal, blocking every future signal in that
        direction because nothing ever expired it.
        """

        filled = []
        cancelled = []

        for t in self.trades:
            if t["status"] != "PENDING":
                continue

            bias = t.get("bias")
            limit_entry = t.get("limit_entry")
            invalidation = t.get("invalidation")

            if bias == "Bullish":
                if limit_entry is not None and candle["low"] <= limit_entry:
                    t["status"] = "OPEN"
                    filled.append(t)
                    continue
                if invalidation is not None and candle["low"] <= invalidation:
                    t["status"] = "CANCELLED"
                    cancelled.append(t)
                    continue

            elif bias == "Bearish":
                if limit_entry is not None and candle["high"] >= limit_entry:
                    t["status"] = "OPEN"
                    filled.append(t)
                    continue
                if invalidation is not None and candle["high"] >= invalidation:
                    t["status"] = "CANCELLED"
                    cancelled.append(t)
                    continue

            if (
                max_wait_bars is not None
                and bar_index is not None
                and t.get("placed_at_bar") is not None
                and bar_index - t["placed_at_bar"] > max_wait_bars
            ):
                t["status"] = "CANCELLED"
                cancelled.append(t)
                continue

            if max_wait_seconds is not None:
                placed_at = _parse_time(t.get("time"))
                if placed_at and (datetime.now() - placed_at).total_seconds() > max_wait_seconds:
                    t["status"] = "CANCELLED"
                    cancelled.append(t)

        if filled or cancelled:
            self._save()

        return filled, cancelled

    # =====================================================
    # 4. GET ACTIVE TRADES
    # =====================================================
    def active_trades(self):
        return [t for t in self.trades if t["status"] == "OPEN"]

    # =====================================================
    # 4b. REFRESH OPEN TRADES  (NEW -- this was entirely missing)
    # =====================================================
    def refresh_open_trades(self):
        """
        FIXED: previously nothing in the codebase ever called
        update_trade(), so every opened trade stayed OPEN forever and
        summary()/winrate/PnL always reported 0, regardless of what
        actually happened to price.

        This pulls the latest live candle and checks every OPEN trade
        against its stop loss / take profit using that candle's actual
        high/low range (not just its close -- see update_trade()), so a
        fast intrabar push through the stop is caught the same cycle
        instead of waiting for a close beyond it. Call this once at the
        start of AI.ask() (or on a timer) before opening any new trade.
        """

        open_trades = self.active_trades()

        if not open_trades:
            return []

        # Imported lazily so the rest of ExecutionEngine's lifecycle
        # logic can be unit tested without requiring tvDatafeed to be
        # installed (see services/__init__.py for the same reasoning).
        from services.market_data import get_xauusd_candles

        candles = get_xauusd_candles()

        if not candles:
            return []

        latest_candle = candles[-1]

        updated = []

        for t in open_trades:
            result = self.update_trade(t["id"], latest_candle)
            if result:
                updated.append(result)

        return updated

    # =====================================================
    # 5. PERFORMANCE SUMMARY
    # =====================================================
    def summary(self):

        total = len(self.trades)
        pending = sum(1 for t in self.trades if t["status"] == "PENDING")
        cancelled = sum(1 for t in self.trades if t["status"] == "CANCELLED")
        wins = sum(1 for t in self.trades if t["result"] == "WIN")
        losses = sum(1 for t in self.trades if t["result"] == "LOSS")

        # "Executed" = actually filled (OPEN or CLOSED) -- PENDING orders
        # that never filled, and ones CANCELLED before filling, never
        # had capital at risk and shouldn't count toward win rate.
        executed = total - pending - cancelled

        pnl = sum(t["pnl"] for t in self.trades if t["pnl"] is not None)

        winrate = (wins / executed * 100) if executed > 0 else 0

        return {
            "total_trades": total,
            "executed_trades": executed,
            "pending": pending,
            "cancelled": cancelled,
            "wins": wins,
            "losses": losses,
            "winrate": round(winrate, 2),
            "net_pnl": round(pnl, 2)
        }

    # =====================================================
    # 6. SAVE FILE
    # =====================================================
    def _save(self):
        with open(self.log_file, "w") as f:
            json.dump(self.trades, f, indent=4)