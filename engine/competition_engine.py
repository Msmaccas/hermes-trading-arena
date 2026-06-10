#!/usr/bin/env python3
"""
COMPETITION ENGINE v3 — FULL REDESIGN
======================================
Separates TRADING (10) from BUSINESS (3) personas.
For EACH persona:
  - Fetches FULL market context via TradingView scanner (8 markets)
  - Reads COMPLETE SOUL.md (NO truncation)
  - Builds prompt for MARKET SENTIMENT + variable watchlist (0-100 picks)
  - Calls DeepSeek API with FULL voice
  - Saves to Obsidian markdown files
  - Prints to terminal
  - Attempts Gemini Vision chart analysis for market context

Output structure:
  Trading:  10_Trading/Competition/{Persona} - YYYY-MM-DD.md
  Business: 00_Business/Competition/{Persona} - YYYY-MM-DD.md
  Index:    10_Trading/Competition/INDEX - YYYY-MM-DD.md
"""
import os, sys, datetime, re, json, urllib.request, urllib.error, base64, time, warnings
from pathlib import Path
from openai import OpenAI
from concurrent.futures import ThreadPoolExecutor, as_completed
import traceback

warnings.filterwarnings("ignore")

# ─── CONFIG ──────────────────────────────────────────────────────────────────

TODAY = datetime.date.today()
DATE_STR = TODAY.isoformat()

OBSIDIAN_VAULT = os.environ.get(
    "OBSIDIAN_VAULT_PATH",
    os.path.expanduser("~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Mind Palace Obsidian current")
)

TRADING_PERSONAS = [
    "oneil", "buffet", "lynch", "minervini", "qullamaggie",
    "david-ryan", "matt-caruso", "brian-shannon", "dan-zanger", "nick-schmidt",
]

# BUSINESS_PERSONAS — moved to separate repo: hermes-business-arena
BUSINESS_PERSONAS = []

TV_MARKETS = {
    "US":         "america",
    "China":      "china",
    "Hong Kong":  "hongkong",
    "India":      "india",
    "Japan":      "japan",
    "UK":         "uk",
    "Brazil":     "brazil",
    "Korea":      "korea",
}

ETF_KEYWORDS = [
    "ETF", "ETN", "ETP", "Leverage Shares", "ProShares", "Direxion",
    "MicroSectors", "UltraShort", "UltraPro", "Ultra", "Bull ",
    "Bear ", "Short ", "Inverse ", "2X", "3X",
]

PROFILES_DIR = os.path.expanduser("~/.hermes/profiles")


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def _load_env():
    """Load DEEPSEEK_API_KEY, GEMINI_API_KEY, etc from ~/.hermes/.env"""
    env_path = os.path.expanduser("~/.hermes/.env")
    env = {}
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    env[k] = v
    except Exception:
        pass
    return env


def _is_etf_product(desc):
    if not desc:
        return False
    upper = desc.upper()
    for kw in ETF_KEYWORDS:
        if kw.upper() in upper:
            return True
    if desc.startswith("iShares") or desc.startswith("SPDR") or desc.startswith("Invesco"):
        return True
    return False


# ─── MARKET CONTEXT ─────────────────────────────────────────────────────────

def fetch_tv_gainers(market_label, tv_region, top_n=15):
    """Fetch top gainers from TradingView scanner. Enhanced: more per market."""
    def _scan(sort_col, range_end=500):
        payload = {
            "columns": ["name", "close", "change", "change_abs", "volume", "description"],
            "sort": {"sortBy": sort_col, "sortOrder": "desc"},
            "range": [0, range_end],
        }
        url = f"https://scanner.tradingview.com/{tv_region}/scan"
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except Exception:
            return {}

    def _filter(raw_data, min_price, min_vol):
        stocks = []
        for item in (raw_data.get("data") or []):
            d = item.get("d", [])
            if len(d) < 5:
                continue
            ticker = d[0] or "?"
            price = d[1]
            chg = d[2]
            vol = d[4]
            desc = d[5] or ""
            if not isinstance(price, (int, float)) or not isinstance(vol, (int, float)):
                continue
            if not isinstance(chg, (int, float)) or chg <= 0:
                continue
            if price < min_price or vol < min_vol:
                continue
            if _is_etf_product(desc):
                continue
            if chg > 500:
                continue
            stocks.append({
                "ticker": ticker,
                "price": round(price, 2),
                "change_pct": round(chg, 2),
                "volume": int(vol),
                "name": desc,
            })
        return stocks

    if market_label == "US":
        pct_data = _scan("change")
        abs_data = _scan("change_abs")
        pct_stocks = _filter(pct_data, min_price=10, min_vol=200000)
        abs_stocks = _filter(abs_data, min_price=20, min_vol=500000)
        seen = {s["ticker"] for s in pct_stocks}
        for s in abs_stocks:
            if s["ticker"] not in seen:
                pct_stocks.append(s)
                seen.add(s["ticker"])
        pct_stocks.sort(key=lambda s: s["change_pct"], reverse=True)
        return pct_stocks[:top_n]

    thresholds = {
        "China": (5, 50000), "India": (5, 50000), "Japan": (5, 50000),
        "Korea": (5, 50000), "Hong Kong": (3, 30000),
        "Brazil": (5, 10000), "UK": (5, 50000),
    }
    min_price, min_vol = thresholds.get(market_label, (5, 50000))
    data = _scan("change")
    stocks = _filter(data, min_price, min_vol)
    stocks.sort(key=lambda s: s["change_pct"], reverse=True)
    return stocks[:top_n]


def fetch_market_context():
    """Fetch market context: indices, sector ETFs, and global gainers via TV scanner."""
    context = {
        "date": DATE_STR,
        "indices": {}, "sectors": {}, "gainers": {},
    }

    # Indices via TV since yfinance may be slow/unavailable
    print("[Engine]  Scanning global indices via TradingView...")
    index_tickers = {
        "SPY": "S&P 500", "QQQ": "Nasdaq 100", "DIA": "Dow Jones",
        "IWM": "Russell 2000", "GLD": "Gold", "TLT": "Long Bond",
        "XIV": "VIX",
    }
    for ticker, name in index_tickers.items():
        try:
            payload = {
                "columns": ["name", "close", "change", "change_abs", "volume"],
                "sort": {"sortBy": "name", "sortOrder": "asc"},
                "range": [0, 50],
                "filter": [{"left": "name", "operation": "equal", "right": ticker}],
            }
            url = "https://scanner.tradingview.com/america/scan"
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                if data.get("data"):
                    d = data["data"][0]["d"]
                    if len(d) >= 4 and isinstance(d[1], (int, float)):
                        close = d[1]
                        chg = d[2] if isinstance(d[2], (int, float)) else 0
                        context["indices"][ticker] = {
                            "name": name,
                            "price": round(close, 2),
                            "change_pct": round(chg, 2),
                        }
        except Exception:
            pass

    # Sector ETFs
    sectors = {
        "XLK": "Technology", "XLF": "Financials", "XLV": "Healthcare",
        "XLI": "Industrials", "XLE": "Energy", "XLP": "Consumer Staples",
        "XLY": "Consumer Discretionary", "XLB": "Materials", "XLU": "Utilities",
        "XLRE": "Real Estate", "SMH": "Semiconductors", "IBB": "Biotech",
        "ARKK": "ARK Innovation",
    }
    for ticker, name in sectors.items():
        try:
            payload = {
                "columns": ["name", "close", "change", "change_abs", "volume"],
                "sort": {"sortBy": "name", "sortOrder": "asc"},
                "range": [0, 50],
                "filter": [{"left": "name", "operation": "equal", "right": ticker}],
            }
            url = "https://scanner.tradingview.com/america/scan"
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                if data.get("data"):
                    d = data["data"][0]["d"]
                    if len(d) >= 4 and isinstance(d[1], (int, float)):
                        close = d[1]
                        chg = d[2] if isinstance(d[2], (int, float)) else 0
                        context["sectors"][ticker] = {
                            "name": name,
                            "price": round(close, 2),
                            "change_pct": round(chg, 2),
                        }
        except Exception:
            pass

    # Global gainers
    print("[Engine]  Scanning global markets for gainers...")
    for label, region in TV_MARKETS.items():
        stocks = fetch_tv_gainers(label, region, 15)
        context["gainers"][label] = stocks
        ticker_list = ", ".join(s["ticker"] for s in stocks[:5])
        print(f"[Engine]    {label}: {len(stocks)} gainers ({ticker_list})")

    return context


# ─── GEMINI VISION CHART ANALYSIS ──────────────────────────────────────────

def fetch_gemini_chart_analysis():
    """
    Attempt to analyze SPY/QQQ charts using Gemini Vision.
    Checks for screenshots in the TradingView MCP screenshot directory.
    Returns a text analysis or empty string if unavailable.
    """
    env = _load_env()
    gemini_key = (env.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY") or "")
    if not gemini_key:
        print("[Engine]  [Chart Vision] No GEMINI_API_KEY — skipping chart analysis")
        return ""

    screenshot_dir = Path(os.path.expanduser("~/tradingview-mcp-jackson/screenshots"))
    if not screenshot_dir.exists():
        print("[Engine]  [Chart Vision] No screenshot dir at ~/tradingview-mcp-jackson/screenshots/")
        return ""

    screenshots = sorted(screenshot_dir.glob("*.png"), key=os.path.getmtime, reverse=True)
    if not screenshots:
        print("[Engine]  [Chart Vision] No screenshots found")
        return ""

    # Find recent screenshots (within last hour)
    recent = [s for s in screenshots if time.time() - os.path.getmtime(s) < 3600]
    if not recent:
        print("[Engine]  [Chart Vision] No recent screenshots (< 1 hour old)")
        return ""

    print(f"[Engine]  [Chart Vision] Found {len(recent)} recent screenshots — analyzing with Gemini...")

    # Use the most recent one
    latest = recent[0]
    try:
        with open(latest, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")

        # Google AI Studio / Gemini API — image analysis
        payload = {
            "contents": [{
                "parts": [
                    {"text": "Analyze this chart in detail. Identify: 1) Trend direction and strength, "
                             "2) Key support/resistance levels, 3) Volume patterns, "
                             "4) Moving average alignment (20, 50, 200), "
                             "5) Any chart patterns (cup/handle, flag, VCP, etc.), "
                             "6) RSI/MACD readings if visible. Give a concise market assessment."},
                    {"inline_data": {"mime_type": "image/png", "data": img_b64}},
                ]
            }]
        }

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={gemini_key}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())

        text = ""
        candidates = result.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            for p in parts:
                text += p.get("text", "")

        if text:
            print(f"[Engine]  [Chart Vision] Analysis: {text[:200]}...")
            return text
    except Exception as e:
        print(f"[Engine]  [Chart Vision] Error: {e}")

    return ""


# ─── PERSONA SOUL ───────────────────────────────────────────────────────────

def read_full_soul(persona):
    """Read COMPLETE SOUL.md — NO truncation. Returns (content, error_message)."""
    # Try common path patterns
    candidates = [
        os.path.join(PROFILES_DIR, persona, "SOUL.md"),
        os.path.join(PROFILES_DIR, persona, "soul.md"),
        os.path.join(PROFILES_DIR, persona.replace("-", "_"), "SOUL.md"),
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                print(f"[Engine]  Read {persona} SOUL.md ({len(content)} chars)")
                return content, None
            except Exception as e:
                return "", str(e)

    return "", "SOUL.md not found"


# ─── PROMPT BUILDING ────────────────────────────────────────────────────────

def build_market_context_str(context):
    """Build a readable market context string for prompts."""
    lines = [f"# COMPETITION ENGINE — GLOBAL MARKET CONTEXT", f"Date: {context['date']}", ""]

    lines.append("## Global Indices")
    for k, v in sorted(context["indices"].items()):
        chg = v.get("change_pct", 0)
        lines.append(f"- {v['name']} ({k}): ${v['price']} ({chg:+.2f}%)")

    lines.append("")
    lines.append("## US Sector Performance")
    for k, v in sorted(context["sectors"].items()):
        chg = v.get("change_pct", 0)
        lines.append(f"- {v['name']} ({k}): ${v['price']} ({chg:+.2f}%)")

    for market_label in ["US", "China", "Hong Kong", "India", "Japan", "UK", "Brazil", "Korea"]:
        stocks = context["gainers"].get(market_label, [])
        if stocks:
            lines.append("")
            lines.append(f"## Top Gainers — {market_label}")
            for s in stocks[:10]:
                name = s.get("name", "")[:60]
                lines.append(f"- {s['ticker']} — ${s['price']} ({s['change_pct']:+.2f}%)  vol:{s['volume']:,}  {name}")

    return "\n".join(lines)


def build_trading_prompt(persona, context_str, chart_analysis=""):
    """Build the prompt for a TRADING persona — asks for complete market analysis + variable picks."""
    prompt = f"""You are analyzing the global markets as PART of a weekly trading competition. This is NOT a casual exercise — you are competing against 9 other legendary traders (Buffett, Lynch, Minervini, Qullamaggie, David Ryan, Matt Caruso, Brian Shannon, Dan Zanger, Nick Schmidt).

Below is the FULL market context from today ({DATE_STR}):

{context_str}
"""

    if chart_analysis:
        prompt += f"""

## Chart Pattern Analysis (Gemini Vision)
{chart_analysis}
"""

    prompt += """

## YOUR TASK

Begin your response with a ## MARKET SENTIMENT ASSESSMENT section (detailed, thorough).

THEN, produce your ## WATCHLIST section with your specific picks.

### Market Sentiment Requirements:
1. Assess EACH major market individually (US, China, India, Japan, Hong Kong, UK, Brazil, Korea)
2. Evaluate sector rotation — which sectors are leading/lagging?
3. Identify the current market phase (uptrend, correction, rally attempt, distribution phase, etc.)
4. Use YOUR SPECIFIC methodology indicators to read the market
5. Search both Chinese and English sources for global stock data in your analysis
6. Reference your past famous trades and how this market compares
7. Quote from YOUR books, interviews, or published work to support your analysis
8. Mention specific indicator readings

### Watchlist Requirements:
- The NUMBER of picks is VARIABLE — anywhere from 0 to 100 stocks
- If the market is risky (distribution, volatility, topping patterns), pick FEWER (0-5)
- If the market shows strong uptrend, broad participation, and accumulation, pick MORE (5-50)
- If the market is extraordinary with massive opportunity across sectors, pick up to 100
- Base the count on what the ACTUAL MARKET DATA shows you — let the market dictate, not a fixed number

### For EACH pick, include:
- Ticker and market
- Specific entry price or zone
- The precise indicator readings YOU use (RSI(14), MACD, VWAP, fib extensions, distribution days, VCP contraction count, weekly RS line position, volume vs 50-day avg, ATR, AVWAP levels, etc.)
- Which of your personal setups this matches
- A quote from your methodology justifying the pick
- Risk level (conservative / moderate / aggressive)

### Format:
Use MARKDOWN headings and bullet points. Write freely in your natural voice.

### Mandatory:
- Write a FULL detailed analysis — AT LEAST 5000 words total. Each individual stock pick must have AT LEAST 400 words of analysis covering: pattern identification, entry/exit levels, risk management, why this fits YOUR methodology, and a verbatim quote from your source material.
- Include references to your past famous trades for comparison (e.g., Cisco 1990, Apple 2004 for O'Neil; 10k→9figures for Qullamaggie; $18M→$42M for Zanger; etc.)
- Use YOUR EXACT voice. Quote from your books/interviews. Reference your past trades.
- Make specific, falsifiable predictions with timeframes.
- Be bold where warranted, cautious where warranted. NO generic boilerplate.

Return ONLY the analysis. No "I'll analyze this" meta-commentary. Just the analysis itself."""

    return prompt


def build_business_prompt(persona, context_str):
    """Build the prompt for a BUSINESS persona — market opportunity analysis."""
    prompt = f"""You are analyzing the current BUSINESS landscape as part of your strategy work.

Today's date: {DATE_STR}

## GLOBAL MARKET CONTEXT
{context_str}

## YOUR TASK

Write a FULL business strategy analysis (1000+ words minimum) covering:

1. **Market Opportunity Assessment** — Where is capital flowing? What industries are hot?
2. **Competitive Landscape** — Who is winning, who is losing, and why?
3. **Tactical Opportunities** — Specific business plays, sectors, or models to pursue
4. **Risk Assessment** — Macro risks, timing risks, execution risks

Use YOUR EXACT voice (Hormozi's Value Equation / Grand Slam Offer framework, Sam Ovens' Consulting Blueprint / Systemize methodology, Kallaway's content systems).

- Reference your actual business experiences and case studies
- Quote from your books and frameworks
- Make specific, actionable recommendations
- Identify 0-10 business opportunities with specific plays

Be thorough and specific. No generic advice."""
    return prompt


# ─── DEEPSEEK API ───────────────────────────────────────────────────────────

def get_deepseek_client():
    """Create OpenAI-compatible client for DeepSeek."""
    env = _load_env()
    api_key = (env.get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "")
    base_url = (env.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "https://api.deepseek.com/v1")

    if not api_key:
        print("[Engine]  WARNING: DEEPSEEK_API_KEY not found")

    return OpenAI(api_key=api_key or "missing", base_url=base_url)


def query_deepseek(client, persona, soul_content, prompt, persona_type="trading"):
    """Call DeepSeek with FULL soul content + detailed prompt. Returns raw text."""
    if not soul_content.strip():
        return "ERROR: No SOUL.md content available for this persona."

    system_prompt = (
        f"You are {persona}. "
        f"This is your COMPLETE identity, methodology, voice, and quote database. "
        f"Adopt this identity EXACTLY. Never break character. "
        f"Never speak as a generic analyst. Your quotes, your methodology, your voice.\n\n"
        f"{soul_content}"
    )

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.8,
            max_tokens=8192,
            timeout=180,
        )
        text = (response.choices[0].message.content or "").strip()

        if not text:
            print(f"[Engine]  {persona}: Empty response from DeepSeek")
            return "ERROR: Empty response from API."

        return text

    except Exception as e:
        err_msg = f"API_ERROR: {e}"
        print(f"[Engine]  {persona}: {err_msg}")
        return f"ERROR: {err_msg}"


# ─── OBSIDIAN OUTPUT ────────────────────────────────────────────────────────

def save_to_obsidian(persona, content, persona_type="trading"):
    """Save analysis to Obsidian vault. Returns (filepath, success_bool)."""
    if persona_type == "trading":
        subdir = "10_Trading/Competition"
    else:
        subdir = "00_Business/Competition"

    vault_dir = Path(OBSIDIAN_VAULT)
    output_dir = vault_dir / subdir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build frontmatter
    if persona_type == "trading":
        tags = "trading/competition"
        aliases = f"aliases:\n  - \"{persona} Weekly\"\n  - \"{persona} {DATE_STR}\""
    else:
        tags = "business/competition"
        aliases = f"aliases:\n  - \"{persona} Weekly\"\n  - \"{persona} {DATE_STR}\""

    # Capitalize persona name for display
    display_name = persona.replace("-", " ").title()

    frontmatter = f"""---
date: {DATE_STR}
type: competition-entry
persona: {persona}
persona_type: {persona_type}
tags: [{tags}]
week: {TODAY.isocalendar()[1]}
{aliases}
---

# {display_name} — Competition Entry
## Week of {DATE_STR}

"""

    full_content = frontmatter + content

    filename = f"{persona} - {DATE_STR}.md"
    filepath = output_dir / filename

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(full_content)
        print(f"[Engine]  Saved to Obsidian: {filepath}")
        return str(filepath), True
    except Exception as e:
        print(f"[Engine]  ERROR saving to Obsidian: {e}")
        return str(filepath), False


def create_index(persona_type="trading", entries=None):
    """Create INDEX file with links to all entries for the day."""
    if persona_type == "trading":
        subdir = "10_Trading/Competition"
    else:
        subdir = "00_Business/Competition"

    vault_dir = Path(OBSIDIAN_VAULT)
    output_dir = vault_dir / subdir
    output_dir.mkdir(parents=True, exist_ok=True)

    filename = f"INDEX - {DATE_STR}.md"
    filepath = output_dir / filename

    lines = [
        f"# COMPETITION INDEX — {DATE_STR}",
        f"",
        f"Week {TODAY.isocalendar()[1]} | {TODAY.strftime('%A')}",
        f"",
        f"## {persona_type.title()} Personas",
        f"",
    ]

    if entries:
        for entry in entries:
            persona = entry.get("persona", "unknown")
            status = entry.get("status", "unknown")
            word_count = entry.get("word_count", 0)
            picks_count = entry.get("picks_count", "?")
            display_name = persona.replace("-", " ").title()
            link = f"[[{persona} - {DATE_STR}]]"
            lines.append(f"- {link} — **{status}** ({word_count} words, {picks_count} picks)")

    lines.extend([
        f"",
        f"---",
        f"*Generated by Competition Engine v3 on {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
    ])

    content = "\n".join(lines)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[Engine]  INDEX saved: {filepath}")
        return str(filepath)
    except Exception as e:
        print(f"[Engine]  ERROR saving INDEX: {e}")
        return None


def save_terminal_log(persona, content, persona_type="trading"):
    """Also save a copy to ~/.hermes/logs/competition/ for reference."""
    log_dir = Path(os.path.expanduser(f"~/.hermes/logs/competition/{DATE_STR}"))
    log_dir.mkdir(parents=True, exist_ok=True)
    filename = log_dir / f"{persona}.md"
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        pass


# ─── PERSONA PROCESSOR ──────────────────────────────────────────────────────

def count_picks(text):
    """Estimate number of stock picks from analysis text."""
    # Look for ticker patterns
    tickers = set()
    # Match common ticker formats: $AAPL, AAPL, AAPL (NASDAQ), etc.
    for line in text.split("\n"):
        # Find patterns like bold tickers or listed stocks
        line_upper = line.upper().strip()
        if line_upper.startswith("- ") or line_upper.startswith("* "):
            # Check if this line starts with a ticker
            words = line_upper.split()
            for w in words:
                # Clean common chars
                w = w.strip("$*[]()`'\".,;:")
                if re.match(r'^[A-Z]{1,5}$', w):
                    if w not in ("I", "A", "AN", "THE", "IN", "ON", "AT", "TO", "FOR", "OF",
                                 "AND", "OR", "IS", "ARE", "WAS", "WERE", "BE", "BEEN",
                                 "HAS", "HAVE", "HAD", "DO", "DOES", "DID",
                                 "BUT", "AS", "WITH", "BY", "FROM", "NOT", "NO",
                                 "IT", "ITS", "WE", "YOU", "OUR", "YOUR", "THIS", "THAT",
                                 "ALL", "CAN", "WILL", "WOULD", "COULD", "SHOULD",
                                 "MAY", "MIGHT", "MUST", "THAN", "THEN", "THEM",
                                 "SOME", "ANY", "EACH", "EVERY", "BOTH", "FEW", "MORE",
                                 "RSI", "MACD", "VWAP", "EPS", "PEG", "ROE", "PE",
                                 "HIGH", "LOW", "NEW", "OLD", "BIG", "TOP", "KEY",
                                 "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN",
                                 "EIGHT", "NINE", "TEN", "NOW", "HOW", "WHY", "WHAT",
                                 "WHEN", "WHERE", "WHO", "WHICH",
                                 "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                                 "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
                                 "INC", "LLC", "LTD", "CORP", "CO",
                                 "NASDAQ", "NYSE", "AMEX", "OTC", "IPO", "ATH",
                                 "YTD", "YOY", "QTY",
                                 "ABC", "XYZ", "SOS", "DNA", "RNA", "CEO", "CFO",
                                 "CTA", "ROI", "KPI", "OKR",
                                 "SMA", "EMA", "ATR", "VCP", "RS",
                                 "SPY", "QQQ", "DIA", "IWM", "GLD", "TLT",
                                 "XLK", "XLF", "XLV", "XLI", "XLE", "XLP", "XLY",
                                 "XLB", "XLU", "XLRE", "SMH", "IBB", "ARKK"):
                        tickers.add(w)

    return len(tickers)


def process_trading_persona(client, persona, context, chart_analysis):
    """Process a single TRADING persona end-to-end."""
    print(f"\n{'='*60}")
    print(f"[Engine]  ▶ TRADING: {persona}")
    print(f"{'='*60}")

    # 1. Read FULL SOUL.md
    soul_content, err = read_full_soul(persona)
    if err or not soul_content.strip():
        print(f"[Engine]  ✗ {persona}: SOUL.md error: {err}")
        return {"persona": persona, "status": "ERROR", "word_count": 0, "picks_count": "?"}

    # 2. Build market context string
    context_str = build_market_context_str(context)

    # 3. Build the detailed prompt
    prompt = build_trading_prompt(persona, context_str, chart_analysis)

    print(f"[Engine]  Prompt built ({len(prompt)} chars, {len(soul_content)} chars of SOUL)")

    # 4. Call DeepSeek
    print(f"[Engine]  Calling DeepSeek for {persona}...")
    analysis = query_deepseek(client, persona, soul_content, prompt, "trading")

    if analysis.startswith("ERROR:"):
        print(f"[Engine]  ✗ {persona}: {analysis}")
        return {"persona": persona, "status": "ERROR", "word_count": 0, "picks_count": "?", "error": analysis}

    word_count = len(analysis.split())
    picks_count = count_picks(analysis)

    print(f"[Engine]  ✓ {persona}: {word_count} words, ~{picks_count} tickers mentioned")

    # 5. Save to Obsidian
    filepath, saved = save_to_obsidian(persona, analysis, "trading")

    # 6. Save terminal log copy
    save_terminal_log(persona, analysis, "trading")

    # 7. Print to terminal (condensed)
    print(f"\n─── {persona.upper()} ANALYSIS ───")
    print(analysis[:3000])
    if len(analysis) > 3000:
        print(f"\n... ({len(analysis)-3000} more chars) ...")
    print(f"\n─── END {persona.upper()} ───\n")

    return {
        "persona": persona,
        "status": "OK" if saved else "SAVE_FAILED",
        "word_count": word_count,
        "picks_count": picks_count,
        "filepath": filepath,
    }


def process_business_persona(client, persona, context):
    """Process a single BUSINESS persona end-to-end."""
    print(f"\n{'='*60}")
    print(f"[Engine]  ▶ BUSINESS: {persona}")
    print(f"{'='*60}")

    # 1. Read FULL SOUL.md
    soul_content, err = read_full_soul(persona)
    if err or not soul_content.strip():
        print(f"[Engine]  ✗ {persona}: SOUL.md error: {err}")
        return {"persona": persona, "status": "ERROR", "word_count": 0, "picks_count": "?"}

    # 2. Build context
    context_str = build_market_context_str(context)

    # 3. Build business prompt
    prompt = build_business_prompt(persona, context_str)

    print(f"[Engine]  Prompt built ({len(prompt)} chars, {len(soul_content)} chars of SOUL)")

    # 4. Call DeepSeek
    print(f"[Engine]  Calling DeepSeek for {persona}...")
    analysis = query_deepseek(client, persona, soul_content, prompt, "business")

    if analysis.startswith("ERROR:"):
        print(f"[Engine]  ✗ {persona}: {analysis}")
        return {"persona": persona, "status": "ERROR", "word_count": 0, "picks_count": "?"}

    word_count = len(analysis.split())

    print(f"[Engine]  ✓ {persona}: {word_count} words")

    # 5. Save to Obsidian
    filepath, saved = save_to_obsidian(persona, analysis, "business")

    # 6. Save terminal log
    save_terminal_log(persona, analysis, "business")

    # 7. Print to terminal
    print(f"\n─── {persona.upper()} ANALYSIS ───")
    print(analysis[:2000])
    if len(analysis) > 2000:
        print(f"\n... ({len(analysis)-2000} more chars) ...")
    print(f"\n─── END {persona.upper()} ───\n")

    return {
        "persona": persona,
        "status": "OK" if saved else "SAVE_FAILED",
        "word_count": word_count,
        "picks_count": "N/A",
        "filepath": filepath,
    }


# ─── MAIN ────────────────────────────────────────────────────────────────────

def run():
    print(f"\n{'='*70}")
    print(f"  COMPETITION ENGINE v3 — {DATE_STR}")
    print(f"{'='*70}")
    print(f"[Engine]  Obsidian Vault: {OBSIDIAN_VAULT}")
    print(f"[Engine]  Trading Personas: {len(TRADING_PERSONAS)} ({', '.join(TRADING_PERSONAS)})")
    print(f"[Engine]  Business Personas: {len(BUSINESS_PERSONAS)} ({', '.join(BUSINESS_PERSONAS)})")
    print()

    # 1. Fetch market context
    print("[Engine]  Phase 1: Fetching market context...")
    context = fetch_market_context()
    print(f"[Engine]  Market context: {len(context['indices'])} indices, "
          f"{len(context['sectors'])} sectors, "
          f"{sum(len(v) for v in context['gainers'].values())} global gainers")
    print()

    # 2. Chart analysis via Gemini Vision (optional)
    print("[Engine]  Phase 2: Chart analysis via Gemini Vision...")
    chart_analysis = fetch_gemini_chart_analysis()
    if chart_analysis:
        print(f"[Engine]  Chart analysis: {len(chart_analysis)} chars")
    else:
        print("[Engine]  Chart analysis: skipped (no recent screenshots)")
    print()

    # 3. Get DeepSeek client
    client = get_deepseek_client()

    # 4. Process TRADING personas in parallel
    print("[Engine]  Phase 3: Processing TRADING personas (4 workers)...")
    trading_results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {}
        for p in TRADING_PERSONAS:
            future = executor.submit(process_trading_persona, client, p, context, chart_analysis)
            futures[future] = p

        for future in as_completed(futures):
            persona = futures[future]
            try:
                result = future.result()
                trading_results.append(result)
                s = result["status"]
                wc = result["word_count"]
                pc = result["picks_count"]
                icon = "✓" if s == "OK" else "✗"
                print(f"\n[Engine]  {icon} {persona}: {s} | {wc} words | ~{pc} picks\n")
            except Exception as e:
                print(f"[Engine]  ✗ {persona}: Thread crashed — {e}")
                traceback.print_exc()
                trading_results.append({"persona": persona, "status": "CRASHED", "word_count": 0, "picks_count": "?"})

    trading_results.sort(key=lambda r: TRADING_PERSONAS.index(r["persona"]))

    # 5. Process BUSINESS personas in parallel
    print(f"\n{'='*70}")
    print("[Engine]  Phase 4: Processing BUSINESS personas (4 workers)...")
    print(f"{'='*70}")
    business_results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {}
        for p in BUSINESS_PERSONAS:
            future = executor.submit(process_business_persona, client, p, context)
            futures[future] = p

        for future in as_completed(futures):
            persona = futures[future]
            try:
                result = future.result()
                business_results.append(result)
                s = result["status"]
                wc = result["word_count"]
                icon = "✓" if s == "OK" else "✗"
                print(f"\n[Engine]  {icon} {persona}: {s} | {wc} words\n")
            except Exception as e:
                print(f"[Engine]  ✗ {persona}: Thread crashed — {e}")
                business_results.append({"persona": persona, "status": "CRASHED", "word_count": 0, "picks_count": "?"})

    business_results.sort(key=lambda r: BUSINESS_PERSONAS.index(r["persona"]))

    # 6. Create INDEX files
    print(f"\n{'='*70}")
    print("[Engine]  Phase 5: Creating INDEX files...")
    create_index("trading", trading_results)
    create_index("business", business_results)

    # 7. Final summary
    print(f"\n{'='*70}")
    print(f"  COMPETITION ENGINE v3 — SUMMARY")
    print(f"{'='*70}")
    print(f"  Date: {DATE_STR}")
    print()

    print(f"  TRADING PERSONAS:")
    for r in trading_results:
        icon = "✓" if r["status"] == "OK" else "✗"
        fp = r.get("filepath", "?")
        print(f"    {icon} {r['persona']:20s} {r['status']:12s} {r['word_count']:5d} words  ~{r['picks_count']} picks")
        if fp and r["status"] == "OK":
            print(f"       → {fp}")

    print()
    print(f"  BUSINESS PERSONAS:")
    for r in business_results:
        icon = "✓" if r["status"] == "OK" else "✗"
        fp = r.get("filepath", "?")
        print(f"    {icon} {r['persona']:20s} {r['status']:12s} {r['word_count']:5d} words")
        if fp and r["status"] == "OK":
            print(f"       → {fp}")

    total_ok = sum(1 for r in trading_results + business_results if r["status"] == "OK")
    total_all = len(trading_results) + len(business_results)
    total_words = sum(r["word_count"] for r in trading_results + business_results)

    print()
    print(f"  RESULTS: {total_ok}/{total_all} succeeded")
    print(f"  TOTAL:   {total_words:,} words of analysis generated")
    print(f"{'='*70}")
    print(f"[Engine]  Done. Open Obsidian to read the full entries.")
    print()


if __name__ == "__main__":
    run()
