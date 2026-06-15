"""
engine/persona_runner.py — Single persona analysis.
This is the ONLY file that calls DeepSeek.
Takes: persona_name, stock_data_dict, output_dir
Reads: profiles/{persona}/SOUL.md
Writes: output/{persona}/{ticker} - YYYY-MM-DD.md
"""

import json
import os
import sys
import yfinance as yf
import functools
from concurrent.futures import ThreadPoolExecutor, as_completed

from engine.config import PERSONAS, PROFILES_DIR, DATE_STR
from engine.utils import call_deepseek, write_analysis_file
from engine.review_gate import check_analysis


# ─── SOUL LOADING ────────────────────────────────────────────────────────────

def load_soul(persona_name):
    """Read SOUL.md from profiles/{persona}/SOUL.md."""
    path = os.path.join(PROFILES_DIR, persona_name, "SOUL.md")
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        print(f"[Runner]  ⚠ SOUL.md not found for {persona_name}", flush=True)
        return None
    except Exception as e:
        print(f"[Runner]  ⚠ Error reading SOUL.md for {persona_name}: {e}", flush=True)
        return None


# ─── WEB RESEARCH ────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=100)
def fetch_web_research(ticker):
    """Fetch recent news for a ticker via yfinance."""
    try:
        stock = yf.Ticker(ticker)
        try:
            news = stock.news
        except Exception:
            news = None
        if news and len(news) > 0:
            parts = [f"### Recent News for {ticker}"]
            for item in news[:5]:
                parts.append(f"- **{item.get('title', '?')}** ({item.get('publisher', '')})")
                if item.get("link"):
                    parts.append(f"  {item['link']}")
            return "\n".join(parts)
    except Exception:
        pass
    return None


# ─── PROMPT BUILDING ─────────────────────────────────────────────────────────

def build_prompt(persona, soul, stock_data):
    """Build system + user prompt for DeepSeek analysis of one stock."""
    ticker = stock_data.get("ticker", "?")
    price = stock_data.get("price", "?")
    sector = stock_data.get("sector", "Unknown")
    change_pct = stock_data.get("change_pct", 0)
    mcap = stock_data.get("mcap")

    system = (
        f"You are {persona.upper()}. This is your identity and voice — embody it completely.\n\n"
        f"{soul[:10000]}\n\n"
        "CRITICAL RULES:\n"
        "1. Write in the EXACT VOICE of the persona — jargon, sentence structure, references\n"
        "2. Include VERBATIM quotes from SOUL.md with source URLs\n"
        "3. Analyze ALL available indicators (21+ technical, fundamental, macro)\n"
        "4. 100+ words per indicator analysis\n"
        "5. Must be 3000+ words total\n"
        "6. Write as if you are the persona personally analyzing this stock"
    )

    user = (
        f"## STOCK ANALYSIS REQUEST: {ticker}\n\n"
        f"**Date:** {DATE_STR}\n"
        f"**Sector:** {sector}\n"
        f"**Price:** ${price}\n"
        f"**Change %:** {change_pct}%\n"
        f"**Market Cap:** {('$' + '{:,}'.format(mcap)) if mcap else 'N/A'}\n\n"
        f"### Fundamentals\n"
        f"- P/E: {stock_data.get('pe', 'N/A')}\n"
        f"- EPS: {stock_data.get('eps', 'N/A')}\n"
        f"- EPS Growth: {stock_data.get('eps_growth', 'N/A')}\n"
        f"- Beta: {stock_data.get('beta', 'N/A')}\n\n"
        f"### Technical\n"
        f"- RSI(14): {stock_data.get('rsi', 'N/A')}\n"
        f"- MA50: ${stock_data.get('ma50', 'N/A')}\n"
        f"- MA200: ${stock_data.get('ma200', 'N/A')}\n"
        f"- Volume Ratio: {stock_data.get('vol_ratio', 'N/A')}x\n\n"
        "BEGIN YOUR FULL ANALYSIS BELOW. Must be 3000+ words. "
        "Write in first person."
    )
    return system, user


# ─── ANALYZE ONE STOCK ───────────────────────────────────────────────────────

def analyze_stock(persona, soul, stock_data, output_dir):
    """Analyze a single stock: build prompt, call DeepSeek, write file, review."""
    ticker = stock_data.get("ticker", "?")
    system, user = build_prompt(persona, soul, stock_data)

    analysis = call_deepseek(system, user)
    if not analysis:
        analysis = f"# {ticker} — {DATE_STR}\n\n*Analysis generation failed.*"

    web = fetch_web_research(ticker)
    header = f"# {ticker} — {persona.upper()} Analysis — {DATE_STR}\n\n"
    full = header + analysis
    if web:
        full += f"\n\n---\n## Web Research\n\n{web}"
    full += f"\n\n---\n*Generated {DATE_STR} by {persona.upper()} Arena*"

    filepath = write_analysis_file(ticker, persona, full, output_dir)

    # Review gate
    if not check_analysis(filepath):
        os.remove(filepath)
        print(f"[Runner:{persona}]  REJECTED: {ticker} — negative analysis deleted", flush=True)
        return {"ticker": ticker, "word_count": 0, "rejected": True}

    wc = len(full.split())
    print(f"[Runner:{persona}]  ✓ {ticker} — {wc} words", flush=True)
    return {"ticker": ticker, "word_count": wc, "file_path": filepath}


# ─── RUN PERSONA ─────────────────────────────────────────────────────────────

def run_persona(persona, stock_data_dict, output_dir):
    """
    Run analysis for one persona across all its assigned stocks.
    Uses ThreadPoolExecutor for 3 concurrent DeepSeek calls.
    Returns list of result dicts.
    """
    soul = load_soul(persona)
    if not soul:
        print(f"[Runner:{persona}]  ⚠ No SOUL.md, skipping", flush=True)
        return []

    results = []
    keys = sorted(stock_data_dict.keys())

    def work(ticker):
        data = stock_data_dict[ticker]
        return analyze_stock(persona, soul, data, output_dir)

    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = {pool.submit(work, t): t for t in keys}
        for fut in as_completed(futs):
            try:
                r = fut.result()
                if r and r.get("ticker"):
                    results.append(r)
            except Exception as e:
                print(f"[Runner:{persona}]  ❌ error: {e}", flush=True)

    ok = sum(1 for r in results if not r.get("rejected"))
    print(f"[Runner:{persona}]  Done: {ok} OK / {len(results)} total", flush=True)
    return results


# ─── CLI ENTRY ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    """
    Spawned as subprocess by orchestrator. Receives JSON via stdin:
    { "persona": "oneil", "stocks": {...}, "output_dir": "..." }
    """
    try:
        params = json.loads(sys.stdin.read())
    except Exception as e:
        print(json.dumps({"error": f"Failed to parse input: {e}"}))
        sys.exit(1)

    persona = params["persona"]
    stocks = params.get("stocks", {})
    output_dir = params.get("output_dir", "output")

    results = run_persona(persona, stocks, output_dir)

    print("\n---WORKER_RESULT---", flush=True)
    print(json.dumps({"persona": persona, "results": results}), flush=True)
