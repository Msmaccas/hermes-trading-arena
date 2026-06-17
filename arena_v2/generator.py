#!/usr/bin/env python3
"""Generator: call DeepSeek API for persona-voiced analysis."""

import os, sys, json, time, logging, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

import requests

from .config import DEEPSEEK_MODEL, DEEPSEEK_TIMEOUT, DEEPSEEK_BASE_URL, CONCURRENCY_LIMIT
from .persona_engine import build_persona_prompt, extract_verbatim_quotes

logger = logging.getLogger("arena")

# ─── DeepSeek API call ───────────────────────────────────────────────────

def call_deepseek(
    system_prompt: str,
    user_message: str,
    api_key: str,
    timeout: int = DEEPSEEK_TIMEOUT,
    max_tokens: int = 16384,
    temperature: float = 0.7,
) -> Optional[str]:
    """Call the DeepSeek chat API (OpenAI-compatible endpoint)."""
    if not api_key:
        logger.error("DeepSeek API key not configured")
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    try:
        resp = requests.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        result = resp.json()
        content = result["choices"][0]["message"]["content"]
        return content
    except requests.Timeout:
        logger.error("DeepSeek API timeout after %ds", timeout)
        return None
    except Exception as e:
        logger.error("DeepSeek API error: %s", e)
        return None

# ─── Perform web research ────────────────────────────────────────────────

def perform_web_research(ticker: str) -> Optional[str]:
    """Search for recent news/catalysts for a given ticker."""
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        news = None
        try:
            news = stock.news
        except Exception:
            pass

        if news and len(news) > 0:
            items = news[:5]
            parts = [f"### Recent News for {ticker}"]
            for item in items:
                title = item.get("title", "?")
                link = item.get("link", "")
                publisher = item.get("publisher", "")
                parts.append(f"- **{title}** ({publisher})")
                if link:
                    parts.append(f"  {link}")
            return "\n".join(parts)

        # Fallback: scrape headlines
        search_url = f"https://finance.yahoo.com/quote/{ticker}/news"
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
        try:
            resp = requests.get(search_url, headers=headers, timeout=10)
            if resp.status_code == 200:
                from html.parser import HTMLParser
                class HP(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.headlines = []
                        self._cap = False
                    def handle_starttag(self, tag, attrs):
                        if tag == "h3":
                            self._cap = True
                    def handle_data(self, data):
                        if self._cap:
                            text = data.strip()
                            if text and len(text) > 10:
                                self.headlines.append(text)
                            self._cap = False
                parser = HP()
                parser.feed(resp.text)
                if parser.headlines:
                    return "### Recent Headlines\n" + "\n".join(f"- {h}" for h in parser.headlines[:5])
        except Exception:
            pass
        return None
    except Exception:
        return None

# ─── Generate full analysis ──────────────────────────────────────────────

def generate_analysis(
    persona: str, ticker: str, stock_data: dict, soul_content: str, api_key: str
) -> dict:
    """Call DeepSeek API to generate persona-voiced analysis.
    Returns {ticker, persona, success, content, word_count, error}
    """
    prompt = build_persona_prompt(persona, stock_data, soul_content)

    # Add web research to user message
    news = perform_web_research(ticker)
    news_section = f"\n### Recent News\n{news}" if news else ""

    user_msg = f"""Analyze {ticker} using your methodology and the indicator data provided above.{news_section}"""

    try:
        content = call_deepseek(prompt, user_msg, api_key, timeout=DEEPSEEK_TIMEOUT)
        if content:
            word_count = len(content.split())
            success = word_count >= 500  # reasonable minimum
            return {
                "ticker": ticker,
                "persona": persona,
                "success": success,
                "content": content,
                "word_count": word_count,
                "error": None if success else f"Too short: {word_count} words",
            }
        else:
            return {
                "ticker": ticker,
                "persona": persona,
                "success": False,
                "content": "",
                "word_count": 0,
                "error": "DeepSeek returned no content",
            }
    except Exception as e:
        return {
            "ticker": ticker,
            "persona": persona,
            "success": False,
            "content": "",
            "word_count": 0,
            "error": str(e),
        }

# ─── Generate summary (500-800 words) ────────────────────────────────────

def generate_summary(
    persona: str, ticker: str, stock_data: dict, soul_content: str, api_key: str
) -> str:
    """Generate shorter (500-800 word) summary for non-top-pick stocks."""
    prompt = build_persona_prompt(persona, stock_data, soul_content)

    user_msg = f"""Give me a concise 500-800 word summary analysis of {ticker}. 
Focus on: your key takeaway, the most important 3-4 indicators, and your stance.
Be specific with actual values from the data above. This is a shorter form, not a full deep-dive."""

    try:
        content = call_deepseek(prompt, user_msg, api_key, timeout=120)
        if content and len(content.split()) >= 200:
            return content
        return ""
    except Exception:
        return ""

# ─── Extract stance from content ─────────────────────────────────────────

def extract_stance(content: str) -> str:
    """Extract bullish/bearish/neutral stance from analysis content."""
    if not content:
        return "neutral"
    lower = content.lower()
    # Look for clear stance markers in first 500 chars
    first_500 = lower[:500]
    bullish_words = ["bullish", "buy", "long", "opportunity", "upside", "strong buy", "accumulate"]
    bearish_words = ["bearish", "sell", "short", "avoid", "downside", "weak", "caution"]

    bull_score = sum(1 for w in bullish_words if w in first_500)
    bear_score = sum(1 for w in bearish_words if w in first_500)

    if bull_score > bear_score + 1:
        return "bullish"
    elif bear_score > bull_score + 1:
        return "bearish"
    return "neutral"

# ─── Run batch generation ────────────────────────────────────────────────

def run_batch(
    persona_ticker_map: Dict[str, list],
    all_data: Dict,
    soul_mds: Dict[str, str],
    api_key: str,
) -> Dict[str, Dict[str, dict]]:
    """Run generation for ALL personas with parallel execution.
    Uses ThreadPoolExecutor with CONCURRENCY_LIMIT.
    Returns Dict[persona][ticker] = analysis_content_dict
    """
    results = {}
    total_analyses = sum(len(tickers) for tickers in persona_ticker_map.values())
    completed = 0

    def process_persona(persona: str) -> tuple:
        """Process all stocks for one persona sequentially."""
        persona_results = {}
        tickers = persona_ticker_map.get(persona, [])
        soul = soul_mds.get(persona, "")

        for ticker in tickers:
            stock_data = all_data.get(ticker, {})
            if not stock_data:
                continue
            result = generate_analysis(persona, ticker, stock_data, soul, api_key)
            persona_results[ticker] = result
            nonlocal completed
            completed += 1
            status = "✓" if result["success"] else "✗"
            wc = result.get("word_count", 0)
            print(f"[Generator] {status} [{completed}/{total_analyses}] {persona}/{ticker}: {wc} words", flush=True)
            time.sleep(0.5)  # rate limiting between stocks

        return persona, persona_results

    with ThreadPoolExecutor(max_workers=CONCURRENCY_LIMIT) as pool:
        fut_map = {pool.submit(process_persona, p): p for p in persona_ticker_map}

        for fut in as_completed(fut_map):
            try:
                persona, persona_results = fut.result()
                results[persona] = persona_results
            except Exception as e:
                persona = fut_map[fut]
                logger.error("Error processing persona %s: %s", persona, e)
                results[persona] = {}

    print(f"[Generator] Batch complete: {completed} analyses", flush=True)
    return results
