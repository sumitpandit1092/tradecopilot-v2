# TradeCopilot — What Was Fixed & How To Run It

**Read this before running.** No trading system can guarantee profit — markets carry real risk regardless of how correct the code is. What's below is a fix pass that makes TradeCopilot run correctly end-to-end; it is not a promise of trading results.

---

## How to run it

1. Install Python 3.10+ if you don't have it.
2. Open a terminal in this folder and create a virtual environment:
   ```
   python -m venv venv
   ```
   Windows: `venv\Scripts\activate`
   Mac/Linux: `source venv/bin/activate`
3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
4. Install and start [Ollama](https://ollama.com), then pull the model referenced in `config.py`:
   ```
   ollama pull qwen2.5-coder:3b
   ```
5. Run the app:
   ```
   python app.py
   ```
6. Type a question (e.g. `what's the setup right now?`). Type `summary` to see your trade journal stats. Type `exit` or `quit` to leave.

If `tvDatafeed` fails to install via pip on your machine, install it directly from its GitHub repo instead:
```
pip install --upgrade git+https://github.com/StreamAlpha/tvdatafeed.git
```

---

## Bugs fixed in this pass

1. **Trades never closed.** Nothing in the codebase ever called `ExecutionEngine.update_trade()`. Every opened trade stayed `OPEN` forever, so `summary()` always showed 0 wins/losses/PnL no matter what actually happened to price. Added `refresh_open_trades()`, now called at the start of every `AI.ask()`.

2. **Direction-blind win/loss check (found while writing the new tests).** `update_trade()` checked `price >= tp2` for a WIN and `price <= sl` for a LOSS unconditionally. That's only correct for a BUY. For a SELL, TP2 is below entry and SL is above entry — so a losing SELL trade where price rallied straight through the stop loss could get logged as a WIN. Now direction-aware based on the trade's `bias`.

3. **BOS/CHoCH silent overwrite.** If a higher-high and a lower-low occurred in the same window, the bearish check silently overwrote a valid bullish BOS (and the same for CHoCH). Now both directions are tracked independently, and a genuine conflict is reported explicitly as `"Both (Range Expansion)"` instead of picking a side silently.

4. **Duplicate network calls.** `analyze_timeframes()` re-fetched M15 candles from TradingView even though the Signal Engine had already fetched them one line earlier — 5 network calls per question instead of 4, and a risk the two M15 candle sets were pulled at slightly different times. Now the already-fetched M15 candles are passed through and reused.

5. **`requirements.txt` was UTF-16.** Rewritten as plain UTF-8. Also added `tvdatafeed`, `pandas`, `numpy`, and `pytest`, which the code actually imports but which were missing from the file entirely.

6. **`venv/` was zipped into the project.** Removed. Never commit or zip your virtual environment — regenerate it locally with the steps above.

7. **Liquidity was computed but never used.** `detect_liquidity()` built buy-side/sell-side pools, but the Signal Engine never factored them into any score — despite liquidity being priority #1 in the trading philosophy. Pools now carry a `swept` flag (via the new `mark_swept_liquidity()`), and unswept pools now contribute to the confluence score with an explicit reason in the report.

8. **Unfilled/mitigated tracking added.** Fair Value Gaps now carry a `filled` flag, and Order Blocks now carry a `mitigated` flag (plus a minimum-displacement filter using ATR, so a 1-tick move no longer counts as an institutional Order Block). Only unfilled FVGs and unmitigated OBs are scored and shown.

9. **Crash on Ollama failure.** `ollama.chat(...)` had no error handling — if Ollama wasn't running, the whole assistant crashed. Now it falls back to showing the raw structured report (the actual source of truth) with a clear message about what went wrong.

10. **Hardcoded account size.** Risk calculations silently assumed a fixed $1,000 account regardless of your real balance. `ACCOUNT_BALANCE` and `RISK_PERCENT` are now real settings in `config.py`, threaded through to the Risk Engine.

11. **`test_risk.py` was broken.** It imported `build_trade_plan`, a function that doesn't exist (`risk_engine.py` only defines `build_risk_plan`). Fixed to use the real function and the correct call sequence (Signal → Entry → Risk).

12. **Forced dependency on `tvDatafeed` for pure logic modules.** `services/__init__.py` eagerly imported `ExecutionEngine`, which pulls in `tvDatafeed` — meaning you couldn't unit-test pure math modules like `structure_engine.py` without a working TradingView connection installed. Emptied out; nothing in the project relied on the package-level import anyway (everything already imports engines directly).

13. **Context Engine zone flipping on noise.** Premium/Discount was computed from only the single most recent internal swing, so the zone could flip on minor noise. Now uses a wider External Structure range (`get_external_structure()`) as the dealing range, falling back to the last internal swing only when there isn't enough data yet.

---

## New tests

`tests/test_offline_engines.py` — deterministic tests using synthetic candle data, no network calls, run in well under a second:
```
pip install pytest
pytest tests/test_offline_engines.py -v
```
These are regression tests for fixes #1, #2, #3, and #8 above — if any of those bugs come back, these tests will catch it immediately.

---

## Still on the roadmap (not done in this pass — see the execution plan)

- Full institutional Structure Engine rebuild (External vs Internal structure classification, Strong/Weak swing point labeling, Trend Phase classification)
- Session/kill-zone analysis, news filter, economic calendar
- Real broker integration (currently read-only market data via anonymous TradingView session — no live order placement)
- Backtesting engine
- Portfolio manager, multi-symbol support

See `TradeCopilot_Execution_Plan.md` for the full phased plan and priority order.
