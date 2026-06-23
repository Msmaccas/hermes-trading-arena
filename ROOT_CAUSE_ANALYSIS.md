# ROOT CAUSE ANALYSIS & PERMANENT FIX PLAN

*Generated: June 21, 2026 — Post-mortem of all bear case analyses and arena runs*

---

## EXECUTIVE SUMMARY

Six systemic issues exist across our entire analysis pipeline. Each one contributed to bear case analyses that were **superficially plausible but not deeply verified** — readable but not defensible against adversarial scrutiny.

---

## SYSTEMIC MISTAKE #1: Single Data Source Monoculture

**What happened:** Every analysis used yfinance exclusively for market data. Not one SEC filing, not one earnings call transcript, not one industry database was consulted.

**Evidence from codebase:** `screener/yahoo_fetcher.py` (327 lines) is the ONLY data fetcher. No EDGAR, no KRX, no HKEX, no TipRanks, no insider trading API, no macro feeds. Zero lines reference SEC, filing, transcript, or any primary source.

**What the reference sources teach:** 
- **Source-Driven Development (addyosmani):** "Priority 1: Official documentation" — for stocks, that's SEC filings, not Yahoo Finance summaries
- **Agent-Reach:** Multi-backend routing — primary + fallback + fallback
- **WorldMonitor:** Cross-stream correlation from 65+ independent data sources
- The Substack prompt says: "I want to burn hundreds of tokens using Perplexity" — the author values DEEP research over speed

**Fix:**
1. Add SEC EDGAR 10-K/10-Q fetcher (fundamentals from official filings)
2. Add earnings call transcript fetcher (for management tone analysis)
3. Add insider trading data source
4. Add analyst estimate revision tracker
5. Add industry-specific databases per sector (Clarksons for shipping, USGS for mining, Gartner for semi)

---

## SYSTEMIC MISTAKE #2: No Source Diversity Enforcement

**What happened:** The bear cases collectively cited <10 URLs across 6 analyses (7,467 words). The user explicitly asked for 50+ unique sources, thinking diverse URLs from Chinese + English sources.

**What the reference sources teach:**
- **Agent-Reach:** Reads from Twitter, Reddit, YouTube, GitHub, Bilibili, Xiaohongshu — 10+ platforms
- **WorldMonitor:** 500+ curated feeds across 15 categories, 65+ external providers
- **Substack: The first step** the author takes is listing 150 companies, then doing deep research on each

**The specific gap:** My analyses had no:
- Chinese sources (雪球, 东方财富, CSDN) for Asian stocks
- Official company IR pages
- SEC filing direct links
- Industry association data
- Competitor analysis references

**Fix:**
1. Add `--min-sources N` flag (default 15 per analysis, 3+ per major claim)
2. Enforce source type diversity (must include: primary filings + analyst + news + industry)
3. Add Chinese source fetcher (Jina Reader for 雪球, 东方财富)
4. Pre-flight source budget check before generating text

---

## SYSTEMIC MISTAKE #3: No Adversarial Validation

**What happened:** Every bear case claim was asserted without cross-examination. No subagent was tasked with trying to disprove each claim. When I found errors in the previous audit (PLTR risk off by 5x, NVDA risk mismatch), it was because I manually checked — not because the system caught it.

**What the reference sources teach:**
- **Doubt-Driven Development (addyosmani):** "Every non-trivial decision gets cross-examined while course-correction is still cheap." The process: CLAIM → EXTRACT → DOUBT → RECONCILE → STOP
- **The key insight:** A fresh-context reviewer "biased to disprove, not approve" catches errors that the original author cannot see because of context contamination

**Fix:**
1. After writing any analysis, spawn an **adversarial subagent** whose sole job is to disprove every factual claim
2. The adversarial subagent gets: the claims, the sources cited, and the instruction "find at least 3 errors"
3. The analysis cannot be published until adversarial review passes
4. Track adversarial review findings over time to identify systematic weaknesses

---

## SYSTEMIC MISTAKE #4: No Multi-Backend Data Resilience

**What happened:** When TV MCP returned stale cached data (known NVDA cache bug), I kept using it. When the browser/Camofox wasn't running, I couldn't read the Substack articles. No fallback chains exist.

**What the reference sources teach:**
- **Agent-Reach:** Multi-backend routing is baked into the architecture. "Platform X blocks method A? We switch to method B transparently."
- yt-dlp blocked by B站 → bili-cli → user feels nothing
- Twitter API requires payment → OpenClaw + browser cookie auth → user feels nothing
- **WorldMonitor:** 35 source groups tracked by freshness monitor — if one source goes stale, others are weighted higher

**Fix:**
1. Add data source fallback chain: yfinance → yahoo v8 API → yahoo v7 API → TV MCP
2. For TV data: TV MCP → TradingView REST → yfinance OHLCV
3. For browser-dependent sources: curl → Jina Reader → browser fallback
4. Add freshness checks: if data is >1 hour stale, re-fetch or note as stale
5. Log source health to a status dashboard

---

## SYSTEMIC MISTAKE #5: No Source Citation at Point of Claim

**What happened:** Bear cases cite broad concepts ("the LNG carrier market collapsed 82.9%") without a clickable, verifiable, timestamped URL for that specific data point. This makes every claim unverifiable.

**What the reference sources teach:**
- **Source-Driven Development:** "Cite — show your sources" at EVERY decision point
- The skill enforces: DETECT → FETCH → IMPLEMENT → CITE
- Citation is not optional — it's a step in the pipeline

**Fix:**
1. Every factual claim in an analysis must have an inline `[source: URL]` marker
2. URL must point to a specific, verifiable page (not a Google search, not a homepage)
3. Source type must be annotated: `[SEC filing]`, `[earnings call]`, `[industry report]`
4. Post-generation validation: verify every URL is still live and points to the claimed content

---

## SYSTEMIC MISTAKE #6: No Execution Feedback Loop

**What happened:** Bear cases make specific predictions ("50% downside", "earnings will decelerate") but there's zero tracking of whether these claims proved accurate. We have no way to learn from mistakes.

**What the reference sources teach:**
- **WorldMonitor:** Tracks everything with a freshness monitor and status dashboard
- **Doubt-Driven Development:** STOP condition includes "met stop condition (trivial findings, 3 cycles, or user override)"

**Fix:**
1. Extract every prediction from bear cases (target prices, revenue decline claims, margin compression claims)
2. Create a tracking table: Prediction → Date → Actual Outcome → Error Magnitude
3. Score each persona's bear case track record (reverse of the bull case accuracy tracker)
4. Monthly review of which bear arguments were correct vs which were wrong

---

## IMMEDIATE FIXES (DELEGATED)

The following fix tasks are being dispatched to subagents in parallel:

| Task | Priority | Description |
|------|----------|-------------|
| **SEC EDGAR Fetcher** | CRITICAL | Add module to pull 10-K/10-Q fundamental data directly from SEC |
| **Adversarial Review Pipeline** | CRITICAL | Add post-generation review subagent that tries to disprove each claim |
| **Source Diversity Enforcer** | HIGH | Add `--min-sources` flag and pre-flight source budget check |
| **Multi-Backend Data Router** | HIGH | Add fallback chains for all data sources |
| **Citation Verification Hook** | HIGH | Inline citation markers at point of claim + URL validation |
| **Prediction Tracker** | MEDIUM | Extract all predictions from bear/bull cases → tracking table |

---

## PRINCIPLES TO ENCODE AS SKILLS

1. **Stock Analysis Source Hierarchy Skill** — Priority list of data sources with verification requirements per source type
2. **Adversarial Review Skill** — Template for spawning fresh-context review subagents biased to disprove
3. **Source Diversity Checklist** — Must-have source categories per analysis with minimum counts
4. **Pre-Flight Data Depth Check** — Before generating text, verify sufficient data has been gathered from diverse sources

---

*Next steps: All 4 new skills + 6 code modules to be created.*
