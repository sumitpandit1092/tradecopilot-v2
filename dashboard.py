import json
import os
from datetime import datetime

import pandas as pd
import streamlit as st

from config import SCAN_INTERVAL_SECONDS

TRADE_JOURNAL = "trade_journal.json"
SCANNER_LOG = "scanner.log"

REFRESH_INTERVAL = "5s"


st.set_page_config(page_title="TradeCopilot Dashboard", layout="wide")


def load_trades():
    if not os.path.exists(TRADE_JOURNAL):
        return []
    try:
        with open(TRADE_JOURNAL, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def load_log_tail(n=300):
    if not os.path.exists(SCANNER_LOG):
        return []
    with open(SCANNER_LOG, encoding="utf-8") as f:
        lines = f.readlines()
    return lines[-n:]


def compute_summary(trades):
    total = len(trades)
    pending = sum(1 for t in trades if t["status"] == "PENDING")
    cancelled = sum(1 for t in trades if t["status"] == "CANCELLED")
    open_now = sum(1 for t in trades if t["status"] == "OPEN")
    wins = sum(1 for t in trades if t.get("result") == "WIN")
    losses = sum(1 for t in trades if t.get("result") == "LOSS")
    executed = total - pending - cancelled
    pnl = sum(t["pnl"] for t in trades if t.get("pnl") is not None)
    winrate = (wins / executed * 100) if executed else 0

    return {
        "total": total,
        "open": open_now,
        "pending": pending,
        "cancelled": cancelled,
        "wins": wins,
        "losses": losses,
        "winrate": round(winrate, 2),
        "net_pnl": round(pnl, 2),
    }


def per_strategy_breakdown(trades):
    groups = {}
    for t in trades:
        name = t.get("strategy", "SMC")
        groups.setdefault(name, []).append(t)

    rows = []
    for name, group in groups.items():
        s = compute_summary(group)
        rows.append({
            "Strategy": name,
            "Total": s["total"],
            "Open": s["open"],
            "Pending": s["pending"],
            "Cancelled": s["cancelled"],
            "Wins": s["wins"],
            "Losses": s["losses"],
            "Win Rate %": s["winrate"],
            "Net PnL": s["net_pnl"],
        })

    return pd.DataFrame(rows)


@st.fragment(run_every=REFRESH_INTERVAL)
def render_scanner_health():
    log_lines = load_log_tail(1)

    st.subheader("Scanner Health")

    if not log_lines:
        st.warning("No scanner.log found yet -- has the scanner been started?")
        return

    last_line = log_lines[-1].strip()

    try:
        ts_str = last_line.split("]")[0].lstrip("[")
        last_ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        age_seconds = (datetime.now() - last_ts).total_seconds()
    except (ValueError, IndexError):
        age_seconds = None

    col1, col2 = st.columns([1, 3])

    with col1:
        if age_seconds is None:
            st.info("Status unknown")
        elif age_seconds <= SCAN_INTERVAL_SECONDS * 3:
            st.success(f"Running (last update {int(age_seconds)}s ago)")
        else:
            st.error(f"Possibly stopped (last update {int(age_seconds)}s ago)")

    with col2:
        st.caption("Last log line")
        st.code(last_line, language=None)


@st.fragment(run_every=REFRESH_INTERVAL)
def render_metrics():
    trades = load_trades()
    summary = compute_summary(trades)

    st.subheader("Performance Summary")

    cols = st.columns(7)
    cols[0].metric("Total Trades", summary["total"])
    cols[1].metric("Open", summary["open"])
    cols[2].metric("Pending", summary["pending"])
    cols[3].metric("Wins", summary["wins"])
    cols[4].metric("Losses", summary["losses"])
    cols[5].metric("Win Rate", f"{summary['winrate']}%")
    cols[6].metric("Net PnL", f"${summary['net_pnl']}")

    st.markdown("**By strategy**")
    breakdown = per_strategy_breakdown(trades)
    if not breakdown.empty:
        st.dataframe(breakdown, hide_index=True, use_container_width=True)
    else:
        st.caption("No trades yet.")


@st.fragment(run_every=REFRESH_INTERVAL)
def render_equity_curve():
    trades = load_trades()

    resolved = [t for t in trades if t.get("pnl") is not None]
    resolved.sort(key=lambda t: t["id"])

    st.subheader("Equity Curve (cumulative PnL)")

    if not resolved:
        st.caption("No resolved trades yet.")
        return

    cumulative = 0
    rows = []
    for t in resolved:
        cumulative += t["pnl"]
        rows.append({"Trade #": t["id"], "Cumulative PnL": round(cumulative, 2)})

    df = pd.DataFrame(rows).set_index("Trade #")
    st.line_chart(df)


@st.fragment(run_every=REFRESH_INTERVAL)
def render_trade_table():
    trades = load_trades()

    st.subheader("Trade History")

    if not trades:
        st.caption("No trades yet.")
        return

    df = pd.DataFrame(trades)
    columns = [
        "id", "time", "strategy", "bias", "recommendation", "entry_type",
        "entry", "stop_loss", "take_profit_2", "status", "result", "pnl",
    ]
    columns = [c for c in columns if c in df.columns]
    df = df[columns].sort_values("id", ascending=False)

    st.dataframe(df, hide_index=True, use_container_width=True)


@st.fragment(run_every=REFRESH_INTERVAL)
def render_live_feed():
    st.subheader("Live Signal Feed")

    lines = load_log_tail(150)

    if not lines:
        st.caption("No scanner.log found yet.")
        return

    text = "".join(reversed(lines))
    st.code(text, language=None, height=400)


st.title("TradeCopilot Dashboard")

render_scanner_health()
st.divider()
render_metrics()
st.divider()
render_equity_curve()
st.divider()

left, right = st.columns([3, 2])
with left:
    render_trade_table()
with right:
    render_live_feed()
