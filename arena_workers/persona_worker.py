#!/usr/bin/env python3
"""
persona_worker.py — Individual persona analysis worker.
Spawned by arena_runner.py Phase 3 for each persona.
Writes one file per stock: 10_Trading/Competition/{persona}/{ticker} - YYYY-MM-DD.md

Args via stdin JSON:
{
  "persona": "oneil",
  "stocks": {ticker: {fundamental_data}},
  "soul": "SOUL.md content",
  "date_str": "YYYY-MM-DD",
  "comp_dir": "/path/to/Competition",
  "indicators": {ticker: {indicator_data}},
  "tv_mcp": {ticker: {tv_cdp_data}},
  "deepseek_key": "sk-...",
  "deepseek_url": "https://api.deepseek.com/v1",
}
"""
import sys, json, os, datetime, time, warnings
import numpy as np
import yfinance as yf
import functools
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
warnings.filterwarnings("ignore")

def call_deepseek(system_prompt, user_message, api_key, base_url, timeout=300, max_tokens=8192):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt[:12000]},
            {"role": "user", "content": user_message[:20000]},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }
    try:
        resp = requests.post(f"{base_url}/chat/completions", headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return None


@functools.lru_cache(maxsize=50)
def perform_web_research(ticker):
    try:
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
        search_url = f"https://finance.yahoo.com/quote/{ticker}/news"
        headers_ua = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
        try:
            resp = requests.get(search_url, headers=headers_ua, timeout=10)
            if resp.status_code == 200:
                from html.parser import HTMLParser
                class HP(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.h = []
                        self._cap = False
                    def handle_starttag(self, tag, attrs):
                        if tag == "h3": self._cap = True
                    def handle_data(self, data):
                        if self._cap:
                            t = data.strip()
                            if t and len(t) > 10: self.h.append(t)
                            self._cap = False
                parser = HP()
                parser.feed(resp.text)
                if parser.h:
                    return "### Recent Headlines for " + ticker + "\n" + "\\n".join(f"- {h}" for h in parser.h[:5])
        except Exception:
            pass
        return None
    except Exception:
        return None


def write_stock_analysis(persona, stock_data_dict, soul, date_str, comp_dir, all_indicators, tv_mcp_data, deepseek_key, deepseek_url):
    """Write a single stock analysis file. Returns {ticker, word_count, file_path}."""
    ticker = stock_data_dict.get("ticker", "?")
    price = stock_data_dict.get("price", "?")
    sector = stock_data_dict.get("sector", "Unknown")
    change_pct = stock_data_dict.get("change_pct", 0)
    rsi = stock_data_dict.get("rsi")
    ma50 = stock_data_dict.get("ma50")
    ma200 = stock_data_dict.get("ma200")
    vol_ratio = stock_data_dict.get("vol_ratio")
    pe = stock_data_dict.get("pe")
    eps = stock_data_dict.get("eps")
    eps_growth = stock_data_dict.get("eps_growth")
    mcap = stock_data_dict.get("mcap")
    beta = stock_data_dict.get("beta")
    dividend_yield = stock_data_dict.get("dividend_yield")
    
    # Local indicators
    indicators = all_indicators.get(ticker, {}) if isinstance(all_indicators, dict) else {}
    local_ind = indicators.get("local_indicators", {}) if isinstance(indicators, dict) else indicators
    
    # TV MCP data
    tv_data = {}
    if isinstance(tv_mcp_data, dict):
        tv_data = tv_mcp_data.get(ticker, {})
    
    # Build indicator summary string for LLM prompt
    ind_lines = []
    if local_ind:
        for name, val in local_ind.items():
            ind_lines.append(f"  {name}: {val}")
    
    # Build TV data summary
    tv_lines = []
    if tv_data:
        if "pine_lines" in tv_data:
            tv_lines.append(f"  Pine Lines: {json.dumps(tv_data['pine_lines'])[:500]}")
        if "tv_indicators" in tv_data:
            tv_lines.append(f"  TV Indicators: {json.dumps(tv_data['tv_indicators'])[:1000]}")
        if "screenshot_path" in tv_data:
            tv_lines.append(f"  Screenshot: {tv_data['screenshot_path']}")
    
    # Web research
    web_research = perform_web_research(ticker)
    
    # FIX 6 — Use pre-computed Phase 1 data instead of fresh yfinance calls
    fresh_data = {}
    if stock_data_dict.get("price"):
        fresh_data["current_price"] = float(stock_data_dict["price"])
    if stock_data_dict.get("rsi") is not None:
        fresh_data["latest_rsi"] = stock_data_dict["rsi"]
    if isinstance(local_ind, dict) and local_ind.get("Volume"):
        fresh_data["last_volume"] = local_ind["Volume"]
    if isinstance(local_ind, dict) and local_ind.get("Volume_ratio"):
        fresh_data["vol_ratio"] = local_ind["Volume_ratio"]
    
    # Build the system prompt from SOUL.md
    system_prompt = f"""You are {persona.upper()}. This is your identity and voice — embody it completely.

{soul[:10000]}

CRITICAL RULES:
1. Write in the EXACT VOICE of {persona.upper()} — use their jargon, sentence structure, references
2. Include VERBATIM quotes from SOUL.md with source URLs
3. Analyze ALL available indicators (21+ technical, fundamental, macro)
4. 100+ words per indicator analysis
5. Must be 3000+ words total
6. Write as if you are {persona.upper()} personally analyzing this stock for your newsletter/blog"""

    user_message = f"""
## STOCK ANALYSIS REQUEST: {ticker}

**Date:** {date_str}
**Sector:** {sector}
**Price:** ${price}
**Change %:** {change_pct}%
**Market Cap:** {("$" + "{:,}".format(mcap)) if mcap else "N/A"}  ({("$" + "{:.2f}B".format(mcap/1e9)) if mcap else "N/A"})

### Fundamentals
- P/E: {pe if pe else "N/A"}
- EPS: {eps if eps else "N/A"}
- EPS Growth (Quarterly): {eps_growth if eps_growth else "N/A"}
- Beta: {beta if beta else "N/A"}
- Dividend Yield: {dividend_yield if dividend_yield else "N/A"}

### Technical Indicators
{chr(10).join(ind_lines[:30]) if ind_lines else "  (Indicators computed via yfinance)"}

### TV MCP Data
{chr(10).join(tv_lines) if tv_lines else "  (TV Desktop CDP data not available)"}

### Fresh yfinance Data
{json.dumps(fresh_data, indent=2) if fresh_data else "  (Not available)"}

### Web Research
{web_research if web_research else "  (No recent news found)"}

### Stock Context
- 50-day MA: ${ma50 if ma50 else "N/A"}
- 200-day MA: ${ma200 if ma200 else "N/A"}
- Volume Ratio (vs 50d avg): {vol_ratio if vol_ratio else "N/A"}x
- RSI(14): {rsi if rsi else "N/A"}

BEGIN YOUR FULL ANALYSIS BELOW. Must be 3000+ words. Each section must be substantive with specific numbers, not generic commentary. Write in first person as {persona.upper()}.
"""
    
    # Call DeepSeek
    analysis = call_deepseek(system_prompt, user_message, deepseek_key, deepseek_url)
    if not analysis:
        analysis = f"# {ticker} — {date_str}\\n\\n*Analysis generation failed.*"
    
    # Ensure file content has header
    header = f"# {ticker} — {persona.upper()} Analysis — {date_str}\\n\\n"
    full_content = header + analysis
    
    # Add web research appendix
    if web_research:
        full_content += f"\\n\\n---\\n## Web Research — Recent News\\n\\n{web_research}"
    
    full_content += f"\\n\\n---\\n*Generated {date_str} by {persona.upper()} Arena Worker*"
    
    # Save file
    persona_dir = os.path.join(comp_dir, persona)
    os.makedirs(persona_dir, exist_ok=True)
    filename = f"{ticker} - {date_str}.md"
    filepath = os.path.join(persona_dir, filename)
    
    with open(filepath, "w") as f:
        f.write(full_content)
    
    # FIX 2: Review Gate (Phase 3.5) - Lightweight verification
    analysis_lower = analysis.lower()
    content_prefix = analysis_lower[:2000]
    # FIX 3 — Only genuinely negative phrases; removed "pass" and "fail" (false positives)
    negative_phrases = [
        "don't buy", "avoid this stock",
        "this stock is garbage", "not a buy", "stay away", "terrible stock",
        "completely avoid", "no redeeming qualities",
        "this is a loser", "skip this one", "not worth your time", "garbage",
        "no confidence",
    ]
    negative_count = sum(1 for phrase in negative_phrases if phrase in content_prefix)
    last_500 = analysis_lower[-500:]
    # FIX 3 — Removed "pass" and "fail" (too many false positives)
    final_negative_phrases = [
        "avoid", "not a buy", "skip",
        "don't recommend", "no opportunity", "no setup",
    ]
    final_negative_count = sum(1 for phrase in final_negative_phrases if phrase in last_500)
    buy_watch_words = ["buy", "watch", "opportunity", "setup", "entry", "target", "potential", "strong"]
    has_actionable = any(w in content_prefix for w in buy_watch_words)
    is_negative = (
        negative_count >= 3 or
        (negative_count >= 2 and final_negative_count >= 2) or
        (final_negative_count >= 3) or
        (negative_count >= 2 and not has_actionable)
    )
    if is_negative:
        os.remove(filepath)
        print(f"[Worker:{persona}]  REJECTED: {ticker} - analysis was entirely negative", flush=True)
        return {"ticker": ticker, "word_count": 0, "file_path": None, "rejected": True}
    
    word_count = len(full_content.split())
    file_size = os.path.getsize(filepath)
    print(f"[Worker:{persona}]  ✓ {ticker} — {word_count} words, {file_size//1024}KB", flush=True)
    
    return {"ticker": ticker, "word_count": word_count, "file_path": filepath}


if __name__ == "__main__":
    try:
        raw = sys.stdin.read()
        params = json.loads(raw)
    except Exception as e:
        print(json.dumps({"error": f"Failed to parse input: {e}"}))
        sys.exit(1)
    
    persona = params["persona"]
    stocks = params["stocks"]
    soul = params["soul"]
    date_str = params["date_str"]
    comp_dir = params["comp_dir"]
    indicators = params.get("indicators", {})
    tv_mcp = params.get("tv_mcp", {})
    deepseek_key = params.get("deepseek_key", "")
    deepseek_url = params.get("deepseek_url", "https://api.deepseek.com/v1")
    
    results = []
    ticker_keys = sorted(stocks.keys())

    # ── Parallel stock analysis (3 concurrent DeepSeek calls) ──
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def analyze_one_stock(ticker_and_data):
        ticker, data = ticker_and_data
        print(f"[Worker:{persona}]  Analyzing {ticker}...", flush=True)
        try:
            return write_stock_analysis(
                persona, data, soul, date_str, comp_dir,
                indicators, tv_mcp, deepseek_key, deepseek_url
            )
        except Exception as e:
            print(f"[Worker:{persona}]  ⚠️ {ticker} error: {e}", flush=True)
            return {"ticker": ticker, "word_count": 0, "error": str(e)}

    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_map = {}
        for t in ticker_keys:
            fut = pool.submit(analyze_one_stock, (t, stocks[t]))
            fut_map[fut] = t
            time.sleep(0.5)  # stagger submissions to avoid hammering DeepSeek

        completed = 0
        failed = 0
        for fut in as_completed(fut_map):
            ticker = fut_map[fut]
            try:
                r = fut.result()
                if r and r.get("ticker"):
                    results.append(r)
                    completed += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1
                print(f"[Worker:{persona}]  ❌ {ticker} crashed: {e}", flush=True)
        print(f"[Worker:{persona}]  Done: {completed} OK / {failed} failed", flush=True)
 
    # Return results as JSON to stdout
    print(f"\\n---WORKER_RESULT---", flush=True)
    print(json.dumps({"persona": persona, "results": results}), flush=True)
