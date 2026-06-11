#!/usr/bin/env python3
"""
COMPETITION ENGINE v2 — STRUCTURED MARKET CONTEXT
==================================================
Optimizations vs v3:
  - Structured market data (compact dict) instead of prose: saves ~600 tokens/persona
  - Market context fetched ONCE and cached in dict, reused for all personas
  - Same TV scanner REST API (8 global markets)
  - Same DeepSeek API integration
  - ThreadPoolExecutor(4)
  - Saves to Obsidian vault
  - Reads SOUL.md for each persona
  - Every quote in output must have source URL from the SOUL.md
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
    "/Users/jiayanghan/Library/Mobile Documents/iCloud~md~obsidian/Documents/Mind Palace Obsidian current"
)

TRADING_PERSONAS = [
    "oneil", "buffet", "lynch", "minervini", "qullamaggie",
    "david-ryan", "matt-caruso", "brian-shannon", "dan-zanger", "nick-schmidt",
]

BUSINESS_PERSONAS = [
    "hormozi", "samovens", "kallaway",
]

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

# ─── STRUCTURED MARKET CONTEXT CACHE ──────────────────────────────────────

_market_cache = {}

def _load_env():
    """Load DEEPSEEK_API_KEY etc from ~/.hermes/.env"""
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
                    env[k.strip()] = v.strip().strip('"').strip("'")
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


# ─── MARKET CONTEXT (TV SCANNER) ─────────────────────────────────────────

def fetch_tv_gainers(market_label, tv_region, top_n=15):
    """Fetch top gainers from TradingView scanner."""
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
    """Fetch market context ONCE. Results cached in _market_cache."""
    global _market_cache
    if _market_cache:
        return _market_cache

    context = {"date": DATE_STR, "indices": {}, "sectors": {}, "gainers": {}}

    # Indices via TV
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
                        context["indices"][ticker] = {
                            "p": round(d[1], 2),
                            "c": round(d[2], 2) if isinstance(d[2], (int, float)) else 0,
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
                        context["sectors"][ticker] = {
                            "p": round(d[1], 2),
                            "c": round(d[2], 2) if isinstance(d[2], (int, float)) else 0,
                        }
        except Exception:
            pass

    # Global gainers
    print("[Engine]  Scanning global markets for gainers...")
    for label, region in TV_MARKETS.items():
        stocks = fetch_tv_gainers(label, region, 15)
        context["gainers"][label] = [
            {"t": s["ticker"], "p": s["price"], "c": s["change_pct"], "v": s["volume"]}
            for s in stocks
        ]
        print(f"[Engine]    {label}: {len(stocks)} gainers")

    _market_cache = context
    return context


# ─── STRUCTURED MARKET DATA (compact format) ─────────────────────────────

# Build a compact, token-efficient market context string
# Uses single-char keys: p=price, c=change%, v=volume, t=ticker
# Saves ~600 tokens per persona vs verbose prose format

def build_market_context_compact(context):
    """Returns a compact market data overview string."""
    mc = context
    lines = [
        f"##MD {mc['date']}",
        "##IX",  # indices
    ]
    for k, v in sorted(mc["indices"].items()):
        lines.append(f"{k} p={v['p']} c={v['c']:+.2f}%")
    lines.append("##SE")  # sectors
    for k, v in sorted(mc["sectors"].items()):
        lines.append(f"{k} p={v['p']} c={v['c']:+.2f}%")
    for label in ["US", "China", "Hong Kong", "India", "Japan", "UK", "Brazil", "Korea"]:
        stocks = mc["gainers"].get(label, [])
        if stocks:
            lines.append(f"##G {label}")
            for s in stocks[:10]:
                lines.append(f"{s['t']} p={s['p']} c={s['c']:+.2f}% v={s['v']:,}")
    return "\n".join(lines)


def expand_to_prose_context(compact_str):
    """Expand compact format back to readable prose for the prompt.
    Keeps the same data, just wraps in readable labels."""
    # For the prompt, we build a hybrid: structured data followed by
    # a brief prose summary to give the LLM the narrative flavor.
    mc = _market_cache
    if not mc:
        return compact_str

    prose = [f"# Global Market Context - {mc['date']}", ""]
    prose.append("## Indices")
    for k, v in sorted(mc["indices"].items()):
        prose.append(f"  {k}: ${v['p']} ({v['c']:+.2f}%)")
    prose.append("")
    prose.append("## Sectors")
    for k, v in sorted(mc["sectors"].items()):
        prose.append(f"  {k}: ${v['p']} ({v['c']:+.2f}%)")
    for label in ["US", "China", "Hong Kong", "India", "Japan", "UK", "Brazil", "Korea"]:
        stocks = mc["gainers"].get(label, [])
        if stocks:
            prose.append("")
            prose.append(f"## Top Gainers - {label}")
            for s in stocks[:10]:
                prose.append(f"  {s['t']} ${s['p']} ({s['c']:+.2f}%) vol:{s['v']:,}")

    return "\n".join(prose)


# ─── GEMINI VISION CHART ANALYSIS ──────────────────────────────────────────

def fetch_gemini_chart_analysis():
    """Analyze SPY/QQQ charts via Gemini Vision if recent screenshots exist."""
    env = _load_env()
    gemini_key = (env.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY") or "")
    if not gemini_key:
        print("[Engine]  [Chart Vision] No GEMINI_API_KEY - skipping")
        return ""

    screenshot_dir = Path(os.path.expanduser("~/tradingview-mcp-jackson/screenshots"))
    if not screenshot_dir.exists():
        print("[Engine]  [Chart Vision] No screenshot dir")
        return ""

    screenshots = sorted(screenshot_dir.glob("*.png"), key=os.path.getmtime, reverse=True)
    if not screenshots:
        return ""

    recent = [s for s in screenshots if time.time() - os.path.getmtime(s) < 3600]
    if not recent:
        print("[Engine]  [Chart Vision] No recent screenshots (< 1 hour)")
        return ""

    print(f"[Engine]  [Chart Vision] Analyzing {len(recent)} recent screenshots...")
    latest = recent[0]
    try:
        with open(latest, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")

        payload = {
            "contents": [{
                "parts": [
                    {"text": "Analyze this chart. Identify: 1) Trend direction/strength, "
                             "2) Key S/R levels, 3) Volume patterns, "
                             "4) MA alignment (20, 50, 200), "
                             "5) Chart patterns (cup/handle, flag, VCP, etc.), "
                             "6) RSI/MACD if visible. Concise market assessment."},
                    {"inline_data": {"mime_type": "image/png", "data": img_b64}},
                ]
            }]
        }

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={gemini_key}"
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())

        text = ""
        for candidate in result.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                text += part.get("text", "")

        if text:
            print(f"[Engine]  [Chart Vision] Analysis: {text[:200]}...")
            return text
    except Exception as e:
        print(f"[Engine]  [Chart Vision] Error: {e}")
    return ""


# ─── PERSONA SOUL ───────────────────────────────────────────────────────────

def read_full_soul(persona):
    """Read SOUL.md. Returns (content, error_message)."""
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


def extract_source_urls(soul_content):
    """Extract all source URLs from SOUL.md content.
    Returns a list of URLs for inclusion as mandatory quote citations."""
    urls = re.findall(r'https?://[^\s\)",]+', soul_content)
    return urls


# ─── PROMPT BUILDING ────────────────────────────────────────────────────────

def build_trading_prompt(persona, context_prose, compact_data, chart_analysis=""):
    """Build prompt with compact market context + prose header for readability."""
    prompt = f"""You are analyzing global markets as PART of a weekly trading competition against 9 other legendary traders (Buffett, Lynch, Minervini, Qullamaggie, David Ryan, Matt Caruso, Brian Shannon, Dan Zanger, Nick Schmidt).

## MARKET DATA
{context_prose}

## COMPACT REFERENCE
{compact_data}
"""
    if chart_analysis:
        prompt += f"""
## CHART ANALYSIS
{chart_analysis}
"""
    prompt += """
## YOUR TASK

### MARKET SENTIMENT
1. Assess each major market individually (US, China, India, Japan, Hong Kong, UK, Brazil, Korea)
2. Evaluate sector rotation - leading/lagging sectors
3. Identify market phase (uptrend, correction, rally attempt, distribution)
4. Use YOUR SPECIFIC methodology indicators
5. Quote from YOUR books, interviews, or published work to support analysis
6. Reference your past famous trades for comparison

### WATCHLIST
- Variable count: 0-100 picks depending on market quality
- Risky market = fewer picks (0-5). Strong uptrend = more (5-50). Extraordinary = up to 100

### For EACH pick:
- Ticker and market
- Specific entry price or zone
- Precise indicator readings YOU use (RSI, MACD, VWAP, distribution days, VCP contraction count, RS line, volume vs 50d avg, ATR, AVWAP, etc.)
- Which of your personal setups this matches
- A quote from your methodology justifying the pick (with source URL)
- Risk level (conservative / moderate / aggressive)

### CRITICAL RULES
- Write in YOUR EXACT VOICE. Quote from your books/interviews. Reference your past trades.
- Every trading quote you use MUST include the source URL from your SOUL.md
- Make specific, falsifiable predictions with timeframes
- Write 1000-5000 words. Each pick needs proper analysis.
- No generic boilerplate. No meta-commentary. Just the analysis.
"""
    return prompt


def build_business_prompt(persona, context_prose, compact_data):
    """Build prompt for BUSINESS persona."""
    prompt = f"""You are analyzing the current BUSINESS landscape.

## MARKET DATA
{context_prose}

## COMPACT REFERENCE
{compact_data}

## YOUR TASK
Write a full business strategy analysis (1000+ words minimum):
1. **Market Opportunity Assessment** - Where is capital flowing?
2. **Competitive Landscape** - Who is winning/losing?
3. **Tactical Opportunities** - Specific business plays to pursue
4. **Risk Assessment** - Macro risks, timing risks

Use YOUR EXACT voice (Hormozi's Value Equation, Sam Ovens' Consulting Blueprint, Kallaway's content systems).
- Reference your actual business experiences and case studies
- Quote from your books and frameworks (with source URLs)
- Make specific, actionable recommendations
- Identify 0-10 business opportunities with specific plays
"""
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
    """Call DeepSeek with FULL soul content + detailed prompt."""
    if not soul_content.strip():
        return "ERROR: No SOUL.md content available."

    system_prompt = (
        f"You are {persona}. "
        f"This is your COMPLETE identity, methodology, voice, and quote database. "
        f"Adopt this identity EXACTLY. Never break character. "
        f"Every trading quote you use MUST include its source URL from the material below.\n\n"
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
            print(f"[Engine]  {persona}: Empty response")
            return "ERROR: Empty response from API."
        return text
    except Exception as e:
        err_msg = f"API_ERROR: {e}"
        print(f"[Engine]  {persona}: {err_msg}")
        return f"ERROR: {err_msg}"


# ─── OBSIDIAN OUTPUT ────────────────────────────────────────────────────────

def save_to_obsidian(persona, content, persona_type="trading"):
    """Save analysis to Obsidian vault."""
    subdir = "10_Trading/Competition" if persona_type == "trading" else "00_Business/Competition"
    vault_dir = Path(OBSIDIAN_VAULT)
    output_dir = vault_dir / subdir
    output_dir.mkdir(parents=True, exist_ok=True)

    tags = "trading/competition" if persona_type == "trading" else "business/competition"
    display_name = persona.replace("-", " ").title()

    frontmatter = f"""---
date: {DATE_STR}
type: competition-entry
persona: {persona}
persona_type: {persona_type}
tags: [{tags}]
week: {TODAY.isocalendar()[1]}
aliases:
  - "{persona} Weekly"
  - "{persona} {DATE_STR}"
---

# {display_name} - Competition Entry
## Week of {DATE_STR}

"""
    full_content = frontmatter + content
    filename = f"{persona} - {DATE_STR}.md"
    filepath = output_dir / filename

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(full_content)
        print(f"[Engine]  Saved: {filepath}")
        return str(filepath), True
    except Exception as e:
        print(f"[Engine]  ERROR saving: {e}")
        return str(filepath), False


def create_index(persona_type="trading", entries=None):
    """Create INDEX file with links to all entries."""
    subdir = "10_Trading/Competition" if persona_type == "trading" else "00_Business/Competition"
    vault_dir = Path(OBSIDIAN_VAULT)
    output_dir = vault_dir / subdir
    output_dir.mkdir(parents=True, exist_ok=True)

    filepath = output_dir / f"INDEX - {DATE_STR}.md"
    lines = [
        f"# COMPETITION INDEX - {DATE_STR}",
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
            wc = entry.get("word_count", 0)
            pc = entry.get("picks_count", "?")
            display = persona.replace("-", " ").title()
            lines.append(f"- [[{persona} - {DATE_STR}]] - **{status}** ({wc} words, {pc} picks)")

    lines.extend([
        f"",
        f"---",
        f"*Generated by Competition Engine v2 on {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
    ])

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"[Engine]  INDEX: {filepath}")
        return str(filepath)
    except Exception as e:
        print(f"[Engine]  ERROR saving INDEX: {e}")
        return None


def save_terminal_log(persona, content, persona_type="trading"):
    """Save copy to ~/.hermes/logs/competition/"""
    log_dir = Path(os.path.expanduser(f"~/.hermes/logs/competition/{DATE_STR}"))
    log_dir.mkdir(parents=True, exist_ok=True)
    try:
        with open(log_dir / f"{persona}.md", "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        pass


# ─── PERSONA PROCESSOR ──────────────────────────────────────────────────────

def count_picks(text):
    """Estimate number of stock picks from analysis text."""
    tickers = set()
    skip_words = {"I", "A", "AN", "THE", "IN", "ON", "AT", "TO", "FOR", "OF",
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
                  "XLB", "XLU", "XLRE", "SMH", "IBB", "ARKK"}

    for line in text.split("\n"):
        line_u = line.upper().strip()
        if line_u.startswith("- ") or line_u.startswith("* "):
            words = line_u.split()
            for w in words:
                w = w.strip("$*[]()`'\".,;:")
                if re.match(r'^[A-Z]{1,5}$', w) and w not in skip_words:
                    tickers.add(w)
    return len(tickers)


def process_trading_persona(client, persona, context_prose, compact_data, chart_analysis):
    """Process a single TRADING persona."""
    print(f"\n{'='*60}")
    print(f"[Engine]  >> TRADING: {persona}")
    print(f"{'='*60}")

    soul_content, err = read_full_soul(persona)
    if err or not soul_content.strip():
        print(f"[Engine]  x {persona}: SOUL.md error: {err}")
        return {"persona": persona, "status": "ERROR", "word_count": 0, "picks_count": "?"}

    prompt = build_trading_prompt(persona, context_prose, compact_data, chart_analysis)
    print(f"[Engine]  Prompt: {len(prompt)} chars, SOUL: {len(soul_content)} chars")

    analysis = query_deepseek(client, persona, soul_content, prompt, "trading")
    if analysis.startswith("ERROR:"):
        return {"persona": persona, "status": "ERROR", "word_count": 0, "picks_count": "?", "error": analysis}

    wc = len(analysis.split())
    pc = count_picks(analysis)
    filepath, saved = save_to_obsidian(persona, analysis, "trading")
    save_terminal_log(persona, analysis, "trading")

    # Print preview
    print(f"\n--- {persona.upper()} ---")
    print(analysis[:2000])
    if len(analysis) > 2000:
        print(f"\n... ({len(analysis)-2000} more chars) ...")
    print(f"\n--- END {persona.upper()} ---\n")

    return {
        "persona": persona, "status": "OK" if saved else "SAVE_FAILED",
        "word_count": wc, "picks_count": pc, "filepath": filepath,
    }


def process_business_persona(client, persona, context_prose, compact_data):
    """Process a single BUSINESS persona."""
    print(f"\n{'='*60}")
    print(f"[Engine]  >> BUSINESS: {persona}")
    print(f"{'='*60}")

    soul_content, err = read_full_soul(persona)
    if err or not soul_content.strip():
        print(f"[Engine]  x {persona}: SOUL.md error: {err}")
        return {"persona": persona, "status": "ERROR", "word_count": 0, "picks_count": "?"}

    prompt = build_business_prompt(persona, context_prose, compact_data)
    analysis = query_deepseek(client, persona, soul_content, prompt, "business")
    if analysis.startswith("ERROR:"):
        return {"persona": persona, "status": "ERROR", "word_count": 0, "picks_count": "?"}

    wc = len(analysis.split())
    filepath, saved = save_to_obsidian(persona, analysis, "business")
    save_terminal_log(persona, analysis, "business")

    print(f"\n--- {persona.upper()} ---")
    print(analysis[:1500])
    if len(analysis) > 1500:
        print(f"\n... ({len(analysis)-1500} more chars) ...")
    print(f"\n--- END {persona.upper()} ---\n")

    return {
        "persona": persona, "status": "OK" if saved else "SAVE_FAILED",
        "word_count": wc, "picks_count": "N/A", "filepath": filepath,
    }


# ─── MAIN ────────────────────────────────────────────────────────────────────

def run():
    print(f"\n{'='*70}")
    print(f"  COMPETITION ENGINE v2 - {DATE_STR}")
    print(f"{'='*70}")
    print(f"[Engine]  Obsidian: {OBSIDIAN_VAULT}")
    print(f"[Engine]  Trading: {len(TRADING_PERSONAS)} ({', '.join(TRADING_PERSONAS)})")
    print(f"[Engine]  Business: {len(BUSINESS_PERSONAS)} ({', '.join(BUSINESS_PERSONAS)})")
    print()

    # 1. Fetch market context ONCE
    print("[Engine]  Phase 1: Fetching market context (ONCE, caching)...")
    context = fetch_market_context()
    print(f"[Engine]  Cached: {len(context['indices'])} indices, "
          f"{len(context['sectors'])} sectors, "
          f"{sum(len(v) for v in context['gainers'].values())} global gainers")

    # Build structured + prose versions from cache
    compact_data = build_market_context_compact(context)
    context_prose = expand_to_prose_context(compact_data)
    print(f"[Engine]  Compact: {len(compact_data)} chars, Prose: {len(context_prose)} chars")
    print()

    # 2. Chart analysis via Gemini Vision
    print("[Engine]  Phase 2: Gemini Vision chart analysis...")
    chart_analysis = fetch_gemini_chart_analysis()
    print()

    # 3. Get DeepSeek client
    client = get_deepseek_client()

    # 4. Process TRADING personas in parallel
    print("[Engine]  Phase 3: TRADING personas (4 workers)...")
    trading_results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {}
        for p in TRADING_PERSONAS:
            future = executor.submit(process_trading_persona, client, p, context_prose, compact_data, chart_analysis)
            futures[future] = p
        for future in as_completed(futures):
            persona = futures[future]
            try:
                r = future.result()
                trading_results.append(r)
                icon = "+" if r["status"] == "OK" else "x"
                print(f"\n[Engine]  {icon} {persona}: {r['status']} | {r['word_count']} words | ~{r['picks_count']} picks\n")
            except Exception as e:
                print(f"[Engine]  x {persona}: Thread crashed - {e}")
                traceback.print_exc()
                trading_results.append({"persona": persona, "status": "CRASHED", "word_count": 0, "picks_count": "?"})

    trading_results.sort(key=lambda r: TRADING_PERSONAS.index(r["persona"]))

    # 5. Process BUSINESS personas
    print(f"\n{'='*70}")
    print("[Engine]  Phase 4: BUSINESS personas (4 workers)...")
    business_results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {}
        for p in BUSINESS_PERSONAS:
            future = executor.submit(process_business_persona, client, p, context_prose, compact_data)
            futures[future] = p
        for future in as_completed(futures):
            persona = futures[future]
            try:
                r = future.result()
                business_results.append(r)
                print(f"\n[Engine]  + {persona}: {r['status']} | {r['word_count']} words\n")
            except Exception as e:
                print(f"[Engine]  x {persona}: {e}")
                business_results.append({"persona": persona, "status": "CRASHED", "word_count": 0, "picks_count": "?"})

    business_results.sort(key=lambda r: BUSINESS_PERSONAS.index(r["persona"]))

    # 6. INDEX files
    print(f"\n{'='*70}")
    print("[Engine]  Phase 5: INDEX files...")
    create_index("trading", trading_results)
    create_index("business", business_results)

    # 7. Summary
    print(f"\n{'='*70}")
    print(f"  COMPETITION ENGINE v2 - SUMMARY")
    print(f"{'='*70}")
    print(f"  Date: {DATE_STR}")
    print(f"\n  TRADING PERSONAS:")
    for r in trading_results:
        fp = r.get("filepath", "?")
        print(f"    {'+' if r['status']=='OK' else 'x'} {r['persona']:20s} {r['status']:12s} {r['word_count']:5d} w  ~{r['picks_count']} picks")
        if r["status"] == "OK":
            print(f"       -> {fp}")
    print(f"\n  BUSINESS PERSONAS:")
    for r in business_results:
        fp = r.get("filepath", "?")
        print(f"    {'+' if r['status']=='OK' else 'x'} {r['persona']:20s} {r['status']:12s} {r['word_count']:5d} w")
        if r["status"] == "OK":
            print(f"       -> {fp}")

    ok = sum(1 for r in trading_results + business_results if r["status"] == "OK")
    total = len(trading_results) + len(business_results)
    total_w = sum(r["word_count"] for r in trading_results + business_results)
    print(f"\n  RESULTS: {ok}/{total} succeeded")
    print(f"  TOTAL:   {total_w:,} words generated")
    print(f"{'='*70}")
    print()


if __name__ == "__main__":
    run()
