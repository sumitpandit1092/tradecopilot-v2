import json
from datetime import datetime

from services.spread_model import get_spread

# Strategies that manage their own exits (close-confirmed stops, a
# moving-target invalidation level, etc.) instead of a fixed SL/TP2
# price level checked against intrabar high/low. refresh_open_trades()
# must NOT touch these trades -- its standard wick-based update_trade()
# check would directly contradict the strategy's own exit design (e.g.
# EMA 20/50 Cross-Retest's stop is explicitly close-only, "wick stops
# kill the trade early"). Each such strategy is responsible for its own
# per-cycle exit management via close_trade_manual().
SELF_MANAGED_STRATEGIES = {"EMA 20/50 Cross-Retest"}


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
    # 1. POSITION-LIMIT GATE (opt-in -- SMC path only)
    # =====================================================
    def _position_blocked(self, signal, timeframe, max_positions, single_side):
        """
        Per-(strategy, timeframe) exposure cap, so the SMC engine
        doesn't overtrade.

        Counts live exposure (OPEN + resting PENDING limits) scoped to
        one strategy on one entry timeframe, and blocks a new order
        when either:
          - `single_side` is set and any live trade in that scope is on
            the opposite side -- never hedge a BUY against a SELL; or
          - that scope already holds `max_positions` live trades.

        Scoped by strategy AND timeframe (not timeframe alone): broadcast
        strategies (Session Breakout, etc.) are tagged with a `timeframe`
        too now, purely so refresh_open_trades() checks them against the
        right-resolution candle -- they must NOT share an exposure count
        with the SMC engine just because both happen to trade M5.

        Up to `max_positions` SAME-side trades are still allowed (e.g. a
        market fill plus a resting limit at a better level), which is
        what makes "2 per timeframe, one direction" work without letting
        the scanner stack unlimited entries. PENDING counts so unfilled
        limits can't sneak past the cap.
        """

        strategy = signal.get("strategy", "SMC")

        active = [
            t for t in self.trades
            if t["status"] in ("OPEN", "PENDING")
            and t.get("timeframe") == timeframe
            and t.get("strategy", "SMC") == strategy
        ]

        if single_side and any(t["bias"] != signal["bias"] for t in active):
            return True

        return len(active) >= max_positions

    # =====================================================
    # 1b. SESSION-CAP / CONSECUTIVE-LOSS GATE (opt-in -- Fib Golden
    #     Zone Pullback, or any strategy that stamps a `session_label`)
    # =====================================================
    def _session_gate_blocked(self, signal, timeframe, session_label, max_per_session, max_consecutive_losses):
        """
        Enforces two "non-negotiable" risk rules that, unlike the SMC
        position cap above, are session/day-scoped rather than purely
        concurrent-exposure-scoped: max N trades per named session
        (e.g. "2026-07-09_London"), and a same-day consecutive-loss
        circuit breaker.

        Strategy functions are stateless by convention in this codebase
        (they only see candles, not trade history), so this state lives
        here in the one place that actually holds trade history --
        ExecutionEngine -- keyed off the `session_label` the strategy
        stamps onto its own signal. `session_label` is expected in the
        form "<date>_<session-name>"; the date prefix is reused to scope
        the consecutive-loss check to "today" without needing a separate
        date field.
        """

        if session_label is None:
            return False

        strategy = signal.get("strategy", "SMC")
        day = session_label.split("_")[0]

        if max_per_session is not None:
            session_trades = [
                t for t in self.trades
                if t.get("strategy", "SMC") == strategy
                and t.get("timeframe") == timeframe
                and t.get("session_label") == session_label
            ]
            if len(session_trades) >= max_per_session:
                return True

        if max_consecutive_losses is not None:
            day_trades = sorted(
                (
                    t for t in self.trades
                    if t.get("strategy", "SMC") == strategy
                    and t.get("timeframe") == timeframe
                    and t.get("status") == "CLOSED"
                    and (t.get("session_label") or "").startswith(day)
                ),
                key=lambda t: t["id"],
            )
            recent = day_trades[-max_consecutive_losses:]
            if len(recent) == max_consecutive_losses and all(t.get("result") == "LOSS" for t in recent):
                return True

        return False

    # =====================================================
    # 2. OPEN TRADE (EXECUTION ENTRY)
    # =====================================================
    def open_trade(self, signal, entry, risk, bar_index=None,
                   timeframe=None, max_positions=None, single_side=False,
                   session_label=None, max_per_session=None, max_consecutive_losses=None,
                   instrument="XAUUSD"):
        """
        Create a new trade if valid.

        By default there's no duplicate-trade blocking: this system
        broadcasts alerts to many subscribers, not a single account
        managing one position, so a repeat signal in the same direction
        should still open (and alert) again -- someone who missed the
        first entry can still catch the next one.

        The SMC path opts INTO position limits by passing `timeframe`
        + `max_positions` (+ `single_side`): at most `max_positions`
        live trades (OPEN or resting PENDING) per timeframe, and never
        an opposite-direction trade while one side is live (see
        _position_blocked()). Callers that omit `max_positions` keep the
        broadcast-everything behaviour.

        The Fib Golden Zone Pullback path opts INTO a session-scoped cap
        by passing `session_label` + `max_per_session` /
        `max_consecutive_losses` (see _session_gate_blocked()): at most
        N trades per named session, and a same-day consecutive-loss
        circuit breaker. Independent of the SMC position cap above --
        both can be active at once, or neither.

        *_LIMIT entries (a sniper entry waiting for a retracement into
        an order block) open as PENDING, not OPEN -- no capital is at
        risk and no WIN/LOSS is possible until price actually trades to
        the limit level (see refresh_pending()). *_MARKET entries fill
        immediately, same as before.

        `bar_index` is optional and only used by the backtester, to let
        a pending order expire after sitting unfilled too long.
        """

        if max_positions is not None and self._position_blocked(
            signal, timeframe, max_positions, single_side
        ):
            return None

        if (max_per_session is not None or max_consecutive_losses is not None) and self._session_gate_blocked(
            signal, timeframe, session_label, max_per_session, max_consecutive_losses
        ):
            return None

        entry_type = entry.get("entry_type")
        is_limit = isinstance(entry_type, str) and entry_type.endswith("_LIMIT")

        trade = {
            "id": len(self.trades) + 1,
            "time": str(datetime.now()),

            # Market Context
            "strategy": signal.get("strategy", "SMC"),
            "timeframe": timeframe,
            "instrument": instrument,
            "session_label": session_label,
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

            # Partial-close tracking: half the position closes at TP1
            # (banking a partial profit) and the stop moves to breakeven
            # for the other half, so a trade that later reverses closes
            # PARTIAL_WIN instead of a full LOSS -- see update_trade().
            "tp1_hit": False,
            "tp1_pnl": None,
            "original_stop_loss": None,

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
        Checks an OPEN trade against TP1, SL, and TP2, booking realized
        PnL from the actual exit price(s).

        `price` accepts either a plain price (legacy -- older callers
        and the offline tests pass a single float, treated as if
        open=high=low=close=that price, so no intrabar distinction) or
        a full candle dict for real intrabar checking.

        Partial-close at TP1: the first time TP1 is hit, half the
        position closes there (banking `tp1_pnl`) and the stop moves to
        breakeven (`original_stop_loss` keeps the pre-move value for
        reference) for the remaining half. This means a trade that
        later reverses closes PARTIAL_WIN (still net profitable from
        the TP1 half) instead of a full LOSS -- and if TP2 is reached
        afterward it's still a full WIN, just computed from two
        half-position legs instead of one. If TP1 is never hit, nothing
        changes vs before: a loss uses the full position at the
        original stop.

        Returns the trade dict on any change (TP1 just hit, or the
        trade just closed) so callers can tell the two apart via
        `status` ("OPEN" = TP1-hit update only, "CLOSED" = resolved).
        Returns None if nothing changed this call.

        FIXED (bugs found via backtest calibration, see project
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
        with a different real RR, and wrong for any loss that
        overshot the stop. PnL is now computed from
        (exit_price - entry) * position_size, direction-aware.
        """

        for t in self.trades:
            if t["id"] == trade_id and t["status"] == "OPEN":

                if isinstance(price, dict):
                    open_ = price.get("open", price["close"])
                    high = price.get("high", price["close"])
                    low = price.get("low", price["close"])
                else:
                    open_ = high = low = price

                bias = t.get("bias")

                if bias not in ("Bullish", "Bearish"):
                    # Unknown bias -- don't guess direction, leave the
                    # trade open rather than risk a wrong result.
                    return None

                entry = t.get("entry")
                position_size = t.get("position_size") or 0
                tp1 = t.get("take_profit_1")
                tp2 = t.get("take_profit_2")

                changed = False

                # --- Step 1: TP1 partial close + move stop to breakeven ---
                if not t.get("tp1_hit") and tp1 is not None and position_size and entry is not None:
                    hit_tp1 = (bias == "Bullish" and high >= tp1) or (bias == "Bearish" and low <= tp1)

                    if hit_tp1:
                        half_size = position_size / 2
                        partial_pnl = (tp1 - entry) * half_size if bias == "Bullish" else (entry - tp1) * half_size

                        t["tp1_hit"] = True
                        t["tp1_pnl"] = round(partial_pnl, 2)
                        t["original_stop_loss"] = t["stop_loss"]
                        t["stop_loss"] = entry  # breakeven for the remaining half
                        changed = True

                sl = t["stop_loss"]

                # --- Step 2: final exit check (SL/breakeven or TP2) ---
                result = None
                exit_price = None

                if bias == "Bullish":
                    if sl is not None and open_ <= sl:
                        result, exit_price = ("PARTIAL_WIN" if t["tp1_hit"] else "LOSS"), open_
                    elif sl is not None and low <= sl:
                        result, exit_price = ("PARTIAL_WIN" if t["tp1_hit"] else "LOSS"), sl
                    elif tp2 is not None and high >= tp2:
                        result, exit_price = "WIN", tp2

                else:  # Bearish
                    if sl is not None and open_ >= sl:
                        result, exit_price = ("PARTIAL_WIN" if t["tp1_hit"] else "LOSS"), open_
                    elif sl is not None and high >= sl:
                        result, exit_price = ("PARTIAL_WIN" if t["tp1_hit"] else "LOSS"), sl
                    elif tp2 is not None and low <= tp2:
                        result, exit_price = "WIN", tp2

                if result is None:
                    if changed:
                        self._save()
                        return t  # TP1 just hit, trade still open
                    return None

                remaining_size = (position_size / 2) if t["tp1_hit"] else position_size

                if entry is not None and remaining_size:
                    remainder_pnl = (exit_price - entry) * remaining_size if bias == "Bullish" \
                        else (entry - exit_price) * remaining_size
                else:
                    remainder_pnl = 0

                # Round-trip spread cost, charged on the remaining leg
                # using the exit candle's session (an approximation --
                # see spread_model.py). Skipped for legacy bare-float
                # callers with no timestamp to key the session off of.
                spread_cost = 0
                if isinstance(price, dict) and "time" in price and remaining_size:
                    spread_cost = get_spread(price["time"], t.get("instrument", "XAUUSD")) * remaining_size
                    remainder_pnl -= spread_cost

                total_pnl = (t.get("tp1_pnl") or 0) + remainder_pnl

                if entry is None or not position_size:
                    # Fallback for records missing entry/position_size
                    total_pnl = t["risk_amount"] * 2 if result == "WIN" else -t["risk_amount"]

                t["status"] = "CLOSED"
                t["result"] = result
                t["exit_price"] = round(exit_price, 2)
                t["spread_cost"] = round(spread_cost, 2)
                t["pnl"] = round(total_pnl, 2)

                self._save()
                return t

        return None

    # =====================================================
    # 2a. MANUAL CLOSE (exit rules update_trade() can't express)
    # =====================================================
    def close_trade_manual(self, trade_id, exit_price, time_str=None):
        """
        Closes an OPEN trade at an explicit price, for exit rules that
        aren't a fixed SL/TP2 price level set at entry -- e.g. the EMA
        20/50 Cross-Retest strategy's "closes beyond the 50 EMA" exit,
        where the 50 EMA itself moves every bar, and the rule is
        explicitly CLOSE-confirmed only ("wick stops kill the trade
        early" -- update_trade()'s intrabar high/low SL check is
        exactly the wick-sensitive behavior that strategy deliberately
        avoids). PnL/spread accounting mirrors update_trade()'s final-
        exit branch exactly, just triggered externally by the caller's
        own exit logic instead of comparing against stop_loss/take_profit_2.

        WIN/LOSS/PARTIAL_WIN is inferred from the actual realized PnL
        sign rather than assumed, since a moving-target exit (unlike a
        fixed SL/TP) can land on either side of breakeven.
        """

        for t in self.trades:
            if t["id"] != trade_id or t["status"] != "OPEN":
                continue

            bias = t.get("bias")
            entry = t.get("entry")
            position_size = t.get("position_size") or 0
            remaining_size = (position_size / 2) if t.get("tp1_hit") else position_size

            if entry is not None and remaining_size:
                remainder_pnl = (exit_price - entry) * remaining_size if bias == "Bullish" \
                    else (entry - exit_price) * remaining_size
            else:
                remainder_pnl = 0

            spread_cost = 0
            if time_str and remaining_size:
                spread_cost = get_spread(time_str, t.get("instrument", "XAUUSD")) * remaining_size
                remainder_pnl -= spread_cost

            total_pnl = (t.get("tp1_pnl") or 0) + remainder_pnl

            if total_pnl > 0:
                result = "WIN"
            elif t.get("tp1_hit"):
                result = "PARTIAL_WIN"
            else:
                result = "LOSS"

            t["status"] = "CLOSED"
            t["result"] = result
            t["exit_price"] = round(exit_price, 2)
            t["spread_cost"] = round(spread_cost, 2)
            t["pnl"] = round(total_pnl, 2)

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

        FIXED (real bug, found live): this used to fetch ONE candle set
        at get_xauusd_candles()'s default interval (M15) and check EVERY
        open trade against it, regardless of which timeframe the trade
        was actually opened on. An M3 trade's tight SL/TP was being
        evaluated against a much coarser M15 candle's open/high/low --
        a range covering 5x the price movement -- which could show a
        stop "hit" (or a TP2 "hit") that never happened at the M3
        resolution the trade's levels were calculated on. Now groups
        open trades by their `timeframe` field and fetches each group's
        own-resolution candle before checking it. Legacy trades with no
        `timeframe` recorded (opened before this field existed) fall
        back to M15, same as before.
        """

        open_trades = [
            t for t in self.active_trades()
            if t.get("strategy") not in SELF_MANAGED_STRATEGIES
        ]

        if not open_trades:
            return []

        # Imported lazily so the rest of ExecutionEngine's lifecycle
        # logic can be unit tested without requiring tvDatafeed to be
        # installed (see services/__init__.py for the same reasoning).
        from services.market_data import get_xauusd_candles, TIMEFRAME_INTERVALS
        from tvDatafeed import Interval

        by_timeframe = {}
        for t in open_trades:
            by_timeframe.setdefault(t.get("timeframe"), []).append(t)

        updated = []

        for timeframe, trades in by_timeframe.items():
            interval = TIMEFRAME_INTERVALS.get(timeframe, Interval.in_15_minute)
            candles = get_xauusd_candles(interval=interval)

            if not candles:
                continue

            latest_candle = candles[-1]

            for t in trades:
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
        partial_wins = sum(1 for t in self.trades if t["result"] == "PARTIAL_WIN")
        losses = sum(1 for t in self.trades if t["result"] == "LOSS")
        tp1_hits = sum(1 for t in self.trades if t.get("tp1_hit"))

        # "Executed" = actually filled (OPEN or CLOSED) -- PENDING orders
        # that never filled, and ones CANCELLED before filling, never
        # had capital at risk and shouldn't count toward win rate.
        executed = total - pending - cancelled

        pnl = sum(t["pnl"] for t in self.trades if t["pnl"] is not None)

        # PARTIAL_WIN counts toward win rate -- it's a net-profitable
        # outcome (the TP1 half banked a real gain, the remainder just
        # closed flat at breakeven), just smaller than a full TP2 win.
        winrate = ((wins + partial_wins) / executed * 100) if executed > 0 else 0

        return {
            "total_trades": total,
            "executed_trades": executed,
            "pending": pending,
            "cancelled": cancelled,
            "wins": wins,
            "partial_wins": partial_wins,
            "losses": losses,
            "tp1_hits": tp1_hits,
            "winrate": round(winrate, 2),
            "net_pnl": round(pnl, 2)
        }

    # =====================================================
    # 6. SAVE FILE
    # =====================================================
    def _save(self):
        with open(self.log_file, "w") as f:
            json.dump(self.trades, f, indent=4)