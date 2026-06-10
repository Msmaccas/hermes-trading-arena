#!/usr/bin/env python3
"""
DEEP RESEARCH ENGINE v1 — Grounded, Two-Phase Trading Competition
=================================================================
Unlike the standard competition engine that lets the LLM hallucinate prices/data,
this engine fetches REAL data from yfinance and the web, then feeds it to
DeepSeek for persona-voiced analysis.

Flow:
  Phase 1: DeepSeek v4 Flash generates watchlist per persona (0-100 picks)
  Phase 2: For each watchlist stock → fetch REAL yfinance data + news
  Phase 3: Feed REAL data + SOUL.md back to DeepSeek for final analysis
  Phase 4: Save to Obsidian vault (same path as competition engine)

Output:
  ~/Library/.../10_Trading/Competition/{persona} - YYYY-MM-DD.md
"""
import os, sys, datetime, re, json, urllib.request, urllib.error, base64, time, warnings, traceback
from pathlib import Path
from openai import OpenAI
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
# Import accuracy tracking + dedup
import sys
_fixes_path = os.path.expanduser("~/.hermes/scripts/engine_fixes")
if _fixes_path not in sys.path:
    sys.path.insert(0, _fixes_path)
try:
    from engine_fixes import init_accuracy_db, record_pick, score_week_picks, resolve_watchlist_conflicts, compute_rankings
    _HAS_ACCURACY = True
except ImportError:
    _HAS_ACCURACY = False

import requests
from bs4 import BeautifulSoup
import yfinance as yf

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

# ─── PERSONA MARKET ROTATION ──────────────────────────────────────────────────
# Each persona has a PRIMARY market focus + 2+ secondary markets.
# This FORCES global coverage and prevents everyone picking the same US stocks.
PERSONA_MARKET_ROTATION = {
    "oneil":          {"primary": "US", "secondary": ["Hong Kong", "Korea"]},
    "buffet":         {"primary": "Japan", "secondary": ["US", "UK"]},
    "lynch":          {"primary": "UK", "secondary": ["Europe", "India"]},
    "minervini":      {"primary": "US", "secondary": ["India", "Brazil"]},
    "qullamaggie":    {"primary": "Korea", "secondary": ["Japan", "US"]},
    "david-ryan":     {"primary": "Hong Kong", "secondary": ["Korea", "China"]},
    "matt-caruso":    {"primary": "India", "secondary": ["Brazil", "US"]},
    "brian-shannon":  {"primary": "US", "secondary": ["Japan", "UK"]},
    "dan-zanger":     {"primary": "US", "secondary": ["Korea", "Hong Kong"]},
    "nick-schmidt":   {"primary": "Brazil", "secondary": ["India", "Korea"]},
}

# Exchange suffix mapping for global stock access via yfinance
EXCHANGE_SUFFIXES = {
    "UK": ".L", "Japan": ".T", "Korea": ".KS", "India": ".NS",
    "Brazil": ".SA", "Hong Kong": ".HK", "Canada": ".TO",
    "Singapore": ".SI", "Australia": ".AX",
}

ETF_KEYWORDS = [
    "ETF", "ETN", "ETP", "Leverage Shares", "ProShares", "Direxion",
    "MicroSectors", "UltraShort", "UltraPro", "Ultra", "Bull ",
    "Bear ", "Short ", "Inverse ", "2X", "3X",
]

PROFILES_DIR = os.path.expanduser("~/.hermes/profiles")

# Rate limiting for yfinance
_yf_lock = threading.Lock()

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def _load_env():
    """Load API keys from ~/.hermes/.env"""
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


# ─── MARKET CONTEXT (from existing competition engine) ──────────────────────

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
    """Fetch market context: indices, sector ETFs, and global gainers via TV scanner."""
    context = {
        "date": DATE_STR,
        "indices": {}, "sectors": {}, "gainers": {},
    }

    print("[Engine]  Scanning global indices via TradingView...")
    index_tickers = {
        "SPY": "S&P 500", "QQQ": "Nasdaq 100", "DIA": "Dow Jones",
        "IWM": "Russell 2000", "GLD": "Gold", "TLT": "Long Bond",
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

    print("[Engine]  Scanning global markets for gainers...")
    for label, region in TV_MARKETS.items():
        stocks = fetch_tv_gainers(label, region, 15)
        context["gainers"][label] = stocks
        ticker_list = ", ".join(s["ticker"] for s in stocks[:5])
        print(f"[Engine]    {label}: {len(stocks)} gainers ({ticker_list})")

    return context


# ─── SOUL.MD ────────────────────────────────────────────────────────────────

def read_full_soul(persona):
    """Read COMPLETE SOUL.md — no truncation."""
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


# ─── DEEPSEEK CLIENT ────────────────────────────────────────────────────────

def get_deepseek_client():
    """Create OpenAI-compatible client for DeepSeek."""
    env = _load_env()
    api_key = (env.get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "")
    base_url = (env.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "https://api.deepseek.com/v1")
    if not api_key:
        print("[Engine]  WARNING: DEEPSEEK_API_KEY not found")
    return OpenAI(api_key=api_key or "missing", base_url=base_url)


def query_deepseek(client, persona, system_content, user_prompt, model="deepseek-v4-flash", max_tokens=8192, temperature=0.7):
    """Generic DeepSeek query with error handling."""
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=300,
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


# ─── PHASE 1: GENERATE WATCHLIST ────────────────────────────────────────────

def build_market_context_str(context):
    """Build a readable market context string for prompts."""
    lines = [f"# GLOBAL MARKET CONTEXT", f"Date: {context['date']}", ""]

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


def generate_watchlist(client, persona, soul_content, context_str):
    """
    Phase 1: Use DeepSeek v4 Flash to generate a structured watchlist.
    Returns (watchlist_tickers, raw_response).
    """
    system_prompt = (
        f"You are {persona}. "
        f"This is your COMPLETE identity, methodology, voice, and quote database. "
        f"Adopt this identity EXACTLY. Never break character.\n\n"
        f"{soul_content}"
    )

    user_prompt = f"""GLOBAL MARKET CONTEXT ({DATE_STR}):

{context_str}

YOUR TASK:
Based on the market data above, generate your watchlist for today. This is a RESEARCH ENGINE that needs stocks to analyze.

CRITICAL INSTRUCTIONS:
1. Use YOUR methodology (CANSLIM, value investing, PEG, VCP, etc.) to identify stocks
2. INCLUDE AT LEAST 10-15 STOCKS in your watchlist. The more stocks you list, the more research data I can collect.
3. Focus on INDIVIDUAL STOCKS first (not just ETFs). Include some ETFs if relevant, but emphasize specific companies.
4. ONLY include tickers that trade on major exchanges
5. Focus on liquid, institutional-quality stocks
6. YOUR PRIMARY MARKET: {PERSONA_MARKET_ROTATION.get(persona, {}).get('primary', 'US')}
   YOUR SECONDARY MARKETS: {', '.join(PERSONA_MARKET_ROTATION.get(persona, {}).get('secondary', ['global']))}
   RULE: At LEAST 50% of picks must come from your primary market. Rest from secondary markets.
   RULE: If primary is NOT US, you must NOT pick more than 20% US stocks.
7. The minimum is 10 stocks — this is a research engine and needs data to work with.
8. FORCE DIFFERENTIATION: Do NOT pick meme stocks or obvious names. Find unique opportunities in YOUR markets that other personas will miss.

You MUST respond with a valid JSON block at the end of your response in this EXACT format:

```json
{{
  "watchlist": ["TICKER1", "TICKER2", "TICKER3"],
  "reasoning": "Brief explanation of your selection strategy"
}}
```

The JSON block is MANDATORY. The engine needs to parse it to look up real data.

Before the JSON, write 1-3 paragraphs of market assessment in your natural voice.
"""
    print(f"[Engine]  Phase 1: Generating watchlist for {persona}...")
    raw = query_deepseek(client, persona, system_prompt, user_prompt, model="deepseek-v4-flash", max_tokens=4096, temperature=0.8)

    if raw.startswith("ERROR:"):
        print(f"[Engine]  ✗ {persona}: Phase 1 failed — {raw}")
        return [], raw

    # Debug: show raw response
    print(f"[Engine]  Phase 1 raw response ({len(raw)} chars)")

    # Parse JSON block from response
    tickers = []
    # Strategy 1: Look for fenced JSON block (```json ... ```)
    json_match = re.search(r'```(?:json)?\s*(\{[^`]+?\})\s*```', raw, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            tickers = data.get("watchlist", [])
            print(f"[Engine]  Phase 1: {persona} watchlist — {len(tickers)} tickers: {', '.join(tickers[:15])}{'...' if len(tickers) > 15 else ''}")
        except json.JSONDecodeError as e:
            print(f"[Engine]  Phase 1: JSON parse error for {persona}: {e}")
            # Try to find any tickers using regex fallback
            tickers = re.findall(r'\b[A-Z]{1,5}\b', raw)
            # Filter out common non-ticker words
            non_tickers = {"THE", "THIS", "THAT", "WITH", "FROM", "HAVE", "WILL", "WOULD", "COULD",
                          "SHOULD", "THERE", "THEIR", "WHICH", "BEEN", "BEING", "AFTER", "BEFORE",
                          "ABOVE", "BELOW", "BETWEEN", "THROUGH", "DURING", "WITHOUT", "ABOUT",
                          "AGAIN", "FURTHER", "THEN", "ONCE", "HERE", "THERE", "WHEN", "WHERE",
                          "WHY", "HOW", "ALL", "EACH", "EVERY", "BOTH", "FEW", "MORE", "MOST",
                          "OTHER", "SOME", "SUCH", "NO", "NOR", "NOT", "ONLY", "OWN", "SAME",
                          "SO", "THAN", "TOO", "VERY", "JUST", "ALSO", "VERY", "WELL", "EVEN",
                          "STILL", "ALREADY", "MUCH", "STILL", "ALSO", "INTO", "OVER", "ALSO",
                          "P/E", "EPS", "RSI", "RS", "ROE", "PEG", "MA", "SMA", "EMA", "AVG",
                          "VOL", "HIGH", "LOW", "NEW", "OLD", "TOP", "BIG", "KEY", "UP", "DOWN",
                          "INC", "LTD", "CORP", "NYSE", "NASDAQ", "AMEX", "IPO", "ATH", "YTD",
                          "CEO", "CFO", "ROI", "YES", "NOW", "GET", "SET", "PUT", "MAY", "CAN",
                          "SPY", "QQQ", "DIA", "IWM", "GLD", "TLT",
                          "XLK", "XLF", "XLV", "XLI", "XLE", "XLP", "XLY", "XLB", "XLU", "XLRE",
                          "SMH", "IBB", "ARKK"}
            tickers = [t for t in tickers if t not in non_tickers and len(t) >= 1]
            tickers = list(dict.fromkeys(tickers))  # dedup preserving order
            print(f"[Engine]  Phase 1: Regex fallback — {len(tickers)} tickers")
    # Strategy 2: No fences — try to parse the whole response as JSON
    if not tickers:
        try:
            data = json.loads(raw)
            tickers = data.get("watchlist", [])
            print(f"[Engine]  Phase 1: Parsed raw response as JSON — {len(tickers)} tickers")
        except (json.JSONDecodeError, TypeError):
            pass

    # Strategy 3: Regex fallback — find any JSON object with "watchlist"
    if not tickers:
        # Balanced braces search for JSON with "watchlist"
        brace_depth = 0
        start_pos = -1
        for i, ch in enumerate(raw):
            if ch == '{':
                if brace_depth == 0:
                    start_pos = i
                brace_depth += 1
            elif ch == '}':
                brace_depth -= 1
                if brace_depth == 0 and start_pos >= 0:
                    candidate = raw[start_pos:i+1]
                    if '"watchlist"' in candidate:
                        try:
                            data = json.loads(candidate)
                            tickers = data.get("watchlist", [])
                            print(f"[Engine]  Phase 1: Balanced-brace JSON parse — {len(tickers)} tickers")
                            break
                        except json.JSONDecodeError:
                            pass
                    start_pos = -1

    if not tickers:
        print(f"[Engine]  Phase 1: No JSON watchlist found for {persona}")

    # Clean tickers
    cleaned = []
    for t in tickers:
        t = t.strip().upper().replace("$", "").replace("(", "").replace(")", "")
        if re.match(r'^[A-Z]{1,5}$', t) and len(t) >= 1:
            cleaned.append(t)
    cleaned = list(dict.fromkeys(cleaned))
    print(f"[Engine]  Phase 1: {persona} final watchlist — {len(cleaned)} clean tickers")
    return cleaned[:100], raw  # cap at 100


# ─── PHASE 2: RESEARCH STOCKS ──────────────────────────────────────────────

def format_value(val, fmt="{:.2f}"):
    """Format a value safely, returning 'N/A' if None or NaN."""
    if val is None:
        return "N/A"
    try:
        if val != val:  # NaN check
            return "N/A"
        return fmt.format(val)
    except (ValueError, TypeError):
        return "N/A"


def format_market_cap(mc):
    """Format market cap in billions or trillions."""
    if mc is None:
        return "N/A"
    try:
        if mc >= 1_000_000_000_000:
            return f"${mc/1_000_000_000_000:.2f}T"
        elif mc >= 1_000_000_000:
            return f"${mc/1_000_000_000:.2f}B"
        elif mc >= 1_000_000:
            return f"${mc/1_000_000:.2f}M"
        else:
            return f"${mc:,.0f}"
    except (ValueError, TypeError):
        return "N/A"


def format_volume(vol):
    """Format volume in millions."""
    if vol is None:
        return "N/A"
    try:
        return f"{vol/1_000_000:.2f}M"
    except (ValueError, TypeError):
        return "N/A"


def compute_technical_indicators(hist, ticker="?"):
    """Compute REAL technical indicators from yfinance OHLCV data.
    
    Returns dict with RSI(14), MACD(12,26,9), SMA(20/50/200), VWAP, ATR(14),
    and last 10 daily OHLCV rows for the LLM to reference.
    Numbers only — no hallucination possible since they're COMPUTED.
    """
    indicators = {}
    if hist is None or hist.empty or len(hist) < 15:
        return indicators
    
    close = hist['Close'].dropna()
    high = hist['High'].dropna()
    low = hist['Low'].dropna()
    volume = hist['Volume'].dropna() if 'Volume' in hist.columns else None
    
    # --- RSI(14) ---
    try:
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta.where(delta < 0, 0.0))
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = (avg_gain / avg_loss.replace(0, float('nan')))
        rsi = 100 - (100 / (1 + rs))
        indicators['rsi_14'] = round(float(rsi.iloc[-1]), 1) if len(rsi) > 0 else None
        indicators['rsi_14_prev'] = round(float(rsi.iloc[-2]), 1) if len(rsi) > 1 else None
    except Exception:
        indicators['rsi_14'] = None
    
    # --- MACD(12,26,9) ---
    try:
        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd_line = ema12 - ema26
        signal = macd_line.ewm(span=9).mean()
        histogram = macd_line - signal
        indicators['macd'] = round(float(macd_line.iloc[-1]), 3) if len(macd_line) > 0 else None
        indicators['macd_signal'] = round(float(signal.iloc[-1]), 3) if len(signal) > 0 else None
        indicators['macd_histogram'] = round(float(histogram.iloc[-1]), 3) if len(histogram) > 0 else None
    except Exception:
        indicators['macd'] = None
    
    # --- SMA(20), SMA(50), SMA(200) ---
    try:
        sma20 = close.rolling(20).mean()
        sma50 = close.rolling(50).mean()
        indicators['sma_20'] = round(float(sma20.iloc[-1]), 2) if len(sma20) > 0 else None
        indicators['price_vs_sma20_pct'] = round(float((close.iloc[-1] / sma20.iloc[-1] - 1) * 100), 2) if (len(sma20) > 0 and sma20.iloc[-1] > 0) else None
        if len(close) >= 50:
            sma200 = close.rolling(200).mean()
            indicators['sma_50'] = round(float(sma50.iloc[-1]), 2)
            indicators['sma_200'] = round(float(sma200.iloc[-1]), 2) if len(sma200) > 0 else None
            indicators['price_vs_sma200_pct'] = round(float((close.iloc[-1] / sma200.iloc[-1] - 1) * 100), 2) if (sma200.iloc[-1] > 0) else None
        else:
            indicators['sma_50'] = round(float(sma50.iloc[-1]), 2) if len(sma50) > 0 else None
    except Exception:
        pass
    
    # --- VWAP (cumulative) ---
    try:
        if volume is not None:
            vwap_series = (close * volume).cumsum() / volume.cumsum()
            indicators['vwap'] = round(float(vwap_series.iloc[-1]), 2) if len(vwap_series) > 0 else None
            # Price vs VWAP
            iv = close.iloc[-1]
            vw = vwap_series.iloc[-1]
            if vw and iv:
                indicators['price_vs_vwap_pct'] = round(float((iv / vw - 1) * 100), 2)
    except Exception:
        indicators['vwap'] = None
    
    # --- ATR(14) ---
    try:
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        indicators['atr_14'] = round(float(atr.iloc[-1]), 2) if len(atr) > 0 else None
        if indicators.get('atr_14') and close.iloc[-1]:
            indicators['atr_pct'] = round(float(indicators['atr_14'] / close.iloc[-1] * 100), 2)
    except Exception:
        indicators['atr_14'] = None
    
    # --- Last 10 daily candles (OHLCV) ---
    try:
        last_n = hist.tail(10)
        candles = []
        for idx, row in last_n.iterrows():
            dt = idx.strftime('%Y-%m-%d') if hasattr(idx, 'strftime') else str(idx)
            o = round(float(row.get('Open', 0)), 2)
            h = round(float(row.get('High', 0)), 2)
            l = round(float(row.get('Low', 0)), 2)
            c = round(float(row.get('Close', 0)), 2)
            v = int(row.get('Volume', 0)) if 'Volume' in last_n.columns else 0
            body_pct = round(abs(c - o) / o * 100, 2) if o > 0 else 0
            direction = "UP" if c > o else "DOWN"
            candles.append(f"{dt} O:{o} H:{h} L:{l} C:{c} V:{v} {direction} body:{body_pct}%")
        indicators['last_10_candles'] = "\n".join(candles)
    except Exception:
        indicators['last_10_candles'] = "N/A"
    
    return indicators


def fetch_stock_research(ticker):
    """
    Phase 2a: Fetch fundamental and price data from yfinance for one ticker.
    Returns a dict with all available data.
    """
    result = {"ticker": ticker, "error": None}
    try:
        with _yf_lock:
            stock = yf.Ticker(ticker)
            info = stock.info

        result["price"] = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
        result["market_cap"] = info.get("marketCap")
        result["pe_ratio"] = info.get("trailingPE")
        result["forward_pe"] = info.get("forwardPE")
        result["eps"] = info.get("trailingEps")
        result["forward_eps"] = info.get("forwardEps")
        result["dividend_yield"] = info.get("dividendYield")
        result["payout_ratio"] = info.get("payoutRatio")
        result["high_52w"] = info.get("fiftyTwoWeekHigh")
        result["low_52w"] = info.get("fiftyTwoWeekLow")
        result["avg_vol"] = info.get("averageVolume")
        result["volume"] = info.get("volume")
        result["avg_vol_10d"] = info.get("averageDailyVolume10Day")
        result["sector"] = info.get("sector")
        result["industry"] = info.get("industry")
        result["name"] = info.get("longName") or info.get("shortName") or ticker
        result["short_ratio"] = info.get("shortRatio")
        result["short_pct"] = info.get("shortPercentOfFloat")
        result["revenue_growth"] = info.get("revenueGrowth")
        result["profit_margins"] = info.get("profitMargins")
        result["roe"] = info.get("returnOnEquity")
        result["roa"] = info.get("returnOnAssets")
        result["debt_to_equity"] = info.get("debtToEquity")
        result["free_cashflow"] = info.get("freeCashflow")
        result["earnings_growth"] = info.get("earningsGrowth")
        result["revenue_per_share"] = info.get("revenuePerShare")
        result["book_value"] = info.get("bookValue")
        result["price_to_book"] = info.get("priceToBook")
        result["beta"] = info.get("beta")
        result["fifty_day_ma"] = info.get("fiftyDayAverage")
        result["two_hundred_day_ma"] = info.get("twoHundredDayAverage")
        result["target_mean"] = info.get("targetMeanPrice")
        result["target_high"] = info.get("targetHighPrice")
        result["target_low"] = info.get("targetLowPrice")
        result["recommendation"] = info.get("recommendationKey")
        result["number_of_analysts"] = info.get("numberOfAnalystOpinions")
        result["institutional_holders"] = info.get("heldPercentInstitutions")
        result["insider_holders"] = info.get("heldPercentInsiders")

        # Try to get recent price history for REAL indicator computation
        try:
            hist = stock.history(period="6mo")
            if not hist.empty and "Volume" in hist.columns:
                recent_vols = hist["Volume"].dropna()
                if len(recent_vols) > 1:
                    result["volume_vs_avg"] = float(recent_vols.iloc[-1] / recent_vols.mean())
                # Compute REAL technical indicators from OHLCV data
                indicators = compute_technical_indicators(hist, ticker)
                result["indicators"] = indicators
                if indicators.get("rsi_14"):
                    print(f"[Engine]    {ticker}: RSI(14)={indicators['rsi_14']} | MACD={indicators.get('macd','N/A')} | VWAP={indicators.get('vwap','N/A')} | ATR={indicators.get('atr_14','N/A')}")
        except Exception:
            pass

        if result["price"]:
            print(f"[Engine]    {ticker}: ${format_value(result['price'])} | "
                  f"{format_market_cap(result['market_cap'])} | "
                  f"PE={format_value(result['pe_ratio'])} | "
                  f"EPS=${format_value(result['eps'])}")
        else:
            # Try with different suffix for foreign stocks
            for suffix in [".NS", ".L", ".T", ".KS", ".HK", ".TO", ".SA"]:
                try:
                    alt_ticker = ticker + suffix
                    with _yf_lock:
                        alt_stock = yf.Ticker(alt_ticker)
                        alt_info = alt_stock.info
                    alt_price = alt_info.get("currentPrice") or alt_info.get("regularMarketPrice") or alt_info.get("previousClose")
                    if alt_price:
                        result["alt_ticker"] = alt_ticker
                        result["price"] = alt_price
                        result["market_cap"] = alt_info.get("marketCap")
                        result["pe_ratio"] = alt_info.get("trailingPE")
                        result["eps"] = alt_info.get("trailingEps")
                        result["high_52w"] = alt_info.get("fiftyTwoWeekHigh")
                        result["low_52w"] = alt_info.get("fiftyTwoWeekLow")
                        result["avg_vol"] = alt_info.get("averageVolume")
                        result["volume"] = alt_info.get("volume")
                        result["name"] = alt_info.get("longName") or alt_info.get("shortName") or ticker
                        result["sector"] = alt_info.get("sector")
                        print(f"[Engine]    {ticker} → {alt_ticker}: ${result['price']}")
                        break
                except Exception:
                    continue

    except Exception as e:
        result["error"] = str(e)
        print(f"[Engine]    {ticker}: yfinance error — {e}")

    return result


def search_stock_news(ticker, company_name=None, max_articles=5):
    """
    Phase 2b: Search for recent news using Yahoo Finance RSS and Google News RSS.
    Returns a list of dicts with title, source, date, snippet.
    """
    articles = []
    seen_titles = set()

    # Source 1: Yahoo Finance RSS
    yahoo_urls = [
        f"https://finance.yahoo.com/rss/headline?s={ticker}",
    ]
    for url in yahoo_urls:
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
            resp = requests.get(url, headers=headers, timeout=8)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, 'xml')
                for item in soup.select('item'):
                    title = item.find('title')
                    if not title or not title.text.strip():
                        continue
                    t = title.text.strip()
                    if t in seen_titles:
                        continue
                    seen_titles.add(t)
                    link = item.find('link')
                    pubdate = item.find('pubDate')
                    desc = item.find('description')
                    articles.append({
                        "title": t,
                        "source": "Yahoo Finance",
                        "date": pubdate.text.strip() if pubdate else "",
                        "snippet": desc.text.strip()[:200] if desc else "",
                        "link": link.text.strip() if link else "",
                    })
                    if len(articles) >= max_articles:
                        break
        except Exception:
            pass

    # Source 2: Google News RSS
    if len(articles) < max_articles:
        query = f"{ticker} stock"
        if company_name:
            query = f"{ticker} {company_name} stock"
        url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-US&gl=US&ceid=US:en"
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
            resp = requests.get(url, headers=headers, timeout=8)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, 'xml')
                for item in soup.select('item'):
                    title = item.find('title')
                    if not title or not title.text.strip():
                        continue
                    t = title.text.strip()
                    if t in seen_titles:
                        continue
                    seen_titles.add(t)
                    link = item.find('link')
                    source = item.find('source')
                    pubdate = item.find('pubDate')
                    articles.append({
                        "title": t,
                        "source": source.text.strip() if source else "Google News",
                        "date": pubdate.text.strip() if pubdate else "",
                        "snippet": "",
                        "link": link.text.strip() if link else "",
                    })
                    if len(articles) >= max_articles:
                        break
        except Exception:
            pass

    return articles


def research_watchlist(tickers):
    """
    Phase 2: Research ALL stocks in the watchlist in parallel.
    Returns dict of {ticker: research_data}.
    """
    print(f"[Engine]  Phase 2: Researching {len(tickers)} stocks...")

    # Fetch yfinance data (sequential with rate limiting)
    research_data = {}
    for ticker in tickers:
        stock_data = fetch_stock_research(ticker)
        research_data[ticker] = stock_data
        time.sleep(0.25)  # Rate limit for yfinance

    # Fetch news in parallel
    print(f"[Engine]  Phase 2: Fetching news for {len(tickers)} stocks...")
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {}
        for ticker in tickers:
            name = research_data[ticker].get("name")
            future = executor.submit(search_stock_news, ticker, name, 5)
            futures[future] = ticker

        for future in as_completed(futures):
            ticker = futures[future]
            try:
                news = future.result()
                research_data[ticker]["news"] = news
                if news:
                    print(f"[Engine]    {ticker}: {len(news)} news articles")
                else:
                    print(f"[Engine]    {ticker}: no news found")
            except Exception as e:
                print(f"[Engine]    {ticker}: news error — {e}")
                research_data[ticker]["news"] = []

    return research_data


def build_research_summary(research_data, max_news=3):
    """
    Build a compact text summary of all research data for feeding into the LLM.
    """
    lines = []
    for ticker, data in research_data.items():
        if data.get("error"):
            lines.append(f"\n### {ticker} — DATA UNAVAILABLE: {data['error']}")
            continue

        lines.append(f"\n### {ticker} — {data.get('name', 'N/A')}")
        lines.append(f"**Price:** ${format_value(data.get('price'))} | "
                     f"**Market Cap:** {format_market_cap(data.get('market_cap'))} | "
                     f"**PE:** {format_value(data.get('pe_ratio'))} | "
                     f"**EPS:** ${format_value(data.get('eps'))}")

        lines.append(f"**Sector:** {data.get('sector', 'N/A')} | "
                     f"**Industry:** {data.get('industry', 'N/A')}")

        lines.append(f"**52w Range:** ${format_value(data.get('low_52w'))} — ${format_value(data.get('high_52w'))} | "
                     f"**Avg Vol:** {format_volume(data.get('avg_vol'))} | "
                     f"**Today Vol:** {format_volume(data.get('volume'))}")

        if data.get("volume_vs_avg") is not None:
            lines.append(f"**Volume vs Avg:** {data['volume_vs_avg']:.2f}x")

        lines.append(f"**Forward PE:** {format_value(data.get('forward_pe'))} | "
                     f"**Forward EPS:** ${format_value(data.get('forward_eps'))}")

        lines.append(f"**Dividend Yield:** {format_value(data.get('dividend_yield'), '{:.4f}') if data.get('dividend_yield') else 'None'} | "
                     f"**Payout Ratio:** {format_value(data.get('payout_ratio'), '{:.2%}') if data.get('payout_ratio') else 'N/A'}")

        lines.append(f"**50-day MA:** ${format_value(data.get('fifty_day_ma'))} | "
                     f"**200-day MA:** ${format_value(data.get('two_hundred_day_ma'))}")

        lines.append(f"**Revenue Growth:** {format_value(data.get('revenue_growth'), '{:.1%}') if data.get('revenue_growth') else 'N/A'} | "
                     f"**Earnings Growth:** {format_value(data.get('earnings_growth'), '{:.1%}') if data.get('earnings_growth') else 'N/A'}")

        lines.append(f"**ROE:** {format_value(data.get('roe'), '{:.1%}') if data.get('roe') else 'N/A'} | "
                     f"**Profit Margin:** {format_value(data.get('profit_margins'), '{:.1%}') if data.get('profit_margins') else 'N/A'}")

        lines.append(f"**Free Cash Flow:** {format_market_cap(data.get('free_cashflow')) if data.get('free_cashflow') else 'N/A'} | "
                     f"**Debt/Equity:** {format_value(data.get('debt_to_equity'))}")

        lines.append(f"**Beta:** {format_value(data.get('beta'))} | "
                     f"**Book Value:** ${format_value(data.get('book_value'))} | "
                     f"**P/B:** {format_value(data.get('price_to_book'))}")

        if data.get("short_pct") is not None:
            lines.append(f"**Short % of Float:** {data['short_pct']:.2%}")

        lines.append(f"**Analyst Target:** ${format_value(data.get('target_mean'))} "
                     f"(Low: ${format_value(data.get('target_low'))}, "
                     f"High: ${format_value(data.get('target_high'))}) | "
                     f"**Rating:** {data.get('recommendation', 'N/A')}")

        lines.append(f"**Insider Ownership:** {format_value(data.get('institutional_holders'), '{:.1%}') if data.get('institutional_holders') else 'N/A'} | "
                     f"**Insider Ownership:** {format_value(data.get('insider_holders'), '{:.1%}') if data.get('insider_holders') else 'N/A'}")

        # -- REAL COMPUTED TECHNICAL INDICATORS --
        ind = data.get("indicators", {})
        if ind:
            lines.append("")
            rsi_str = f"RSI(14): {ind.get('rsi_14', 'N/A')}"
            if ind.get('rsi_14_prev'):
                arrow = "▲" if ind.get('rsi_14', 0) > ind.get('rsi_14_prev', 0) else "▼"
                rsi_str += f" ({arrow} from {ind['rsi_14_prev']})"
            macd_str = f"MACD: {ind.get('macd', 'N/A')} / Sig: {ind.get('macd_signal', 'N/A')} / Hist: {ind.get('macd_histogram', 'N/A')}"
            ma_str = f"SMA20: ${format_value(ind.get('sma_20'))}"
            if ind.get('price_vs_sma20_pct') is not None:
                ma_str += f" ({ind['price_vs_sma20_pct']:+.1f}%)"
            if ind.get('sma_50') is not None:
                ma_str += f" | SMA50: ${format_value(ind.get('sma_50'))}"
            if ind.get('sma_200') is not None:
                ma_str += f" | SMA200: ${format_value(ind.get('sma_200'))} ({ind.get('price_vs_sma200_pct', 0):+.1f}%)"
            vwap_str = f"VWAP: ${format_value(ind.get('vwap'))}"
            if ind.get('price_vs_vwap_pct') is not None:
                vwap_str += f" (price {ind['price_vs_vwap_pct']:+.1f}% vs VWAP)"
            atr_str = f"ATR(14): ${format_value(ind.get('atr_14'))}"
            if ind.get('atr_pct') is not None:
                atr_str += f" ({ind['atr_pct']:.1f}% of price)"
                lines.append(f"**Technical Indicators (COMPUTED from REAL OHLCV data):**")
                lines.append(f"  {rsi_str}")
                lines.append(f"  {macd_str}")
                lines.append(f"  {ma_str}")
                lines.append(f"  {vwap_str}")
                lines.append(f"  {atr_str}")
            candles = ind.get('last_10_candles', '')
            if candles and candles != 'N/A':
                lines.append(f"\n**Last 10 Daily Candles:**")
                for c in candles.split('\n'):
                    lines.append(f"  {c}")
        
        # News
            news = data.get("news", [])
            if news:
                lines.append(f"\n**Recent News:**")
            for article in news[:max_news]:
                date_str = article.get("date", "")[:16] if article.get("date", "") else ""
                source = article.get("source", "")
                title = article.get("title", "")
                snippet = article.get("snippet", "")
                if snippet:
                    lines.append(f"  • [{source}] {title} ({date_str}) — {snippet[:150]}")
                else:
                    lines.append(f"  • [{source}] {title} ({date_str})")
        else:
            lines.append(f"\n**Recent News:** None found")

    return "\n".join(lines)


# ─── PHASE 3: GENERATE FINAL ANALYSIS ──────────────────────────────────────

def generate_final_analysis(client, persona, soul_content, context_str, research_summary, raw_watchlist):
    """
    Phase 3: Feed REAL research data + SOUL.md to DeepSeek for final analysis.
    Returns the full analysis text.
    """
    system_prompt = (
        f"You are {persona}. "
        f"This is your COMPLETE identity, methodology, voice, and quote database. "
        f"Adopt this identity EXACTLY. Never break character. "
        f"Never speak as a generic analyst. Your quotes, your methodology, your voice.\n\n"
        f"{soul_content}"
    )

    user_prompt = f"""You are writing your weekly competition entry for {DATE_STR}.

## GLOBAL MARKET CONTEXT
{context_str}

## THE RESEARCH — REAL DATA FROM YAHOO FINANCE & NEWS
Below is the ACTUAL research data for every stock on your watchlist. These are REAL prices, REAL fundamentals, REAL news. Every number in here is from the market, not generated.

{research_summary}

---

## YOUR TASK

Write a COMPLETE analysis for your weekly competition entry. This analysis will be saved to an Obsidian vault and read by your peers.

### STRUCTURE REQUIREMENTS:

1. **MARKET SENTIMENT ASSESSMENT** (1500+ words)
   - Analyze each major market individually
   - Evaluate sector rotation
   - Identify the current market phase using YOUR specific methodology
   - Reference YOUR books, interviews, and past trades
   - Quote YOURSELF verbatim from your quote database

2. **WATCHLIST ANALYSIS** (400+ words PER stock)
   For EACH stock in your watchlist, write a dedicated section:

   ### PICK: TICKER (Market)
   **Price:** $XX.XX | **Market Cap:** $XXB | **PE:** XX | **EPS:** $XX
   **52w Range:** $XX - $XX | **Avg Vol:** XXM | **Today Vol:** XXM
   **Recent News:** [summary from the news provided]

   Then 400+ words of deep analysis covering:
   - Pattern identification / setup type
   - Entry/exit levels based on REAL price data
   - Risk management
   - Why this fits YOUR specific methodology
   - A VERBATIM quoted passage from your books or interviews

3. **FINAL MARKET CALL** (500+ words)
   - Overall positioning
   - Cash level recommendation
   - Timeframe for the trade
   - Risk factors

### MANDATORY RULES:
- EVERY price/volume/fundamental must come from the research data — DO NOT INVENT ANY DATA
- ⚠️ NO PRICE TARGETS unless you compute them from the real data provided (e.g., fib extension from actual pivot low, AVWAP from real volume data). NEVER say "VWAP from October 2023 low" — you don't have that data.
- ⚠️ NEVER reference indicator levels you cannot see — if the research didn't give you RSI(14), MACD, or AVWAP data, do NOT invent them
- Each stock must have 400+ words of analysis with EXACT quotes from YOUR books/interviews
- ⚠️ EVERY quote must include a SOURCE URL (Amazon link, YouTube timestamp, book page number)
- Total analysis must be 5000+ words minimum
- Use YOUR EXACT voice and methodology — verbatim quotes, not paraphrasing
- ⚠️ If a stock doesn't have real price data, do NOT analyze it — skip it
- NO generic boilerplate. NO hallucinated numbers. NO fake indicator levels.

Return ONLY the analysis. Start with '## MARKET SENTIMENT ASSESSMENT'."""
    print(f"[Engine]  Phase 3: Generating final analysis for {persona}...")
    analysis = query_deepseek(client, persona, system_prompt, user_prompt, model="deepseek-v4-flash", max_tokens=16384, temperature=0.7)

    if analysis.startswith("ERROR:"):
        print(f"[Engine]  ✗ {persona}: Phase 3 failed — {analysis}")

    return analysis


# ─── PHASE 4: SAVE TO OBSIDIAN ──────────────────────────────────────────────

def save_to_obsidian(persona, content):
    """Save analysis to Obsidian vault."""
    subdir = "10_Trading/Competition"
    vault_dir = Path(OBSIDIAN_VAULT)
    output_dir = vault_dir / subdir
    output_dir.mkdir(parents=True, exist_ok=True)

    tags = "trading/competition"
    display_name = persona.replace("-", " ").title()

    frontmatter = f"""---
date: {DATE_STR}
type: deep-research-entry
persona: {persona}
persona_type: trading
tags: [{tags}]
week: {TODAY.isocalendar()[1]}
aliases:
  - "{persona} Deep Research"
  - "{persona} {DATE_STR}"
---

# {display_name} — Deep Research Entry
## Week of {DATE_STR}

"""

    full_content = frontmatter + content
    filename = f"{persona} - {DATE_STR}.md"
    filepath = output_dir / filename

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(full_content)
        print(f"[Engine]  ✓ Saved to Obsidian: {filepath}")
        return str(filepath), True
    except Exception as e:
        print(f"[Engine]  ✗ ERROR saving to Obsidian: {e}")
        return str(filepath), False


def save_terminal_log(persona, content):
    """Save a log copy."""
    log_dir = Path(os.path.expanduser(f"~/.hermes/logs/deep_research/{DATE_STR}"))
    log_dir.mkdir(parents=True, exist_ok=True)
    filename = log_dir / f"{persona}.md"
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        pass


# ─── PERSONA PROCESSOR ──────────────────────────────────────────────────────

def process_persona_full(client, persona, context):
    """
    Complete pipeline for one persona:
    Phase 1 → Phase 2 → Phase 3 → Phase 4
    """
    print(f"\n{'='*60}")
    print(f"[Engine]  ▶ PROCESSING: {persona}")
    print(f"{'='*60}")

    # Read SOUL.md
    soul_content, err = read_full_soul(persona)
    if err or not soul_content.strip():
        print(f"[Engine]  ✗ {persona}: SOUL.md error: {err}")
        return {"persona": persona, "status": "NO_SOUL", "word_count": 0, "picks_count": 0}

    # Build market context string
    context_str = build_market_context_str(context)

    # ─── PHASE 1: Generate watchlist ───
    print(f"\n[Engine]  ── PHASE 1: Watchlist Generation ──")
    watchlist_tickers, raw_watchlist = generate_watchlist(client, persona, soul_content, context_str)
    if not watchlist_tickers:
        print(f"[Engine]  ✗ {persona}: No watchlist generated by AI")
        # Fallback: try persona's PRIMARY market first, then secondary markets
        print(f"[Engine]    Falling back to persona's primary market...")
        fallback_tickers = []
        persona_mkt = PERSONA_MARKET_ROTATION.get(persona, {}).get('primary', 'US')
        fallback_regions = [persona_mkt] + PERSONA_MARKET_ROTATION.get(persona, {}).get('secondary', [])
        for region in fallback_regions:
            gainers = context.get("gainers", {}).get(region, [])
            for s in gainers:
                t = s.get("ticker", "")
                if re.match(r'^[A-Z\.]{1,7}$', t) and t not in ("SPY", "QQQ", "IWM", "DIA", "GLD", "TLT",
                            "XLK", "XLF", "XLV", "XLI", "XLE", "XLP", "XLY", "XLB", "XLU", "XLRE",
                            "SMH", "IBB", "ARKK"):
                    if t not in fallback_tickers:
                        fallback_tickers.append(t)
                if len(fallback_tickers) >= 12:
                    break
            if len(fallback_tickers) >= 12:
                break
        watchlist_tickers = fallback_tickers
        print(f"[Engine]    Fallback watchlist ({len(watchlist_tickers)}): {', '.join(watchlist_tickers[:10])}")

    # ─── PHASE 2: Research each stock ───
    print(f"\n[Engine]  ── PHASE 2: Research ({len(watchlist_tickers)} stocks) ──")
    research_data = research_watchlist(watchlist_tickers)
    research_summary = build_research_summary(research_data)

    # Count successfully researched stocks
    successful_stocks = sum(1 for t, d in research_data.items() if d.get("price") is not None and d.get("error") is None)
    print(f"[Engine]  Phase 2: {successful_stocks}/{len(watchlist_tickers)} stocks researched successfully")

    if successful_stocks == 0:
        print(f"[Engine]  ✗ {persona}: No stocks could be researched")
        return {"persona": persona, "status": "NO_DATA", "word_count": 0, "picks_count": 0}

    # ─── PHASE 3: Generate final analysis ───
    print(f"\n[Engine]  ── PHASE 3: Final Analysis Generation ──")
    analysis = generate_final_analysis(client, persona, soul_content, context_str, research_summary, raw_watchlist)

    if analysis.startswith("ERROR:"):
        print(f"[Engine]  ✗ {persona}: Phase 3 failed")
        return {"persona": persona, "status": "ANALYSIS_FAILED", "word_count": 0, "picks_count": 0}

    word_count = len(analysis.split())
    print(f"[Engine]  ✓ {persona}: {word_count} words generated")

    # ─── PHASE 4: Save to Obsidian ───
    print(f"\n[Engine]  ── PHASE 4: Saving ──")
    filepath, saved = save_to_obsidian(persona, analysis)
    save_terminal_log(persona, analysis)

    # Print summary to terminal
    print(f"\n─── {persona.upper()} ANALYSIS ───")
    print(f"Generated {word_count} words across {successful_stocks} researched stocks")
    print(f"\nFirst 2000 chars:")
    print(analysis[:2000])
    if len(analysis) > 2000:
        print(f"\n... ({len(analysis)-2000} more chars) ...")
    print(f"\n─── END {persona.upper()} ───\n")

    return {
        "persona": persona,
        "status": "OK" if saved else "SAVE_FAILED",
        "word_count": word_count,
        "picks_count": successful_stocks,
        "filepath": filepath,
        "total_tickers": len(watchlist_tickers),
    }


# ─── MAIN ────────────────────────────────────────────────────────────────────

def run(personas=None):
    """
    Main entry point. If personas is provided, only process those.
    Otherwise process all trading personas.
    """
    if personas:
        print(f"\n{'='*70}")
        print(f"  DEEP RESEARCH ENGINE v1 — {DATE_STR}")
        print(f"  Targeted: {', '.join(personas)}")
        print(f"{'='*70}")
    else:
        personas = TRADING_PERSONAS
        print(f"\n{'='*70}")
        print(f"  DEEP RESEARCH ENGINE v1 — {DATE_STR}")
        print(f"  All Trading Personas: {', '.join(personas)}")
        print(f"{'='*70}")

    print(f"[Engine]  Obsidian Vault: {OBSIDIAN_VAULT}")
    print()

    # 1. Fetch market context
    print("[Engine]  Stage 1: Fetching market context...")
    context = fetch_market_context()
    print(f"[Engine]  Market context: {len(context['indices'])} indices, "
          f"{len(context['sectors'])} sectors, "
          f"{len(context['gainers'])} market gainer sets")

    # Init accuracy DB
    if _HAS_ACCURACY:
        init_accuracy_db()

    # 2. Get DeepSeek client
    client = get_deepseek_client()

    # 3. Process personas in parallel with ThreadPoolExecutor
    print(f"[Engine]  Processing {len(personas)} personas in parallel (max_workers=4)...\n")
    all_results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(process_persona_full, client, p, context): p for p in personas}
        for future in as_completed(futures):
            persona_name = futures[future]
            try:
                result = future.result()
                all_results.append(result)
                sep = "=" * 50
                print(f"\n[Engine]  {sep}")
                print(f"[Engine]  RESULT: {result['persona']} -> {result['status']} "
                      f"({result['word_count']} words, {result.get('picks_count', 0)} picks)")
                print(f"[Engine]  {sep}\n")
            except Exception as e:
                print(f"\n[Engine]  ✗ {persona_name}: Exception in thread — {e}")
                all_results.append({"persona": persona_name, "status": "EXCEPTION", "word_count": 0, "picks_count": 0})
    # 4. Summary
    print(f"\n{'='*70}")
    print(f"  DEEP RESEARCH ENGINE v1 — SUMMARY")
    print(f"{'='*70}")
    print(f"  Date: {DATE_STR}")
    print()

    for r in all_results:
        icon = "✓" if r["status"] == "OK" else "✗"
        fp = r.get("filepath", "?")
        print(f"    {icon} {r['persona']:20s} {r['status']:12s} {r['word_count']:5d} words  ~{r['picks_count']} picks")
        if fp and r["status"] == "OK":
            print(f"       → {fp}")

    total_ok = sum(1 for r in all_results if r["status"] == "OK")
    total_words = sum(r["word_count"] for r in all_results)
    print()
    print(f"  RESULTS: {total_ok}/{len(all_results)} succeeded")
    print(f"  TOTAL:   {total_words:,} words of analysis generated")
    print(f"{'='*70}")
    print(f"[Engine]  Done.")
    print()


if __name__ == "__main__":
    # Parse args: if --persona=oneil is passed, only process that persona
    import argparse
    parser = argparse.ArgumentParser(description="Deep Research Engine v1")
    parser.add_argument("--persona", "-p", type=str, default=None,
                        help="Process only a specific persona (e.g., oneil)")
    parser.add_argument("--list", action="store_true",
                        help="List available personas")
    args = parser.parse_args()

    if args.list:
        print("Available trading personas:")
        for p in TRADING_PERSONAS:
            print(f"  - {p}")
        sys.exit(0)

    if args.persona:
        persona = args.persona.strip().lower()
        if persona not in TRADING_PERSONAS:
            print(f"Unknown persona: {persona}")
            print(f"Available: {', '.join(TRADING_PERSONAS)}")
            sys.exit(1)
        run(personas=[persona])
    else:
        run()
