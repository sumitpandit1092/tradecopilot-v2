# TradeCopilot — Master Execution Plan (v1)

Single source of truth for what to build next, in order. Work top to bottom. Do not skip a phase — several later phases silently depend on bugs fixed in Phase 1.

---

## 0. Audit Summary (what's actually wrong today)

Reviewed: `ai.py`, `config.py`, all of `services/*.py`, `test_*.py`, `requirements.txt`, `trade_journal.json`.

| # | Issue | File | Impact |
|---|-------|------|--------|
| 1 | `test_risk.py` imports `build_trade_plan`, which doesn't exist (`risk_engine.py` only defines `build_risk_plan`) | test_risk.py | Test is dead, gives false confidence that risk layer is tested |
| 2 | No code ever calls `ExecutionEngine.update_trade()` | execution_engine.py | Every trade stays `OPEN` forever. Win rate / PnL always report 0 |
| 3 | `detect_bos_choc()`: bearish check overwrites bullish `bos` if both fire in the same window | structure_engine.py | Silent data loss — a real Bullish BOS can vanish |
| 4 | `build_signal()` re-fetches Daily/H4/H1/M15 candles inside `analyze_timeframes()`, on top of the candles `ai.py` already fetched | signal_engine.py, timeframe_engine.py | 5 network calls per single user question; rate-limit risk; possible timing mismatch between the "current" candle set and the HTF set |
| 5 | `requirements.txt` saved as UTF-16 | requirements.txt | `pip install -r requirements.txt` can fail depending on environment |
| 6 | Full `venv/` folder shipped inside the project zip | — | Hundreds of MB of unrelated binaries; never commit this |
| 7 | `detect_liquidity()` (buy/sell-side pools) is computed but never scored in `build_signal()` | signal_engine.py | Liquidity — priority #1 in the trading philosophy — currently contributes 0 points to any decision |
| 8 | `ollama.chat(...)` called with no error handling | ai.py | If Ollama isn't running, the whole assistant crashes instead of falling back to the raw report |
| 9 | No `main.py` / CLI / API entry point anywhere | — | Only way to run the system is executing individual test files by hand |
| 10 | `account_balance=1000, risk_percent=1.0` hardcoded defaults, never overridden from `ai.py` | risk_engine.py, ai.py | Every risk plan silently assumes a fixed $1,000 account regardless of the real account |
| 11 | `services/__init__.py` only exports `ExecutionEngine` | services/__init__.py | Inconsistent — other engines imported by full path elsewhere |
| 12 | Confidence numbers mean two different things in two places: `bias_engine.py`'s 0–100 HTF-weighted confidence vs `signal_engine.py`'s own 0–100 point-scoring confidence | bias_engine.py, signal_engine.py | Confusing to reason about; `bias_engine`'s own `entry_allowed` flag is computed but never actually used downstream |

Fix order below addresses these in Phase 1 before any new feature work.

---

## PHASE 0 — Environment & Repo Hygiene (30 min)

- [ ] Re-save `requirements.txt` as plain UTF-8 (`pip freeze > requirements.txt` from an activated venv, or re-save via a plain text editor set to UTF-8 no-BOM).
- [ ] Add a `.gitignore` / zip-ignore with at minimum:
  ```
  venv/
  __pycache__/
  *.pyc
  trade_journal.json
  .env
  ```
- [ ] Never zip/commit `venv/` again — regenerate it locally with `python -m venv venv` + `pip install -r requirements.txt`.
- [ ] Move `trade_journal.json` out of version control (it's runtime state, not source) — keep a `trade_journal.example.json` with an empty `[]` instead.
- [ ] Confirm `.env` exists for future broker/API keys (you already list `python-dotenv` in requirements but never call `load_dotenv()` anywhere — either use it now for future keys or drop the dependency).

**Acceptance:** fresh `pip install -r requirements.txt` succeeds on a clean machine; zip size drops from hundreds of MB to a few hundred KB.

---

## PHASE 1 — Critical Bug Fixes (do first, before touching Structure Engine)

### 1.1 Fix `detect_bos_choc()` — stop silent overwrite
Track bullish and bearish BOS independently instead of one shared variable:
```python
def detect_bos_choc(highs, lows):
    bullish_bos = len(highs) >= 2 and highs[-1] > highs[-2]
    bearish_bos = len(lows) >= 2 and lows[-1] < lows[-2]

    if bullish_bos and bearish_bos:
        bos = "Both (Broadening/Expansion)"
    elif bullish_bos:
        bos = "Bullish BOS"
    elif bearish_bos:
        bos = "Bearish BOS"
    else:
        bos = None
    ...
```
Downstream (`signal_engine.py`) should treat `"Both"` as a no-score / neutral case, not silently pick one side.

### 1.2 Eliminate duplicate candle fetching
`analyze_timeframes()` should accept the already-fetched M15 candles instead of re-pulling them, and `build_signal()` should call it once and reuse the result:
```python
def analyze_timeframes(m15_candles=None):
    ...
    for name, interval in TIMEFRAMES.items():
        if name == "M15" and m15_candles:
            candles = m15_candles
        else:
            candles = get_xauusd_candles(interval=interval, n_bars=100)
        ...
```
Cuts network calls per question from 5 to 4 (Daily/H4/H1 still need their own pulls; M15 is reused).

### 1.3 Wire up trade lifecycle updates
Add a `refresh_open_trades(current_price)` step that runs before every `ask()` call (or on a timer):
```python
def refresh_open_trades(self):
    price = get_latest_price(get_xauusd_candles())
    if price is None:
        return
    for t in self.active_trades():
        self.update_trade(t["id"], price)
```
Call `self.executor.refresh_open_trades()` at the top of `AI.ask()`, before opening any new trade. This makes `summary()` actually mean something.

### 1.4 Graceful LLM failure
Wrap the `ollama.chat(...)` call:
```python
try:
    response = ollama.chat(model=MODEL_NAME, messages=self.messages)
    answer = response["message"]["content"]
except Exception as e:
    answer = f"[AI explanation unavailable — Ollama error: {e}]\n\n{report}"
```
The raw report must always reach the user even if the LLM layer is down — that's the whole point of Report Engine being the source of truth.

### 1.5 Make account balance / risk % real inputs
Add them to `config.py` (or a `.env`-backed settings object) and thread through `ai.py → build_risk_plan(...)` instead of relying on function defaults. Minimum:
```python
# config.py
ACCOUNT_BALANCE = 1000
RISK_PERCENT = 1.0
```
```python
# ai.py
risk = build_risk_plan(candles, signal, entry,
                        account_balance=ACCOUNT_BALANCE,
                        risk_percent=RISK_PERCENT)
```

### 1.6 Delete or fix `test_risk.py`
Replace `build_trade_plan` with the real function name:
```python
from services.risk_engine import build_risk_plan
...
risk = build_risk_plan(candles, signal, entry)
```
(Note: `test_risk.py` never builds an `entry` today — it must call `build_entry(signal)` first, same sequence as `ai.py`.)

**Acceptance:** all `test_*.py` files run to completion with no import errors; a trade opened in a test run can transition to `CLOSED` when you feed it a price past TP2 or SL.

---

## PHASE 2 — Rebuild Structure Engine (per your own roadmap: highest priority)

Current `structure_engine.py` only does swing detection + basic HH/HL/LH/LL + a naive BOS/CHoCH + one liquidity-pool pass. Your master prompt calls for a full institutional Structure Engine. Build it as a **separate internal module set** so nothing else breaks:

- [ ] `structure/swings.py` — keep current fractal swing logic, but make `lookback` and minimum-bar-count configurable and add a fallback for thin data (currently returns empty silently if `len(candles) < 2*lookback+1`).
- [ ] `structure/external_internal.py` — separate **External Structure** (major swing highs/lows across the full window) from **Internal Structure** (minor swings inside the current external range). This is currently entirely missing — today there's only one flat structure read.
- [ ] `structure/bos_choc.py` — use the Phase 1.1 fix as the base, then extend CHoCH detection to require a structural break *through* a prior opposite swing point, not just three-point comparison (current 3-point CHoCH logic is a simplification that can misfire on noisy ranges).
- [ ] `structure/strong_weak_points.py` — classify each swing high/low as **Strong** or **Weak** based on whether liquidity beyond it has already been swept (feed in `detect_liquidity_sweep` output from Phase 1's independent sweep detector).
- [ ] `structure/dealing_range.py` — formalize Premium/Discount/Equilibrium as a function of the **current External Structure range**, not just the single latest swing high/low pair (current `context_engine.py` uses only the very last swing, which flips zone too easily on noise).
- [ ] `structure/trend_phase.py` — classify Accumulation / Distribution / Continuation / Reversal from the sequence of BOS/CHoCH + internal structure over the last N swings.

Keep `get_swings`, `detect_structure`, `detect_bos_choc` as the public API other engines already import — internally delegate to the new submodules so `signal_engine.py`, `context_engine.py`, `timeframe_engine.py` don't need changes yet.

**Acceptance:** `test_timeframe.py` output shows External vs Internal structure per timeframe, and Strong/Weak labels on the last 3 swing points for each.

---

## PHASE 3 — Upgrade Liquidity Engine (2nd priority)

- [ ] Feed `detect_liquidity()` output (`buy_side_liquidity`, `sell_side_liquidity`) into `build_signal()`'s scoring — currently computed, never scored (Bug #7). Add points when a Discount-zone Bullish setup sits below unswept sell-side liquidity, and mirror for Bearish/Premium.
- [ ] Distinguish **Equal Highs/Equal Lows** (already partially done via tolerance) from **Old Highs/Old Lows** liquidity — right now everything within tolerance is lumped together.
- [ ] Track whether a liquidity pool has already been swept (`detect_liquidity_sweep`) so it isn't scored twice as still-valid liquidity — currently `detect_liquidity` and `detect_liquidity_sweep` run independently with no shared state.

**Acceptance:** `signal["reasons"]` includes explicit liquidity-pool reasons (e.g. `"Entry below unswept sell-side liquidity"`), not just FVG/OB reasons.

---

## PHASE 4 — Improve Order Block Detection (3rd priority)

- [ ] Current `detect_order_blocks()` only checks a single-candle "last opposite candle before a strong move" pattern. Add:
  - Minimum displacement filter (require the breakout candle's range to exceed N × ATR, using `atr.py`, so a 1-tick move doesn't count as a valid OB).
  - Mitigation tracking — mark an OB as "mitigated" once price has returned into it, so `entry_engine.py` doesn't keep quoting a stale OB from days ago.
  - Breaker Block detection (an OB that failed and flipped polarity) — currently entirely absent.

**Acceptance:** each OB returned includes a `"mitigated": bool` field; `entry_engine.py` only uses unmitigated OBs for `invalidation`.

---

## PHASE 5 — Improve Fair Value Gap Detection (4th priority)

- [ ] Current `detect_fvg()` is a correct basic 3-candle model but never checks whether a gap has since been filled. Add a `"filled": bool` field by scanning subsequent candles for price returning into the gap range.
- [ ] Only score/return unfilled FVGs to `signal_engine.py` and `entry_engine.py`.

**Acceptance:** `signal["bullish_fvg"]` / `signal["bearish_fvg"]` never include fully-filled gaps.

---

## PHASE 6 — Improve Context Engine (5th priority)

- [ ] Replace the single-swing Premium/Discount calc with the Dealing Range from Phase 2 (External Structure high/low), so zone classification is stable across the whole current range instead of flipping on the latest fractal.
- [ ] Add explicit `"dealing_range_high"` / `"dealing_range_low"` fields to the context output for the Report Engine to surface.

**Acceptance:** `test_context.py` output shows a zone that only changes when structure actually shifts (a new external high/low forms), not every time a minor swing updates.

---

## PHASE 7 — Execution Loop (system currently has none)

Right now nothing runs continuously. Add:
- [ ] `main.py` — a simple loop/CLI:
  ```python
  from ai import AI

  def main():
      bot = AI()
      while True:
          prompt = input("\nAsk TradeCopilot > ")
          if prompt.lower() in ("exit", "quit"):
              break
          print(bot.ask(prompt))

  if __name__ == "__main__":
      main()
  ```
- [ ] Inside `AI.ask()` (or a scheduler thread), call `self.executor.refresh_open_trades()` (Phase 1.3) before generating a new report, so every interaction reflects up-to-date trade status.
- [ ] Add a `--interval` polling mode later (Phase 9+) once broker/live-price streaming exists.

**Acceptance:** `python main.py` runs a persistent session; asking twice in a row shows any previously opened trade's updated status if price has moved past SL/TP.

---

## PHASE 8 — Harden the AI Layer

- [ ] Apply Phase 1.4 (graceful Ollama failure).
- [ ] Re-evaluate `MODEL_NAME = "qwen2.5-coder:3b"` — this is a *code*-tuned model being used to write trading explanations. A general-instruct model (e.g. a Llama/Qwen instruct variant, not a coder variant) will follow the strict "explain-only" system prompt more reliably. Test both and compare hallucination rate against the report.
- [ ] Keep the "never invent numbers" system prompt as-is — it's correctly designed. Just verify empirically (Phase 10) that the smaller 3B model actually obeys it consistently; if not, size up.

**Acceptance:** 20 manual test prompts against real reports show zero instances of the LLM inventing a price, stop loss, or confidence value not present in the report.

---

## PHASE 9 — Testing & Validation Harness

- [ ] Fix `test_risk.py` (Phase 1.6).
- [ ] Add one new test per engine that feeds **synthetic/hardcoded candle arrays** (not live TradingView data) so tests are deterministic and don't depend on market hours or TradingView rate limits — currently every test hits the live feed.
- [ ] Add a regression test asserting `detect_bos_choc` returns distinct bullish/bearish flags (guards Phase 1.1 fix).
- [ ] Add a regression test that opens a trade, feeds a price past TP2, and asserts `summary()["wins"] == 1` (guards Phase 1.3 fix).

**Acceptance:** `pytest` (add `pytest` to `requirements.txt`) runs all tests offline in under 5 seconds, no network calls required.

---

## PHASE 10 — Later Roadmap (unchanged from your original priority list, sequenced after the above)

Only start these once Phases 1–9 are done and stable:

1. Volume Profile
2. VWAP
3. Session Analysis (Asian/London/NY kill zones)
4. News Filter / Economic Calendar integration
5. Broker API (replace anonymous TvDatafeed session with authenticated data + real execution)
6. Backtesting Engine (run Signal/Entry/Risk engines over historical data, using the deterministic test harness from Phase 9 as its foundation)
7. Portfolio Manager (multi-symbol, multi-position risk aggregation)
8. Copy Trading
9. Prop Firm Evaluation rule sets
10. Machine Learning Layer (only after a backtesting engine exists to validate it against — do not add ML before Phase 10.6)

---

## Design Rules to Keep Enforcing (from your original master prompt — do not relax these)

- Every engine keeps exactly one responsibility. New Structure sub-modules stay internal to `structure_engine.py`'s public API.
- Bias Engine never generates entries. Signal Engine never decides entry/SL/TP. Entry Engine never decides direction. Risk Engine never creates trades. Execution Engine never generates trading logic.
- The AI Layer explains the Report Engine's output only — it never computes or overrides Bias, Confidence, Entry, Stop Loss, Take Profit, or Recommendation.
- All trading logic stays in Python. The LLM's only job is natural-language explanation.

---

## Suggested Execution Order (checklist form)

- [ ] Phase 0 — Environment & Hygiene
- [ ] Phase 1 — Critical Bug Fixes
- [ ] Phase 2 — Structure Engine Rebuild
- [ ] Phase 3 — Liquidity Engine Upgrade
- [ ] Phase 4 — Order Block Upgrade
- [ ] Phase 5 — FVG Upgrade
- [ ] Phase 6 — Context Engine Upgrade
- [ ] Phase 7 — Execution Loop / main.py
- [ ] Phase 8 — AI Layer Hardening
- [ ] Phase 9 — Testing Harness
- [ ] Phase 10 — Later Roadmap (Volume Profile → ML Layer)
