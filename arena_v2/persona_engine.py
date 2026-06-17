#!/usr/bin/env python3
"""Persona engine: load SOUL.md, score stocks, build prompts."""

import os, re, logging, datetime
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from .config import PERSONAS, PROFILES_DIR, MAX_STOCKS_PER_PERSONA, SUMMARY_STOCKS_PER_PERSONA

logger = logging.getLogger("arena")

# ─── Safe float helper ───────────────────────────────────────────────────

def _sf(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None

# ─── Load SOUL.md files ──────────────────────────────────────────────────

def load_soul_mds() -> Dict[str, str]:
    """Load all 10 SOUL.md files from ~/.hermes/profiles/{name}/SOUL.md."""
    soul_mds = {}
    for persona in PERSONAS:
        soul_path = os.path.join(PROFILES_DIR, persona, "SOUL.md")
        try:
            with open(soul_path, "r") as f:
                content = f.read()
            soul_mds[persona] = content
        except FileNotFoundError:
            logger.warning("SOUL.md not found for %s at %s", persona, soul_path)
            soul_mds[persona] = ""
        except Exception as e:
            logger.warning("Error reading SOUL.md for %s: %s", persona, e)
            soul_mds[persona] = ""
    return soul_mds

# ─── Extract verbatim quotes from SOUL.md ───────────────────────────────

def extract_verbatim_quotes(soul_content: str, persona: str) -> List[Tuple[str, str]]:
    """Extract verbatim quotes with source URLs from SOUL.md content.
    Returns list of (quote, source_url) tuples.
    """
    quotes = []
    # Pattern: "quote" — Source or "quote" (Source)
    patterns = [
        r'"([^"]{20,})"\s*[—–-]\s*(https?://[^\s)]+)',
        r'"([^"]{20,})"\s*\(([^)]+)\)',
        r'"([^"]{20,})"\s*[—–-]\s*\[([^\]]+)\]',
        r'\"([^"]{20,})\"',
    ]
    for pat in patterns:
        matches = re.findall(pat, soul_content, re.DOTALL)
        for m in matches:
            if isinstance(m, tuple):
                quote, source = m[0].strip(), m[1].strip()
            else:
                quote, source = m.strip(), ""
            if len(quote) > 20:
                quotes.append((quote, source))
    return quotes[:10]  # max 10 quotes

# ─── Score stock for persona fit ─────────────────────────────────────────

def score_stock_for_persona(stock_data: dict, persona: str) -> float:
    """Score a stock for persona fit (0.0 = no fit, 1.0 = perfect fit).
    Every stock gets a score — this determines ranking/emphasis, not exclusion.
    """
    price = _sf(stock_data.get("price"))
    pe = _sf(stock_data.get("pe"))
    eps = _sf(stock_data.get("eps"))
    eps_growth = _sf(stock_data.get("eps_growth"))
    mcap = _sf(stock_data.get("mcap"))
    beta = _sf(stock_data.get("beta"))
    dividend_yield = _sf(stock_data.get("dividend_yield"))
    volume = _sf(stock_data.get("volume"))

    ind = stock_data.get("indicators", {}) or {}
    rsi = _sf(ind.get("RSI"))
    ma50 = _sf(ind.get("SMA50"))
    ma200 = _sf(ind.get("SMA200"))
    ma10 = _sf(ind.get("SMA10"))
    ma30 = _sf(ind.get("SMA30"))
    vol_ratio = _sf(ind.get("Volume_ratio"))
    change_pct = _sf(ind.get("Change_pct"))
    atr = _sf(ind.get("ATR"))
    atr_pct = _sf(ind.get("ATR_pct"))
    bb_width = _sf(ind.get("BB_width_pct"))
    volatility = _sf(ind.get("Volatility_20d"))
    stoch_k = _sf(ind.get("Stoch_K"))
    vwma = _sf(ind.get("VWMA20"))
    bb_pct_b = _sf(ind.get("BB_pct_b"))
    sma10 = _sf(ind.get("SMA10"))
    sma30 = _sf(ind.get("SMA30"))
    sma50 = _sf(ind.get("SMA50"))
    sma200 = _sf(ind.get("SMA200"))
    ema50 = _sf(ind.get("EMA50"))
    ema200 = _sf(ind.get("EMA200"))

    score = 0.0

    if persona == "oneil":
        # CAN SLIM: PE 5-60, EPS growth positive, RSI>30, price>MA50, volume>1.5x
        if pe is not None and 5 <= pe <= 60:
            score += 0.3
        if eps_growth is not None and eps_growth > 0:
            score += 0.2
        if rsi is not None and rsi > 30:
            score += 0.1
        if price is not None and ma50 is not None and price > ma50:
            score += 0.2
        if vol_ratio is not None and vol_ratio > 1.5:
            score += 0.1
        score += 0.1  # sector momentum proxy (always available)
        return min(score, 1.0)

    if persona == "buffet":
        # Value: PE 5-40, EPS positive, mcap>10B, price>MA200, beta<1.5, dividend>0
        if pe is not None and 5 <= pe <= 40:
            score += 0.3
        if eps is not None and eps > 0:
            score += 0.2
        if mcap is not None and mcap > 10e9:
            score += 0.2
        if price is not None and ma200 is not None and price > ma200:
            score += 0.1
        if beta is not None and beta < 1.5:
            score += 0.1
        if dividend_yield is not None and dividend_yield > 0:
            score += 0.1
        return min(score, 1.0)

    if persona == "lynch":
        # GARP: PEG<2, PE>0, EPS growth>0, mcap>1B, volume>100k, RSI>30
        if pe is not None and pe > 0 and eps_growth is not None and eps_growth > 0:
            peg = pe / (eps_growth * 100)
            if peg < 2.0:
                score += 0.4
        if pe is not None and pe > 0:
            score += 0.1
        if eps_growth is not None and eps_growth > 0:
            score += 0.2
        if mcap is not None and mcap > 1e9:
            score += 0.1
        if volume is not None and volume > 100000:
            score += 0.1
        if rsi is not None and rsi > 30:
            score += 0.1
        return min(score, 1.0)

    if persona == "minervini":
        # SEPA/VCP: price>MA50>MA200, RSI 40-80, volume>avg, EPS>0, price>10
        if price is not None and ma50 is not None and ma200 is not None:
            if price > ma50 > ma200:
                score += 0.4
        if rsi is not None and 40 <= rsi <= 80:
            score += 0.2
        if vol_ratio is not None and vol_ratio > 1.0:
            score += 0.1
        if eps_growth is not None and eps_growth > 0:
            score += 0.2
        if price is not None and price > 10:
            score += 0.1
        return min(score, 1.0)

    if persona == "qullamaggie":
        # Episodic Pivot: volume>1.5x, change>3%, price>MA50, RSI>50, ATR>1%, BB_width>median
        if vol_ratio is not None and vol_ratio > 1.5:
            score += 0.2
        if change_pct is not None and change_pct > 3:
            score += 0.2
        if price is not None and ma50 is not None and price > ma50:
            score += 0.2
        if rsi is not None and rsi > 50:
            score += 0.1
        if atr_pct is not None and atr_pct > 1:
            score += 0.2
        if bb_width is not None and bb_width > 8:  # proxy for > median
            score += 0.1
        return min(score, 1.0)

    if persona == "david-ryan":
        # 3x Champion: EPS growth>20%, volume>1.5x, price>MA50, RSI>50, close near high
        if eps_growth is not None and eps_growth > 0.20:
            score += 0.3
        if vol_ratio is not None and vol_ratio > 1.5:
            score += 0.2
        if price is not None and ma50 is not None and price > ma50:
            score += 0.2
        if rsi is not None and rsi > 50:
            score += 0.1
        if bb_pct_b is not None and bb_pct_b > 0.7:
            score += 0.2  # close near upper band = near high
        return min(score, 1.0)

    if persona == "matt-caruso":
        # Position Sizing: price>$5, volume>50k, ATR>0.5%, RSI>45, volatility>median, change>0
        if price is not None and price > 5:
            score += 0.2
        if volume is not None and volume > 50000:
            score += 0.2
        if atr_pct is not None and atr_pct > 0.5:
            score += 0.2
        if rsi is not None and rsi > 45:
            score += 0.1
        if volatility is not None and volatility > 3:  # proxy for > median
            score += 0.2
        if change_pct is not None and change_pct > 0:
            score += 0.1
        return min(score, 1.0)

    if persona == "brian-shannon":
        # Anchored VWAP: price>MA50, MA50 slope>0, RSI>50, VWMA uptrend, volume>avg, close in upper BB
        if price is not None and ma50 is not None and price > ma50:
            score += 0.2
        if ema50 is not None and ema200 is not None and ema50 > ema200:
            score += 0.2  # MA50 slope positive proxy
        if rsi is not None and rsi > 50:
            score += 0.2
        if vwma is not None and ma50 is not None and vwma > ma50:
            score += 0.2  # VWMA uptrend proxy
        if vol_ratio is not None and vol_ratio > 1.0:
            score += 0.1
        if bb_pct_b is not None and bb_pct_b > 0.5:
            score += 0.1
        return min(score, 1.0)

    if persona == "dan-zanger":
        # Chart Pattern Momentum: price>MA50, volume>1.5x, change>2%, RSI 50-70, BB expanding, price>MA200
        if price is not None and ma50 is not None and price > ma50:
            score += 0.2
        if vol_ratio is not None and vol_ratio > 1.5:
            score += 0.15
        if change_pct is not None and change_pct > 2:
            score += 0.15
        if rsi is not None and 50 <= rsi <= 70:
            score += 0.15
        if bb_width is not None and bb_width > 5:
            score += 0.15  # BB expanding proxy
        if price is not None and ma200 is not None and price > ma200:
            score += 0.1
        sector = stock_data.get("sector", "").lower()
        if "technology" in sector or "semiconductor" in sector or "tech" in sector:
            score += 0.1
        return min(score, 1.0)

    if persona == "nick-schmidt":
        # Weekly Chart Trader: SMA10 > SMA30, price > SMA10, volume>avg, RSI>50, no distribution, Stoch>20
        if sma10 is not None and sma30 is not None and sma10 > sma30:
            score += 0.3
        if price is not None and sma10 is not None and price > sma10:
            score += 0.2
        if vol_ratio is not None and vol_ratio > 1.0:
            score += 0.15
        if rsi is not None and rsi > 50:
            score += 0.15
        score += 0.1  # no distribution (proxy)
        if stoch_k is not None and stoch_k > 20:
            score += 0.1
        return min(score, 1.0)

    return score

# ─── Build persona prompt ────────────────────────────────────────────────

def build_persona_prompt(persona: str, stock_data: dict, soul_content: str) -> str:
    """Build the system prompt for DeepSeek persona-voiced analysis.
    Embeds REAL indicator values, not generic descriptions.
    """
    ticker = stock_data.get("ticker", "UNKNOWN")
    price = _sf(stock_data.get("price"))
    pe = _sf(stock_data.get("pe"))
    eps = _sf(stock_data.get("eps"))
    eps_growth = _sf(stock_data.get("eps_growth"))
    mcap = _sf(stock_data.get("mcap"))
    sector = stock_data.get("sector", "Unknown")
    beta = _sf(stock_data.get("beta"))
    dividend_yield = _sf(stock_data.get("dividend_yield"))
    change_pct = _sf(stock_data.get("change_pct"))

    ind = stock_data.get("indicators", {}) or {}

    # Extract verbatim quotes
    quotes = extract_verbatim_quotes(soul_content, persona)
    quotes_str = "\n".join(
        f'- "{q}" — {s}' if s else f'- "{q}"'
        for q, s in quotes
    ) if quotes else "  (No verbatim quotes extracted)"

    # Format indicators naturally
    ind_lines = []
    for k, v in sorted(ind.items()):
        if isinstance(v, float):
            ind_lines.append(f"  {k}: {v:.2f}")
        elif isinstance(v, bool):
            ind_lines.append(f"  {k}: {v}")
        else:
            ind_lines.append(f"  {k}: {v}")

    indicators_str = "\n".join(ind_lines)

    # Market cap display
    mcap_str = f"${mcap:,.0f}" if mcap else "N/A"
    mcap_b = f" (${mcap/1e9:.2f}B)" if mcap else ""

    # Count how many of the 25+ indicators we captured
    ind_count = len(ind)

    prompt = f"""You are {persona.upper()}. This is your complete identity — embody it fully.

## YOUR IDENTITY & VOICE

{soul_content[:8000]}

## VERBATIM QUOTES FROM YOUR WRITINGS
{quotes_str}

## STOCK TO ANALYZE: {ticker}

**Date:** {datetime.date.today().isoformat()}
**Sector:** {sector}
**Price:** ${price if price else "N/A"}
**Change %:** {change_pct}%
**Market Cap:** {mcap_str}{mcap_b}
**P/E:** {pe if pe else "N/A"}
**EPS:** {eps if eps else "N/A"}
**EPS Growth (Quarterly):** {f"{eps_growth*100:.1f}%" if eps_growth else "N/A"}
**Beta:** {beta if beta else "N/A"}
**Dividend Yield:** {f"{dividend_yield*100:.2f}%" if dividend_yield else "N/A"}

## REAL TECHNICAL INDICATORS ({ind_count} computed)
{indicators_str}

## YOUR ANALYSIS INSTRUCTIONS

Write a comprehensive 3000+ word analysis in the EXACT voice of {persona.upper()}. Follow this structure:

1. **Overview & Setup** — Your take on {ticker} in 2-3 paragraphs. What caught your eye? What worries you?
2. **Fundamental Analysis** — Analyze P/E, EPS growth, sector context. Use your specific methodology lens.
3. **Technical Analysis** — Walk through ALL indicators above with actual values. Each indicator gets 100+ words.
4. **Risk Assessment** — What could go wrong? Be specific.
5. **Your Verdict** — Buy, watch, or avoid? With price targets and conditions.

CRITICAL RULES:
- Use verbatim quotes from your writings above with source URLs
- EMBED ACTUAL INDICATOR VALUES in your analysis, not generic descriptions
- Write in first person as {persona.upper()}
- Must be 3000+ words
- Include a clear stance (bullish/bearish/neutral) at the top
"""
    return prompt

# ─── Assign stocks to personas ───────────────────────────────────────────

def assign_stocks_to_personas(all_data: Dict[str, dict], soul_mds: Dict[str, str]) -> Dict[str, list]:
    """Score all stocks against all personas, assign top picks.
    Returns {persona: [ticker1, ticker2, ...]}
    """
    persona_assignments = {p: [] for p in PERSONAS}

    for persona in PERSONAS:
        # Score all stocks for this persona
        scored = []
        for ticker, data in all_data.items():
            if not data or "error" in data:
                continue
            score = score_stock_for_persona(data, persona)
            scored.append((score, ticker, data))

        # Sort by score descending
        scored.sort(key=lambda x: -x[0])

        # Take top MAX_STOCKS_PER_PERSONA for full analysis
        top_picks = scored[:MAX_STOCKS_PER_PERSONA]
        persona_assignments[persona] = [t for _, t, _ in top_picks]

        # Log summary
        viable = sum(1 for s, _, _ in scored if s > 0)
        total = len(scored)
        avg_score = sum(s for s, _, _ in scored) / total if total > 0 else 0
        top_scores = ", ".join(f"{t}({s:.2f})" for s, t, _ in scored[:5])
        print(f"[Engine] {persona}: {viable}/{total} viable stocks, avg score {avg_score:.2f}, top: {top_scores}", flush=True)

    return persona_assignments
