# Arena V2 — Complete Rewrite Plan

## Why Rewrite?

The current `arena_runner.py` (2208 lines) accumulated 6+ rounds of patch fixes. It suffers from:

| # | Problem | Root Cause | Fix |
|---|---------|------------|-----|
| 1 | **Phase 2b kills stocks** | PE filters too strict (Buffett 5-25, O'Neil 5-50). Modern tech/growth stocks routinely trade at PE 30-100+. | Convert to **SCORING** (0.0-1.0) not pass/fail. Every stock gets analyzed, scoring determines ranking/emphasis. |
| 2 | **Template voice** | System prompt lacks verbatim quotes from SOUL.md. Formulaic "RSI measures momentum on a 0-100 scale" instead of O'Neil: "The RS line making a new high before price is the strongest bullish signal I know." | Build system prompts with **verbatim SOUL.md quotes** + **real indicator data** embedded. |
| 3 | **DeepSeek timeout kills analysis** | 180-300s API timeout cuts off 3000-word generation mid-stream. Falls back to "Analysis generation failed" garbage. | **600s timeout** + **Parallel subagents** via delegate_task (max 4 concurrent). Each subagent handles one persona × N stocks. |
| 4 | **No MACD/Stoch/ADX/etc** | Only RSI and MAs computed locally from yfinance. | Add **pandas-based indicator computation**: MACD, Stoch, ADX, BB width, VWMA, ATR, CCI20. |
| 5 | **TV CDP hack never worked** | `chart._panes.values is not a function`. Tried 6+ fixes, all failed. | **REMOVE** CDP code entirely. Replace with yfinance OHLCV + local computation for 40+ indicators. |
| 6 | **No real indicator analysis** | Fablicated "Bollinger Band width suggests expanding volatility" instead of reading real BB values. | **Real data in system prompt** — indicators are computed from live yfinance data, not described generically. |
| 7 | **Sequential bottleneck** | Phase 3 runs 3 concurrent subprocesses that all hang on API timeouts. | **Parallel persona agents** via ThreadPoolExecutor with `concurrency_limit=4`. Each has INDEPENDENT 600s timeout. |
| 8 | **Only 10 stocks analyzed** | Phase 2b filters out 90%+ of stocks before analysis begins. | **ALL stocks scored by ALL personas**. Top 5 per persona by score get full 3000-word analysis. Rest get 500-word summary. |

## New Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        THE ORCHESTRATOR                                 │
│              (Hermes — delegates, does NOT do direct work)              │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                      ┌─────────────┴─────────────┐
                      ▼                           ▼
     ┌─────────────────────────────┐   ┌─────────────────────────────┐
     │  Phase 1: Data Collection   │   │  Phase 2: Persona Assign    │
     │  (single Python process)    │   │  (single Python process)    │
     │                             │   │                             │
     │  • TV Scanner API (global)  │   │  • Load 10 SOUL.md files    │
     │  • yfinance fundamentals    │   │  • Score stocks per persona │
     │  • Compute 40+ indicators   │   │  • Top 5 per persona        │
     └───────────┬─────────────────┘   └───────────┬─────────────────┘
                 └────────────────┬────────────────┘
                                  ▼
                 ┌─────────────────────────────────────┐
                 │ Phase 3: Parallel Persona Analysis  │
                 │ (4 concurrent subagents via Hermes  │
                 │  delegate_task, 600s timeout each)  │
                 │                                     │
                 │  ┌────────┐ ┌────────┐ ┌────────┐  │
                 │  │O'Neil  │ │Buffett │ │ Lynch  │  │
                 │  │top 5   │ │top 5   │ │top 5   │  │
                 │  │3000w   │ │3000w   │ │3000w   │  │
                 │  └────────┘ └────────┘ └────────┘  │
                 │   concurrency_limit=4               │
                 └──────────────────┬──────────────────┘
                                    ▼
                 ┌─────────────────────────────────────┐
                 │     Phase 4: Output Assembly        │
                 │  (single Python process)            │
                 │                                     │
                 │  • Write per-persona per-stock .md  │
                 │  • Push to GitHub repo              │
                 │  • Write master index               │
                 └─────────────────────────────────────┘
```

## Detailed Module Spec

### Module 1: Data Collection (`data_layer.py`) — ~400 lines

**Input:** `TARGET_STOCK_LIST` (list of ticker strings) OR `TV_SCAN_ENABLED=True` (scan all 11 global markets)

**Steps:**
1. If TV_SCAN: call `global_tv_scan()` (keep existing working code)
2. For each ticker in final list:
   a. Call `yfinance.download(period="1y")` for OHLCV
   b. Call `yfinance.Ticker.info` for fundamentals (PE, EPS, mcap, beta, sector, dividend)
   c. Compute ALL indicators from OHLCV using numpy/pandas:
      - **RSI(14)** — keep existing `compute_rsi()`
      - **MACD(12,26,9)** — line + signal + histogram
      - **BB(20,2)** — upper, lower, width, %b
      - **SMA(5,10,20,30,50,100,200)** — full range
      - **EMA(5,10,20,30,50,100,200)** — full range
      - **Stoch.K(14,3)** + **Stoch.D(3)**
      - **ADX(14)** — +DI, -DI, ADX
      - **VWMA** — volume-weighted moving average
      - **ATR(14)** — average true range
      - **CCI20** — commodity channel index
      - **Volatility** — 20-day std dev / close
      - **Volume ratio** — current / 50-day avg
   d. Skip stocks with price < $2 AND mcap < $50M (penny stock filter)
3. Store all data in `Dict[ticker -> Dict]` passed to Phase 2

**Key functions:**
```python
def compute_indicators(close: pd.Series, high: pd.Series, low: pd.Series, volume: pd.Series) -> dict:
    """Compute 25+ indicators from OHLCV data. Pure numpy/pandas, zero external deps."""
    returns = {
        "RSI": ..., "MACD": ..., "MACD_signal": ..., "MACD_hist": ...,
        "BB_upper": ..., "BB_lower": ..., "BB_width": ..., "BB_pct_b": ...,
        "SMA5": ..., "SMA10": ..., "SMA20": ..., "SMA50": ..., "SMA200": ...,
        "EMA5": ..., "EMA10": ..., "EMA20": ..., "EMA50": ..., "EMA200": ...,
        "Stoch_K": ..., "Stoch_D": ...,
        "ADX": ..., "ADX_PDI": ..., "ADX_NDI": ...,
        "VWMA": ..., "ATR": ..., "CCI20": ..., "Volatility": ...,
        "Volume_ratio": ...,
    }
    return returns
```

### Module 2: Persona System (`persona_layer.py`) — ~300 lines

**Input:** Ticker data from Module 1, SOUL.md files from `~/.hermes/profiles/`

**Steps:**
1. Load all 10 SOUL.md files into memory
2. For each persona, extract:
   - **Name** and **methodology type**
   - **Verbatim quotes** (pre-extracted from SOUL.md)
   - **Scoring criteria** (PE range, RSI range, volume requirements, EPS requirements)
3. Score each stock × persona pair (0.0-1.0):
   - O'Neil: PE 5-60 (was 5-50), EPS growth>0, RSI>30, price>MA50, volume ratio>1.0
   - Buffett: PE 5-40 (was 5-25), EPS>0, mcap>10B, ROE>15% if available
   - Lynch: PEG<2.0, PE>0, EPS growth>0
   - Minervini: trend template (price>MA50>MA200), RSI 30-80, volume ratio>1.0
   - Qullamaggie: volume spike>1.5x OR change>3% OR RS line new high
   - David Ryan: EPS acceleration>20%, volume>1.5x avg
   - Matt Caruso: price>$5, volume>$1M, ATR>1%
   - Brian Shannon: price>AVWAP, AVWAP slope>0, RSI>50
   - Dan Zanger: price>MA50, volume>avg, sector momentum
   - Nick Schmidt: uptrend (10>SMA > 30>SMA), institutional accumulation
4. Sort stocks by score for each persona, take top N (N=5 for full analysis, rest get summary)

### Module 3: Generation Engine (`persona_agent.py`) — ~400 lines

**Input:** Per-persona per-stock data + SOUL.md quotes + indicator data
**Execution:** 4 concurrent subagents via Hermes `delegate_task`
**Output:** Markdown analysis files

**System Prompt Construction:**
```
You are {PERSONA NAME}. {PERSONA BIO WITH VERBATIM QUOTES}.

METHODOLOGY:
{Persona's specific methodology steps from SOUL.md}

HERE IS THE REAL DATA FOR THIS STOCK:
- Price: ${price}
- RSI(14): {rsi} — interpret this through your methodology
- MACD: {macd_line} / {macd_signal} / {macd_hist} — {macd_interpretation}
- BB Upper: {bb_upper} / Lower: {bb_lower} / Width: {bb_width}%
- SMA50: {sma50} / SMA200: {sma200} — {golden_death_cross_status}
- Volume ratio vs 50d avg: {vol_ratio}x
- P/E: {pe} / EPS Growth: {eps_growth}%
- Market Cap: ${mcap}B

REQUIRED OUTPUT FORMAT:
1. Opening stance: BUY/SELL/HOLD with conviction level
2. Methodology walkthrough: Apply each letter/step to this stock with specific numbers
3. Indicator analysis (100+ words per indicator): {indicator_list}
4. Key risks and catalysts
5. Position sizing recommendation based on methodology
6. Verbatim quotes from SOUL.md with source URLs
7. Minimum 3000 words total
```

**Timeout handling:**
- Each subagent gets 600s (10 minutes) — enough for 3000+ word DeepSeek generation
- If subagent doesn't finish, it returns partial output with `[INCOMPLETE]` prefix
- The orchestrator collects ALL outputs (complete + partial) and writes them all

### Module 4: Output (`output_layer.py`) — ~200 lines

**Input:** Dict of {persona: {ticker: analysis_text}}
**Output:** Files in `~/hermes-trading-arena/output/{date}/{persona}/{ticker}.md`

**Format:**
```markdown
---
persona: oneil
ticker: MU
date: 2026-06-15
stance: BUY
conviction: 7/10
indicators: RSI, MACD, BB, SMA50, SMA200, Volume
source: https://finance.yahoo.com/quote/MU
---

# MU — O'Neil CANSLIM Analysis — 2026-06-15

## Verdict: BUY on pullback to $1050-1070

### C — Current Earnings
...
```

## Files to Create

| File | Lines | Purpose |
|------|-------|---------|
| `arena_v2_data.py` | ~400 | Data collection + indicator computation |
| `arena_v2_persona.py` | ~300 | Persona scoring + system prompt builder |
| `arena_v2_runner.py` | ~400 | Main orchestrator: Phases 1-4, parallel execution |
| `arena_v2_output.py` | ~200 | Markdown output + GitHub push |
| `ARENA_V2_CONFIG.py` | ~100 | Config constants, exchange suffixes, persona configs |

Total: ~1400 lines (down from 2208)

## Testing Plan

1. **Unit test**: `compute_indicators()` on 5 known stocks, verify RSI/MACD/BB values against TradingView
2. **Integration test**: Run `arena_v2_runner.py` MODE=TARGET_STOCKS on 5 stocks (MU, RDDT, CRDO, 080220, 356860)
3. **Quality check**: Read output files, verify: real data used, persona voice present, 3000+ words, verbatim quotes with URLs
4. **Full run**: All 10 personas × 5 stocks = 50 analysis files

## Data Sources (50+ URLs collected)

### Multi-Agent Trading Frameworks
- https://github.com/TauricResearch/TradingAgents (86K★, local fork at ~/council-of-goats/tradingagents/)
- https://github.com/FinStep-AI/ContestTrade (664★, debate mechanism)
- https://github.com/simonlin1212/TradingAgents-astock (1.2K★, Chinese A-share)
- https://github.com/dragon1086/prism-insight (641★, 13+ agents + MCP)
- https://arxiv.org/abs/2412.20138 (TradingAgents paper)
- https://arxiv.org/abs/2508.00554 (ContestTrade paper)

### TV Indicator Extraction
- https://github.com/FerroxLabs/tvcontrol — Correct CDP dataWindowView() pattern
- https://pypi.org/project/tradingview-ta/ — 40+ indicators (rate-limited but works)
- https://github.com/digitalleonard/tradingview-mcp — Claude skill for TV MCP

### Persona Voice Generation
- https://github.com/LC1332/ChatHaruhi (1.2K★) — Character role-play LLM
- https://github.com/InteractiveNLP-Team/RoleLLM (1.8K★) — Role-based LLM

### Other MCP/Scrapers
- https://github.com/bidouilles/mcp-tradingview-server — TV MCP via scraper
- https://github.com/smitkunpara/tv-mcp — FastMCP TV scraper
- https://github.com/DeltaPy/TradingView-Scraper
- https://github.com/atilaahmettaner/tradingview-mcp — Screening + indicators
