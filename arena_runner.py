#!/usr/bin/env python3
"""
arena_runner.py — Self-contained unified weekly trading competition engine.
MASTER entry point for the weekly trading competition.
Runs autonomously as a no_agent cron job (Sunday 8AM SGT, script=arena_runner.py).

New Architecture (Phase 1/2/3):
  Phase 1: TV scanner → scan ALL 11 global markets → raw tickers + yfinance data
  Phase 2: Assign stocks to personas → ~50 UNIQUE stocks each (no overlap, sector-matched)
  Phase 3: For each persona, analyze in parallel (max 3 concurrent) → each stock → individual file
    * 3000+ word analysis in persona voice with verbatim SOUL.md quotes
    * All 21 TV indicators analyzed (100+ words per)
    * Real yfinance fundamentals
    * Deep web research
    * Saved as: 10_Trading/Competition/{persona}/{ticker} - YYYY-MM-DD.md

Output:
  10_Trading/Competition/{Persona}/{Ticker} - YYYY-MM-DD.md
"""

import os, sys, datetime, json, time, warnings, traceback, re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed, ProcessPoolExecutor
import threading
import subprocess

import numpy as np
import yfinance as yf
import requests

warnings.filterwarnings("ignore")

# ─── CONSTANTS ────────────────────────────────────────────────────────────────

PERSONAS = [
    "oneil", "buffet", "lynch", "minervini", "qullamaggie",
    "david-ryan", "matt-caruso", "brian-shannon", "dan-zanger", "nick-schmidt",
]

TODAY = datetime.date.today()
DATE_STR = TODAY.isoformat()

OBSIDIAN_VAULT = os.path.expanduser(
    "/Users/jiayanghan/Library/Mobile Documents/iCloud~md~obsidian/"
    "Documents/Mind Palace Obsidian current"
)
COMP_DIR = os.path.join(OBSIDIAN_VAULT, "10_Trading", "Competition")

PROFILES_DIR = os.path.expanduser("~/.hermes/profiles")

GLOBAL_MARKET_DATA_PATH = "/tmp/global_market_data.json"

TV_CDP_URL = "http://127.0.0.1:1234"  # TV Desktop CDP endpoint

# ─── 11 MARKET REGIONS — TradingView scanner regions ─────────────────────────

TV_MARKETS = {
    "US": "america",
    "China": "china",
    "Hong_Kong": "hongkong",
    "India": "india",
    "Japan": "japan",
    "UK": "uk",
    "Brazil": "brazil",
    "Korea": "korea",
    "Taiwan": "taiwan",
    "Turkey": "turkey",
    "Vietnam": "vietnam",
}

# Exchange suffix mapping for global stock access via yfinance
EXCHANGE_SUFFIXES = {
    "UK": ".L", "Japan": ".T", "Korea": ".KS", "India": ".NS",
    "Brazil": ".SA", "Hong_Kong": ".HK", "Canada": ".TO",
    "Singapore": ".SI", "Australia": ".AX",
    "Taiwan": ".TW", "Turkey": ".IS",
    "Shanghai": ".SS", "Shenzhen": ".SZ", "Vietnam": ".VN",
}

# ─── API CONFIG ───────────────────────────────────────────────────────────────

DEEPSEEK_API_KEY = None
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"

# Rate limiting for yfinance
_yf_lock = threading.Lock()

# ─── STOCKS PER PERSONA ──────────────────────────────────────────────────────
STOCKS_PER_PERSONA = 50

# ─── CONFIG LOADING ───────────────────────────────────────────────────────────

# ─── MODE FLAG ────────────────────────────────────────────────────────────────
# Set to "TARGET_STOCKS" to skip Phase 1 (TV scanner) and use a predefined list.
# Set to "TV_SCAN" (or anything else) for the normal full pipeline.
MODE = "TARGET_STOCKS"

# When MODE is TARGET_STOCKS, use this stock list instead of scanning global markets.
TARGET_STOCK_LIST = [
    "2245.HK", "CMTL", "CRDO", "ALAB", "WSTL", "MU",
    "080220.KS", "356860.KS", "031330.KS", "RDDT", "GLW",
]

# Bypass the TV scanner phase — use TARGET_STOCK_LIST directly.

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
                    env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return env


def _load_config_yaml():
    """Load deepseek API key from ~/.hermes/config.yaml as fallback."""
    try:
        import yaml
        cfg_path = os.path.expanduser("~/.hermes/config.yaml")
        if os.path.isfile(cfg_path):
            with open(cfg_path) as f:
                cfg = yaml.safe_load(f)
            providers = cfg.get("providers", {}) or {}
            ds = providers.get("deepseek", {})
            if ds.get("api_key"):
                return ds["api_key"]
            if ds.get("base_url"):
                global DEEPSEEK_BASE_URL
                DEEPSEEK_BASE_URL = ds["base_url"]
    except ImportError:
        pass
    except Exception:
        pass
    return None


def _resolve_api_key():
    """Resolve DeepSeek API key: env -> .env -> config.yaml"""
    global DEEPSEEK_API_KEY

    # 1. Check environment variable
    env_key = os.environ.get("DEEPSEEK_API_KEY")
    if env_key:
        DEEPSEEK_API_KEY = env_key
        return

    # 2. Check .env file
    env = _load_env()
    if "DEEPSEEK_API_KEY" in env and env["DEEPSEEK_API_KEY"]:
        DEEPSEEK_API_KEY = env["DEEPSEEK_API_KEY"]
        return

    # 3. Check config.yaml
    yaml_key = _load_config_yaml()
    if yaml_key:
        DEEPSEEK_API_KEY = yaml_key
        return


# ─── TV CDP HANDSHAKE ────────────────────────────────────────────────────────

def wait_for_tv_cdp(url=TV_CDP_URL, timeout=30, interval=2):
    """
    Active polling retry loop to check if TV Desktop CDP is ready.
    Returns True if ready, False if timed out.
    """
    start = time.monotonic()
    while (time.monotonic() - start) < timeout:
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code < 500:
                return True
        except requests.ConnectionError:
            pass
        except requests.Timeout:
            pass
        except Exception:
            pass
        time.sleep(interval)
    return False

# ─── TRADINGVIEW SCANNER API ─────────────────────────────────────────────────

TV_SCAN_URL = "https://scanner.tradingview.com/{region}/scan"

TV_SCAN_COLUMNS = [
    "name", "close", "change", "change_abs", "volume",
    "description", "Recommend.All", "RSI", "BB.upper", "BB.lower",
    "EMA50", "EMA200", "SMA50", "SMA200", "Volatility.D",
]

TV_SCAN_FILTER = [{"left": "change", "operation": "greater", "right": 0}]


def tv_scan_region(region, sort_by="volume", sort_order="desc", range_size=100):
    """
    Call TradingView scanner API for a single region.
    Returns list of {symbol, name, close, change, ...} or empty list on error.
    """
    payload = {
        "columns": TV_SCAN_COLUMNS,
        "sort": {"sortBy": sort_by, "sortOrder": sort_order},
        "range": [0, range_size],
        "filter": TV_SCAN_FILTER,
    }
    url = TV_SCAN_URL.format(region=region)
    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        stocks = []
        for item in data.get("data", []):
            d = item.get("d", [])
            if len(d) >= len(TV_SCAN_COLUMNS):
                entry = dict(zip(TV_SCAN_COLUMNS, d))
                stocks.append(entry)
            else:
                # Partial data — still capture what we have
                entry = dict(zip(TV_SCAN_COLUMNS[:len(d)], d))
                stocks.append(entry)
        return stocks
    except Exception as e:
        print(f"[Arena]  ⚠️  TV scan failed for {region} ({sort_by}): {e}", flush=True)
        return []


def tv_scan_tickers(region):
    """
    Scan a region for stocks. Runs two queries:
      1. Sort by volume (unusual activity)
      2. Sort by change (gainers)
    Merges and deduplicates.
    Returns a set of ticker symbol strings (with exchange suffix).
    """
    # First pass: volume sorted
    vol_stocks = tv_scan_region(region, sort_by="volume", sort_order="desc")
    # Second pass: change sorted (gainers)
    change_stocks = tv_scan_region(region, sort_by="change", sort_order="desc")
    # Merge all stocks
    all_stocks = vol_stocks + change_stocks
    # Deduplicate by symbol name
    seen = set()
    tickers = []
    for s in all_stocks:
        name = s.get("name", "")
        if name and name not in seen:
            seen.add(name)
            tickers.append(name)
    if not tickers:
        print(f"[Arena]  ⚠️  No tickers returned from TV scan for {region}", flush=True)
    return set(tickers)


def global_tv_scan():
    """
    Scan all 11 market regions via TradingView scanner API.
    Returns a dict of {region: [list of ticker strings]} and a list of (ticker, region) pairs.
    """
    by_region = {}
    all_ticker_pairs = []  # (ticker, region) tuples
    total = 0
    for display_name, tv_region in TV_MARKETS.items():
        region_tickers = tv_scan_tickers(tv_region)
        by_region[display_name] = list(region_tickers)
        for t in region_tickers:
            all_ticker_pairs.append((t, display_name))
        total += len(region_tickers)
        print(f"[Arena]  TV scan: {display_name} -> {len(region_tickers)} tickers", flush=True)
    print(f"[Arena]  TV scan total: {total} tickers across {len(TV_MARKETS)} regions", flush=True)
    return by_region, all_ticker_pairs


# ─── MARKET DATA ──────────────────────────────────────────────────────────────

def convert_tv_ticker(ticker, region):
    """
    Convert a TradingView scanner ticker to yfinance-compatible format.
    """
    suffix_map = {
        "US": "",
        "China": "",
        "Hong_Kong": ".HK",
        "India": ".NS",
        "Japan": ".T",
        "UK": ".L",
        "Brazil": ".SA",
        "Korea": ".KS",
        "Taiwan": ".TW",
        "Turkey": ".IS",
        "Vietnam": ".VN",
    }

    suffix = suffix_map.get(region, "")
    if not suffix:
        return ticker  # US or China ADR — no suffix needed

    if region == "UK":
        ticker = ticker.rstrip('.')
        if '.' in ticker:
            ticker = ticker.replace('.', '-')
        return ticker + ".L"

    if region == "Hong_Kong":
        while len(ticker) > 4 and ticker.startswith('0'):
            ticker = ticker[1:]
        return ticker + ".HK"

    return ticker + suffix


def compute_rsi(close, period=14):
    """Compute RSI from a pandas Series of close prices."""
    diff = close.diff()
    gain = diff.where(diff > 0, 0.0)
    loss = -diff.where(diff < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    val = rsi.iloc[-1]
    return float(val) if not (isinstance(val, float) and np.isnan(val)) else None


def analyze_ticker(ticker_str):
    """Fetch real data for one ticker via yfinance.
    Computes RSI(14), MA50, MA200, volume ratio, P/E, EPS growth, market cap.
    FIX 5 — Uses _yf_lock for rate-limiting. FIX 7 — Uses direct Yahoo API first.
    """
    try:
        # FIX 7 — Try direct Yahoo v8/v7 API first (10-15x faster)
        v8_data = yahoo_v8_history(ticker_str, range_str="1y", interval="1d")
        v7_data = yahoo_v7_quote(ticker_str)
        v8_ok = v8_data is not None and len(v8_data.get("close", [])) >= 20
        v7_ok = v7_data is not None

        if v8_ok and v7_ok:
            # Use direct API data
            close_arr = np.array([c for c in v8_data["close"] if c is not None], dtype=float)
            vol_arr = np.array([v for v in v8_data["volume"] if v is not None], dtype=float)
            if len(close_arr) < 20:
                return None
            import pandas as pd
            close_series = pd.Series(close_arr)
            vol_series = pd.Series(vol_arr)
            price = float(close_arr[-1])
            mcap = v7_data.get("marketCap")
            if price < 2.0 and (mcap is None or mcap < 50e6):
                return None
            prev_close = float(close_arr[-2]) if len(close_arr) > 1 else price
            change_pct = round((price - prev_close) / prev_close * 100, 2)
            rsi = compute_rsi(close_series)
            ma50 = float(np.mean(close_arr[-50:])) if len(close_arr) >= 50 else None
            ma200 = float(np.mean(close_arr[-200:])) if len(close_arr) >= 200 else None
            avg_vol = float(np.mean(vol_arr[-50:])) if len(vol_arr) >= 50 else float(np.mean(vol_arr))
            vol_ratio = round(float(vol_arr[-1]) / avg_vol, 2) if avg_vol > 0 else 1.0
            pe = v7_data.get("trailingPE") or v7_data.get("forwardPE")
            eps = v7_data.get("trailingEps") or v7_data.get("forwardEps")
            eps_growth = v7_data.get("earningsQuarterlyGrowth")
            sector = v7_data.get("sector", "Unknown")
            beta = v7_data.get("beta")
            dividend_yield = v7_data.get("dividendYield")
            return {
                "ticker": ticker_str,
                "price": round(price, 2),
                "change_pct": change_pct,
                "rsi": rsi,
                "ma50": round(ma50, 2) if ma50 else None,
                "ma200": round(ma200, 2) if ma200 else None,
                "vol_ratio": vol_ratio,
                "pe": pe,
                "eps": eps,
                "eps_growth": eps_growth,
                "mcap": mcap,
                "sector": sector,
                "beta": beta,
                "dividend_yield": dividend_yield,
            }

        # Fallback: yfinance with rate-limiting and retry
        hist = _yf_fetch_with_retry(ticker_str, operation="history", period="1y")
        info = _yf_fetch_with_retry(ticker_str, operation="info", period="1y")
        if hist is None or hist.empty or len(hist) < 20:
            return None

        close = hist["Close"]
        volume = hist["Volume"]

        price = float(close.iloc[-1])
        # Skip penny stocks: price < 2.0 AND mcap < 50M
        mcap = info.get("marketCap")
        if price < 2.0 and (mcap is None or mcap < 50e6):
            return None

        prev_close = float(close.iloc[-2]) if len(close) > 1 else price
        change_pct = round((price - prev_close) / prev_close * 100, 2)
        rsi = compute_rsi(close)

        ma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
        ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None

        avg_vol = float(volume.iloc[-50:].mean()) if len(volume) >= 50 else float(volume.mean())
        vol_ratio = round(float(volume.iloc[-1]) / avg_vol, 2) if avg_vol > 0 else 1.0

        pe = info.get("trailingPE") or info.get("forwardPE")
        eps = info.get("trailingEps") or info.get("forwardEps")
        eps_growth = info.get("earningsQuarterlyGrowth")
        sector = info.get("sector", "Unknown")
        beta = info.get("beta")
        dividend_yield = info.get("dividendYield")

        return {
            "ticker": ticker_str,
            "price": round(price, 2),
            "change_pct": change_pct,
            "rsi": rsi,
            "ma50": round(ma50, 2) if ma50 else None,
            "ma200": round(ma200, 2) if ma200 else None,
            "vol_ratio": vol_ratio,
            "pe": pe,
            "eps": eps,
            "eps_growth": eps_growth,
            "mcap": mcap,
            "sector": sector,
            "beta": beta,
            "dividend_yield": dividend_yield,
        }
    except Exception as e:
        return {"ticker": ticker_str, "error": str(e)}

def scan_market_data(ticker_list):
    """
    Scan all tickers using yfinance + ThreadPoolExecutor(max_workers=6).
    Returns a dict of {ticker: data_dict}.
    """
    results = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_map = {}
        for t in ticker_list:
            fut = pool.submit(analyze_ticker, t)
            fut_map[fut] = t
        for fut in as_completed(fut_map):
            t = fut_map[fut]
            try:
                data = fut.result()
                results[t] = data
            except Exception:
                results[t] = {"ticker": t, "error": "exception"}
    return results


# ─── TV INDICATOR COMPUTATION (local via yfinance + numpy) ───────────────────

def _ema(values, period):
    """Compute exponential moving average."""
    weights = np.exp(np.linspace(-1, 0, period))
    weights /= weights.sum()
    return np.convolve(values, weights, mode='valid')


def compute_all_indicators(ticker):
    """
    Compute 21 technical indicators for a single ticker via yfinance + numpy.
    FIX 5 — Uses _yf_lock for rate-limiting. FIX 7 — Uses direct Yahoo API first.
    Returns dict of {indicator_name: value} or None on failure.
    """
    try:
        # FIX 7 — Try direct Yahoo v8 API first
        v8_data = yahoo_v8_history(ticker, range_str="6mo", interval="1d")
        if v8_data is not None and len(v8_data.get("close", [])) >= 30:
            close_arr = np.array([c for c in v8_data["close"] if c is not None], dtype=float)
            high_arr = np.array([h for h in v8_data["high"] if h is not None], dtype=float)
            low_arr = np.array([l for l in v8_data["low"] if l is not None], dtype=float)
            vol_arr = np.array([v for v in v8_data["volume"] if v is not None], dtype=float)
            if len(close_arr) >= 30 and len(high_arr) >= 30 and len(low_arr) >= 30 and len(vol_arr) >= 30:
                close = close_arr
                high = high_arr
                low = low_arr
                volume = vol_arr
            else:
                v8_data = None

        if v8_data is None:
            # Fallback: yfinance with rate-limiting and retry
            hist = _yf_fetch_with_retry(ticker, operation="history", period="6mo")
            if hist is None or hist.empty or len(hist) < 30:
                return None
            close = hist["Close"].values
            high = hist["High"].values
            low = hist["Low"].values
            volume = hist["Volume"].values

        result = {}
        price = float(close[-1])

        # 1. RSI(14)
        delta = np.diff(close)
        gains = np.where(delta > 0, delta, 0)
        losses = np.where(delta < 0, -delta, 0)
        avg_gain = np.mean(gains[-14:]) if len(gains) >= 14 else np.mean(gains)
        avg_loss = np.mean(losses[-14:]) if len(losses) >= 14 else np.mean(losses)
        if avg_loss == 0:
            result["RSI"] = 100.0
        else:
            rs = avg_gain / avg_loss
            result["RSI"] = round(100.0 - (100.0 / (1.0 + rs)), 2)

        # 2. MA50, 3. MA200
        if len(close) >= 50:
            result["MA50"] = round(float(np.mean(close[-50:])), 2)
        if len(close) >= 200:
            result["MA200"] = round(float(np.mean(close[-200:])), 2)

        # 4-6. MACD (12, 26, 9)
        if len(close) >= 26:
            ema12 = _ema(close, 12)
            ema26 = _ema(close, 26)
            macd_line = ema12 - ema26
            signal = _ema(macd_line, 9)
            result["MACD"] = round(float(macd_line[-1]), 4)
            result["MACD_signal"] = round(float(signal[-1]), 4)
            result["MACD_histogram"] = round(float(macd_line[-1] - signal[-1]), 4)

        # 7-9. Bollinger Bands (20, 2)
        if len(close) >= 20:
            sma20 = np.mean(close[-20:])
            std20 = np.std(close[-20:])
            result["BB_upper"] = round(float(sma20 + 2 * std20), 2)
            result["BB_middle"] = round(float(sma20), 2)
            result["BB_lower"] = round(float(sma20 - 2 * std20), 2)
            result["BB_width_pct"] = round(float(4 * std20 / sma20 * 100), 2) if sma20 != 0 else 0

        # 10-11. ATR(14) + ATR%
        if len(high) >= 15:
            tr = np.maximum(high[1:] - low[1:],
                            np.maximum(np.abs(high[1:] - close[:-1]),
                                       np.abs(low[1:] - close[:-1])))
            result["ATR"] = round(float(np.mean(tr[-14:])), 4)
            result["ATR_pct"] = round(result["ATR"] / price * 100, 2) if price > 0 else 0

        # 12-13. EMA 9, 21
        for period in [9, 21]:
            if len(close) >= period:
                result[f"EMA{period}"] = round(float(_ema(close, period)[-1]), 2)

        # 14-15. Volume + Volume ratio
        if len(volume) >= 20:
            avg_vol = np.mean(volume[-20:])
            result["Volume"] = int(volume[-1])
            result["Volume_ratio"] = round(float(volume[-1] / avg_vol), 2) if avg_vol > 0 else 1.0

        # 16-17. Stochastic (14,3)
        if len(close) >= 14:
            low14 = np.min(low[-14:])
            high14 = np.max(high[-14:])
            k = (close[-1] - low14) / (high14 - low14) * 100 if (high14 - low14) > 0 else 50
            result["Stoch_K"] = round(float(k), 2)
            if len(close) >= 16:
                k_vals = []
                for i in range(-3, 0):
                    l14 = np.min(low[-14 + i:i])
                    h14 = np.max(high[-14 + i:i])
                    kv = (close[i] - l14) / (h14 - l14) * 100 if (h14 - l14) > 0 else 50
                    k_vals.append(kv)
                result["Stoch_D"] = round(float(np.mean(k_vals)), 2)

        # 18. Price
        result["Price"] = round(price, 2)

        # 19. 52-Week High/Low
        if len(close) >= 252:
            result["52w_high"] = round(float(np.max(close[-252:])), 2)
            result["52w_low"] = round(float(np.min(close[-252:])), 2)
        elif len(close) >= 1:
            result["52w_high"] = round(float(np.max(close)), 2)
            result["52w_low"] = round(float(np.min(close)), 2)

        # 20. SMA20
        if len(close) >= 20:
            result["SMA20"] = round(float(np.mean(close[-20:])), 2)

        # 21. Change %
        if len(close) >= 2:
            result["Change_pct"] = round(float((close[-1] - close[-2]) / close[-2] * 100), 2)

        return result
    except Exception:
        return None


def compute_indicators_batch(ticker_list):
    """Compute indicators for a list of tickers using ThreadPoolExecutor."""
    results = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_map = {pool.submit(compute_all_indicators, t): t for t in ticker_list}
        for fut in as_completed(fut_map):
            t = fut_map[fut]
            try:
                data = fut.result()
                if data:
                    results[t] = data
            except Exception:
                pass
    return results


# ─── MARKET DATA CACHING ─────────────────────────────────────────────────────

def load_or_scan_market_data():
    """
    Load from /tmp/global_market_data.json if available,
    otherwise run full market scan via TV scanner + yfinance.
    Returns (by_region dict, flat results dict, bool indicating if cached).
    """
    if os.path.isfile(GLOBAL_MARKET_DATA_PATH):
        try:
            with open(GLOBAL_MARKET_DATA_PATH) as f:
                data = json.load(f)
            by_region = data.get("by_region", {})
            results = {}
            for region, stocks in by_region.items():
                for s in stocks:
                    if "ticker" in s:
                        results[s["ticker"]] = s
            print(f"[Arena]  Loaded market data from {GLOBAL_MARKET_DATA_PATH} ({len(results)} tickers)", flush=True)
            return by_region, results, True
        except Exception as e:
            print(f"[Arena]  Failed to load cached market data: {e}", flush=True)

    print(f"[Arena]  No cached market data found. Running TV scanner + yfinance scan...", flush=True)

    # Step 1: TV scanner — get all tickers across 11 regions
    tv_by_region, all_ticker_pairs = global_tv_scan()
    raw_to_yf = {}
    yf_ticker_set = set()
    for bare_ticker, region in all_ticker_pairs:
        yf_ticker = convert_tv_ticker(bare_ticker, region)
        yf_ticker_set.add(yf_ticker)
        if yf_ticker not in raw_to_yf:
            raw_to_yf[yf_ticker] = (bare_ticker, region)
    ticker_list = sorted(yf_ticker_set)
    print(f"[Arena]  Total unique tickers from TV scan: {len(ticker_list)}", flush=True)

    # Skip non-exchange numeric tickers
    ticker_list = [t for t in ticker_list if not (t[0].isdigit() and not any(t.endswith(suf) for suf in [".L", ".T", ".KS", ".NS", ".SA", ".HK", ".TW", ".IS", ".VN"]))]
    print(f"[Arena]  After ticker filter: {len(ticker_list)} tickers", flush=True)

    # FIX 3: Smarter yfinance Ticker Selection
    def score_ticker(t):
        """Score ticker by likelihood of having valid yfinance data."""
        score = 0
        known_suffixes = [".L", ".SA", ".HK", ".KS", ".NS", ".T", ".VN", ".IS", ".TW"]
        suffix = next((s for s in known_suffixes if t.endswith(s)), None)
        # US stocks (no suffix): +10 points
        if suffix is None:
            score += 10
        # UK (.L), Brazil (.SA), HK (.HK): +5 points
        elif suffix in (".L", ".SA", ".HK"):
            score += 5
        # Korea (.KS), India (.NS), Japan (.T): +3 points
        elif suffix in (".KS", ".NS", ".T"):
            score += 3
        # Vietnam (.VN), Turkey (.IS): +1 point
        elif suffix in (".VN", ".IS"):
            score += 1
        # Starts with digit AND no recognized suffix: -100 points
        if t[0].isdigit() and suffix is None:
            score -= 100
        return score

    ticker_list.sort(key=score_ticker, reverse=True)
    selected = ticker_list[:150]
    print(f"[Arena]  Selected {len(selected)} tickers by yfinance likelihood score (top score={score_ticker(selected[0]) if selected else chr(39)+chr(78)+chr(47)+chr(65)+chr(39)})", flush=True)
    ticker_list = selected

    print(f"[Arena]  Computing fundamentals via yfinance for {len(ticker_list)} tickers...", flush=True)
    raw_results = scan_market_data(ticker_list)

    # Build by_region from TV scan + yfinance
    by_region = {}
    for region, tickers in tv_by_region.items():
        region_data = []
        for bare_t in tickers:
            yf_t = convert_tv_ticker(bare_t, region)
            if yf_t in raw_results and raw_results[yf_t]:
                region_data.append(raw_results[yf_t])
        if region_data:
            by_region[region] = region_data

    # Compute full indicators for valid tickers
    valid_tickers = [t for t, d in raw_results.items() if d and "error" not in d]
    print(f"[Arena]  Computing full 21 indicators for {len(valid_tickers)} tickers...", flush=True)
    indicators = compute_indicators_batch(valid_tickers)
    for t, ind in indicators.items():
        if t in raw_results and raw_results[t]:
            raw_results[t]["indicators"] = ind

    # Cache results
    try:
        cache_by_region = {}
        for region, stocks in by_region.items():
            region_cache = []
            for s in stocks:
                if s and "ticker" in s:
                    entry = dict(s)
                    yf_t = s["ticker"]
                    bare_t, orig_region = raw_to_yf.get(yf_t, (yf_t, region))
                    entry["_raw_ticker"] = bare_t
                    entry["_region"] = orig_region
                    region_cache.append(entry)
            cache_by_region[region] = region_cache
        cache_data = {"by_region": cache_by_region, "date": DATE_STR, "_has_region_info": True}
        with open(GLOBAL_MARKET_DATA_PATH, "w") as f:
            json.dump(cache_data, f, default=str)
        print(f"[Arena]  Cached market data to {GLOBAL_MARKET_DATA_PATH}", flush=True)
    except Exception as e:
        print(f"[Arena]  ⚠️  Failed to cache market data: {e}", flush=True)

    returns_by_region = {}
    for region, tickers in tv_by_region.items():
        region_data = []
        for bare_t in tickers:
            yf_t = convert_tv_ticker(bare_t, region)
            if yf_t in raw_results and raw_results[yf_t]:
                region_data.append(raw_results[yf_t])
        if region_data:
            returns_by_region[region] = region_data

    return returns_by_region, raw_results, False

# ─── PERSONA SOUL LOADING ─────────────────────────────────────────────────────

def load_persona_soul(persona_name):
    """Read a persona's SOUL.md from ~/.hermes/profiles/{name}/SOUL.md."""
    soul_path = os.path.join(PROFILES_DIR, persona_name, "SOUL.md")
    try:
        with open(soul_path, "r") as f:
            return f.read()
    except FileNotFoundError:
        print(f"[Arena]  ⚠️  SOUL.md not found for {persona_name} at {soul_path}", flush=True)
        return None
    except Exception as e:
        print(f"[Arena]  ⚠️  Error reading SOUL.md for {persona_name}: {e}", flush=True)
        return None


# ─── DEEPSEEK API CALL ───────────────────────────────────────────────────────

def call_deepseek(system_prompt, user_message, timeout=180, max_tokens=8192):
    """
    Call the DeepSeek chat API (OpenAI-compatible endpoint).
    Returns the response text or None on failure.
    """
    if not DEEPSEEK_API_KEY:
        print(f"[Arena]  ❌ DeepSeek API key not configured", flush=True)
        return None

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.7,
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
        return result["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"[Arena]  ❌ DeepSeek API error: {e}", flush=True)
        return None


# ─── WEB RESEARCH ─────────────────────────────────────────────────────────────

def perform_web_research(ticker, persona_name):
    """
    Search for recent news/catalysts for a given ticker using yfinance news + web.
    Returns a markdown summary string (or None).
    """
    try:
        stock = yf.Ticker(ticker)
        news = None
        try:
            news = stock.news
        except Exception:
            pass

        if news and len(news) > 0:
            items = news[:5]
            summary_parts = [f"### Recent News for {ticker}\n"]
            for item in items:
                title = item.get("title", "?")
                link = item.get("link", "")
                publisher = item.get("publisher", "")
                summary_parts.append(f"- **{title}** ({publisher})")
                if link:
                    summary_parts.append(f"  {link}")
            return "\n".join(summary_parts)

        # Fallback: Yahoo Finance news page
        search_url = f"https://finance.yahoo.com/quote/{ticker}/news"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
        }
        try:
            resp = requests.get(search_url, headers=headers, timeout=10)
            if resp.status_code == 200:
                from html.parser import HTMLParser

                class HeadlineParser(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.headlines = []
                        self._capture = False

                    def handle_starttag(self, tag, attrs):
                        attrs_dict = dict(attrs)
                        if tag == "h3":
                            self._capture = True

                    def handle_data(self, data):
                        if self._capture:
                            text = data.strip()
                            if text and len(text) > 10:
                                self.headlines.append(text)
                            self._capture = False

                parser = HeadlineParser()
                parser.feed(resp.text)
                if parser.headlines:
                    summary_parts = [f"### Recent Headlines for {ticker}\n"]
                    for h in parser.headlines[:5]:
                        summary_parts.append(f"- {h}")
                    return "\n".join(summary_parts)
        except Exception:
            pass

        return None
    except Exception as e:
        print(f"[Arena]  Web research error for {ticker}: {e}", file=sys.stderr)
        return None

# ─── TV MCP INDICATOR READING (TV Desktop CDP) ──────────────────────────────

# Chrome CDP helpers for TradingView fallback
_CHROME_CDP = "http://127.0.0.1:9222"
_TV_CHART_URL = "https://www.tradingview.com/chart/"


def _chrome_list_tabs():
    """List open tabs via Chrome DevTools Protocol."""
    try:
        r = requests.get(f"{_CHROME_CDP}/json", timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return []


def _chrome_create_tab(url=None):
    """Create a new tab in Chrome CDP, optionally navigating to url."""
    try:
        payload = {"url": url or "about:blank"}
        r = requests.put(f"{_CHROME_CDP}/json/new", json=payload, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def _chrome_evaluate(tab_id, js_expression):
    """Evaluate JavaScript in a specific CDP tab via WebSocket."""
    import json as _json
    try:
        tabs = _chrome_list_tabs()
        ws_url = None
        for tab in tabs:
            if tab.get("id") == tab_id:
                ws_url = tab.get("webSocketDebuggerUrl")
                break
        if not ws_url:
            return None

        from websocket import create_connection

        ws = create_connection(ws_url, timeout=10)
        cmd_id = 1
        cmd = _json.dumps({
            "id": cmd_id,
            "method": "Runtime.evaluate",
            "params": {
                "expression": js_expression,
                "returnByValue": True,
                "awaitPromise": True,
            }
        })
        ws.send(cmd)
        response = ws.recv()
        ws.close()
        result = _json.loads(response)
        if "result" in result and "result" in result["result"]:
            return result["result"]["result"].get("value")
        return None
    except ImportError:
        print(f"[Arena]  websocket-client not installed, skipping Chrome CDP JS eval", flush=True)
        return None
    except Exception:
        return None


def _chrome_navigate_and_read_studies(ticker):
    """Navigate Chrome to TV chart for ticker, extract study values via JS."""
    try:
        try:
            import websocket
        except ImportError:
            return None

        tabs = _chrome_list_tabs()
        if not tabs:
            tab = _chrome_create_tab(_TV_CHART_URL + ticker)
            if not tab:
                return None
            tab_id = tab.get("id")
            time.sleep(3)
        else:
            tv_tab = None
            for tab in tabs:
                url = tab.get("url", "")
                if "tradingview.com/chart" in url:
                    tv_tab = tab
                    break
            if tv_tab:
                tab_id = tv_tab["id"]
                js_go = f"window.location.href = '{_TV_CHART_URL}{ticker}';"
                _chrome_evaluate(tab_id, js_go)
                time.sleep(4)
            else:
                tab = _chrome_create_tab(_TV_CHART_URL + ticker)
                if not tab:
                    return None
                tab_id = tab.get("id")
                time.sleep(3)

        js_extract = """
(async () => {
    await new Promise(resolve => {
        const check = () => {
            if (window.tvWidget && window.tvWidget.chart) resolve();
            else setTimeout(check, 200);
        };
        check();
    });
    await new Promise(resolve => setTimeout(resolve, 1500));
    try {
        const chart = window.tvWidget.chart();
        const studies = chart.getAllStudies();
        const result = {};
        for (const s of studies) {
            try {
                const vals = await chart.getStudyValues(s.id);
                result[s.name] = vals;
            } catch(e) {}
        }
        return JSON.stringify(result);
    } catch(e) {
        return JSON.stringify({error: e.message});
    }
})();
"""
        raw = _chrome_evaluate(tab_id, js_extract)
        if raw and isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
        return None
    except Exception:
        return None


def read_tv_mcp_indicators(ticker_list):
    """
    Read TradingView indicator values for tickers using cascade:
    1. Try TV Desktop CDP (port 1234) with pine lines/labels + screenshot + indicator cycling
    2. Compute indicators locally via yfinance + numpy as fallback
    3. Merge: local indicators baseline, TV CDP data overlay

    Returns dict of {ticker: {local_indicators: {...}, tv_cdp: {...}}} or empty dict.
    """
    import sys as _sys
    _venv_site = os.path.expanduser('~/hermes_env/lib/python3.12/site-packages')
    if os.path.isdir(_venv_site) and _venv_site not in _sys.path:
        _sys.path.insert(0, _venv_site)

    # ─── TV CDP reading ────────────────────────────────────────────────
    def _tv_cdp_read(ticker):
        """Read from TV Desktop CDP: pine lines, labels, screenshot, indicator cycling."""
        try:
            resp = requests.get(
                f"{TV_CDP_URL}/navigate",
                params={"symbol": ticker},
                timeout=10,
            )
            if resp.status_code != 200:
                return None
            time.sleep(1)

            data = {"ticker": ticker, "source": "tv_cdp"}

            # Pine lines
            try:
                r_lines = requests.get(
                    f"{TV_CDP_URL}/mcp_tradingview_data_get_pine_lines",
                    timeout=10,
                )
                if r_lines.status_code == 200:
                    data["pine_lines"] = r_lines.json()
            except Exception:
                pass

            # Pine labels
            try:
                r_labels = requests.get(
                    f"{TV_CDP_URL}/mcp_tradingview_data_get_pine_labels",
                    timeout=10,
                )
                if r_labels.status_code == 200:
                    data["pine_labels"] = r_labels.json()
            except Exception:
                pass

            # Screenshot
            try:
                r_ss = requests.get(
                    f"{TV_CDP_URL}/capture_screenshot",
                    params={"symbol": ticker},
                    timeout=15,
                )
                if r_ss.status_code == 200:
                    data["screenshot_path"] = r_ss.json().get("path")
            except Exception:
                pass

            # Cycle 21 indicators 2 at a time via chart_manage_indicator
            STANDARD_INDICATORS = [
                "RSI", "MACD", "Bollinger Bands", "Moving Average Exponential",
                "Volume", "ATR", "Stochastic", "Moving Average", "Ichimoku Cloud",
                "Parabolic SAR", "Commodity Channel Index", "On Balance Volume",
                "Money Flow Index", "Williams %R", "Awesome Oscillator",
                "Chaikin Money Flow", "Rate of Change", "Elder-Ray Index",
                "Keltner Channels", "Donchian Channels", "VWAP",
            ]

            read_indicators = []
            indicator_data = {}
            for i in range(0, len(STANDARD_INDICATORS), 2):
                batch = STANDARD_INDICATORS[i:i+2]
                for ind_name in batch:
                    try:
                        resp_add = requests.post(
                            f"{TV_CDP_URL}/chart_manage_indicator",
                            json={"action": "add", "name": ind_name},
                            timeout=10,
                        )
                        if resp_add.status_code == 200:
                            read_indicators.append(ind_name)
                        time.sleep(1)
                    except Exception:
                        pass

                time.sleep(2)

                for ind_name in batch:
                    try:
                        r_lines2 = requests.get(
                            f"{TV_CDP_URL}/mcp_tradingview_data_get_pine_lines",
                            timeout=10,
                        )
                        if r_lines2.status_code == 200:
                            indicator_data[ind_name] = r_lines2.json()
                    except Exception:
                        pass

                    try:
                        requests.post(
                            f"{TV_CDP_URL}/chart_manage_indicator",
                            json={"action": "remove", "name": ind_name},
                            timeout=5,
                        )
                    except Exception:
                        pass

            if indicator_data:
                data["tv_indicators"] = indicator_data
            data["indicators_read"] = read_indicators

            return data
        except Exception:
            return None

    # ─── Main cascade ──────────────────────────────────────────────────
    enhanced = {}
    successful_tickers = 0

    cdp_ok = wait_for_tv_cdp(timeout=10)
    if cdp_ok:
        print(f"[Arena]  TV Desktop CDP ready -- reading enhanced data...", flush=True)
        for ticker in ticker_list[:50]:
            tv_data = _tv_cdp_read(ticker)
            if tv_data:
                enhanced[ticker] = tv_data
                successful_tickers += 1

        if successful_tickers:
            print(f"[Arena]  TV Desktop CDP: enhanced data for {successful_tickers} tickers", flush=True)

    # Compute local indicators as baseline for ALL tickers
    print(f"[Arena]  Computing local indicators for {len(ticker_list)} tickers via yfinance...", flush=True)
    local_results = compute_indicators_batch(ticker_list[:50])
    if local_results:
        print(f"[Arena]  Local indicators computed for {len(local_results)} tickers", flush=True)

    # Merge
    merged = {}
    for ticker in ticker_list[:50]:
        entry = {}
        if ticker in local_results:
            entry["local_indicators"] = local_results[ticker]
        if ticker in enhanced:
            entry["tv_cdp"] = enhanced[ticker]
        if entry:
            merged[ticker] = entry

    if merged:
        print(f"[Arena]  TV indicators: merged data for {len(merged)} tickers", flush=True)
        return merged
    elif local_results:
        print(f"[Arena]  TV CDP failed, returning local yfinance indicators only", flush=True)
        result = {}
        for ticker, loc in local_results.items():
            result[ticker] = {"local_indicators": loc}
        return result

    print(f"[Arena]  No indicator data available", flush=True)
    return {}

# ─── PERSONA SCREENING FUNCTIONS ──────────────────────────────────────────

def _safe_float(v):
    """Convert value to float, return None if not possible."""
    if v is None: return None
    try: return float(v)
    except: return None

def filter_by_persona_criteria(persona, stock):
    """
    FIX 1: Phase 2b - Filter stock against a specific persona's methodology criteria.
    Only stocks that PASS should be analyzed.
    Returns (passes: bool, reason: str).
    """
    price = _safe_float(stock.get("price"))
    pe = _safe_float(stock.get("pe"))
    eps = _safe_float(stock.get("eps"))
    eps_growth = _safe_float(stock.get("eps_growth"))
    rsi = _safe_float(stock.get("rsi"))
    ma50 = _safe_float(stock.get("ma50"))
    ma200 = _safe_float(stock.get("ma200"))
    mcap = _safe_float(stock.get("mcap"))
    vol_ratio = _safe_float(stock.get("vol_ratio"))
    change_pct = _safe_float(stock.get("change_pct"))
    volume = stock.get("volume")
    if volume is None:
        volume = stock.get("Volume")
    volume_f = _safe_float(volume) if volume is not None else None

    if persona == "oneil":
        if pe is None or pe <= 0:
            return False, "PE missing or non-positive"
        if pe < 5 or pe > 50:
            return False, f"PE {pe:.1f} outside 5-50 range"
        if eps_growth is None or eps_growth <= 0:
            return False, "No positive EPS growth"
        if rsi is not None and rsi <= 30:
            return False, f"RSI {rsi:.0f} <= 30"
        if ma50 is not None and price is not None and price <= ma50:
            return False, f"Price ${price:.2f} <= MA50 ${ma50:.2f}"
        if price is None:
            return False, "No price data"
        return True, "Passes O'Neil: PE 5-50, positive EPS growth, RSI>30, price>MA50"

    if persona == "buffet":
        # FIX 1 — Minimum market cap $10B
        if pe is None or pe <= 0:
            return False, "PE missing or non-positive"
        if pe < 5 or pe > 25:
            return False, f"PE {pe:.1f} outside 5-25 range"
        if eps is None or eps <= 0:
            return False, "No positive EPS"
        if mcap is None:
            return False, "Market cap missing"
        if mcap < 10e9:
            return False, "Market cap < 10B"
        if price is None:
            return False, "No price data"
        return True, "Passes Buffett: PE 5-25, EPS>0, mcap>10B"

    if persona == "lynch":
        # FIX 1 — Guard against eps_growth=0 and division by zero
        if pe is None or pe <= 0:
            return False, "P/E too low or missing"
        if eps_growth is None or eps_growth <= 0:
            return False, "No positive EPS growth"
        # Safe PEG computation: eps_growth > 0 guaranteed above
        peg = pe / (eps_growth * 100)
        if peg >= 3.0:
            return False, f"PEG {peg:.2f} >= 3.0"
        if price is None:
            return False, "No price data"
        return True, "Passes Lynch: PE>0, EPS growth>0, PEG<3.0"

    if persona == "minervini":
        if price is None:
            return False, "No price data"
        if ma50 is not None and price <= ma50:
            return False, f"Price ${price:.2f} <= MA50 ${ma50:.2f}"
        if ma200 is not None and ma50 is not None and ma50 <= ma200:
            return False, f"MA50 ${ma50:.2f} <= MA200 ${ma200:.2f}"
        if rsi is not None and (rsi < 30 or rsi > 80):
            return False, f"RSI {rsi:.0f} outside 30-80 range"
        if eps_growth is not None and eps_growth <= 0:
            return False, "Negative EPS growth"
        return True, "Passes Minervini: trend template, RSI 30-80"

    if persona == "qullamaggie":
        if price is None:
            return False, "No price data"
        conditions_met = 0
        if vol_ratio is not None and vol_ratio > 1.0:
            conditions_met += 1
        if change_pct is not None and change_pct > 2:
            conditions_met += 1
        if ma50 is not None and price > ma50:
            conditions_met += 1
        if conditions_met == 0:
            return False, "No criteria met: need VR>1.0 OR change>2% OR price>MA50"
        return True, f"Passes Qullamaggie: {conditions_met}/3 criteria met"

    if persona == "david-ryan":
        # FIX 2 — EPS acceleration is core. If EPS missing, require BOTH price>MA50 and vol_ratio>1.2
        if eps_growth is not None and eps_growth > 0.1:
            return True, f"Passes David Ryan: EPS growth {eps_growth*100:.1f}% > 10%"
        if eps_growth is None:
            # No EPS data: require BOTH price>MA50 and unusual volume
            if ma50 is not None and price is not None and price > ma50 and vol_ratio is not None and vol_ratio > 1.2:
                return True, f"Passes David Ryan: price > MA50 and vol_ratio {vol_ratio:.1f} > 1.2 (no EPS)"
            return False, "No EPS growth data; need price>MA50 AND vol_ratio>1.2"
        if eps_growth is not None and eps_growth <= 0.1:
            return False, f"EPS growth {eps_growth*100:.1f}% <= 10%"
        return False, "No criteria met: need EPS growth>10% OR (price>MA50 AND vol_ratio>1.2)"

    if persona == "matt-caruso":
        if price is None:
            return False, "No price data"
        if price <= 2.0:
            return False, f"Price ${price:.2f} <= $2.0"
        if volume_f is not None and volume_f <= 50000:
            return False, f"Volume {volume_f:.0f} <= 50000"
        return True, "Passes Caruso: price>2, volume>50000"

    if persona == "brian-shannon":
        if price is None:
            return False, "No price data"
        conditions_met = 0
        if ma50 is not None and price > ma50:
            conditions_met += 1
        if rsi is not None and rsi > 40:
            conditions_met += 1
        if conditions_met == 0:
            return False, "Need price>MA50 OR RSI>40"
        return True, "Passes Shannon: uptrend or RSI>40"

    if persona == "dan-zanger":
        if price is None:
            return False, "No price data"
        conditions_met = 0
        if change_pct is not None and change_pct > 1:
            conditions_met += 1
        if vol_ratio is not None and vol_ratio > 1.2:
            conditions_met += 1
        if ma50 is not None and price > ma50:
            conditions_met += 1
        if conditions_met == 0:
            return False, "Need change>1% OR VR>1.2 OR price>MA50"
        return True, f"Passes Zanger: {conditions_met}/3 criteria met"

    if persona == "nick-schmidt":
        if price is None:
            return False, "No price data"
        conditions_met = 0
        if ma50 is not None and price > ma50:
            conditions_met += 1
        if rsi is not None and rsi > 40:
            conditions_met += 1
        if conditions_met == 0:
            return False, "Need price>MA50 OR RSI>40"
        return True, "Passes Schmidt: uptrend or RSI>40"

    return False, f"Unknown persona: {persona}"




def passes_oneil(stock):
    """CAN SLIM pre-check: EPS growth > 0, price above MA50, PE > 0, RSI > 30"""
    price = _safe_float(stock.get("price"))
    pe = _safe_float(stock.get("pe"))
    eps = _safe_float(stock.get("eps_growth"))
    rsi = _safe_float(stock.get("rsi"))
    ma50 = _safe_float(stock.get("ma50"))
    mcap = _safe_float(stock.get("mcap"))
    
    if pe is None or pe <= 0: return False, "Negative/no P/E"
    if eps is None or eps <= 0: return False, "No EPS growth"
    if price is None: return False, "No price data"
    if ma50 and price < ma50 * 0.8: return False, "Price too far below 50-MA"
    if rsi is not None and rsi < 30: return False, "RSI oversold"
    if mcap and mcap < 50e6: return False, "Market cap too small"
    return True, "Passes CAN SLIM pre-check"

def passes_buffett(stock):
    """4 Gates: P/E 5-20, positive EPS, mcap > 10B, reasonable sector"""
    price = _safe_float(stock.get("price"))
    pe = _safe_float(stock.get("pe"))
    eps = _safe_float(stock.get("eps_growth"))
    mcap = _safe_float(stock.get("mcap"))
    sector = stock.get("sector", "")
    
    if pe is None or pe < 5 or pe > 20: return False, f"P/E {pe} outside 5-20 range"
    if eps is None or eps < 0: return False, "No positive earnings growth"
    if mcap is None or mcap < 1e9: return False, "Market cap under B"
    if price is None or price < 2: return False, "Price too low"
    if sector in ["Unknown", ""]: return False, "Unknown sector"
    return True, "Passes 4 Gates"

def passes_lynch(stock):
    """PEG ratio < 2.0 or reasonable story stock"""
    pe = _safe_float(stock.get("pe"))
    eps = _safe_float(stock.get("eps_growth"))
    price = _safe_float(stock.get("price"))
    
    if pe is None or pe <= 0: return False, "No P/E"
    if eps is None or eps <= 0: return False, "No earnings growth for PEG"
    peg = pe / (eps * 100) if eps > 0 else None
    if peg and peg > 3.0: return False, f"PEG {peg:.2f} too high"
    if price is None or price < 1: return False, "Price too low"
    if peg:
        return True, f"PEG {peg:.2f}"
    return True, "Passes Lynch"

def passes_minervini(stock):
    """VCP/SEPA: price > 50MA > 200MA, RSI 30-75, positive EPS"""
    price = _safe_float(stock.get("price"))
    ma50 = _safe_float(stock.get("ma50"))
    ma200 = _safe_float(stock.get("ma200"))
    rsi = _safe_float(stock.get("rsi"))
    eps = _safe_float(stock.get("eps_growth"))
    mcap = _safe_float(stock.get("mcap"))
    
    if price is None: return False, "No price"
    if ma50 and price < ma50: return False, "Price below 50-MA"
    if ma200 and price < ma200: return False, "Price below 200-MA"
    if rsi is not None and (rsi < 30 or rsi > 80): return False, f"RSI {rsi:.0f} outside range"
    if eps is not None and eps < 0: return False, "Negative EPS growth"
    if mcap and mcap < 50e6: return False, "Too small"
    return True, "Passes trend template"

def passes_qullamaggie(stock):
    """Episodic pivot: VR > 1.2, change > 2%, price > 5"""
    price = _safe_float(stock.get("price"))
    vr = _safe_float(stock.get("vol_ratio"))
    chg = _safe_float(stock.get("change_pct"))
    mcap = _safe_float(stock.get("mcap"))
    
    if price is None or price < 3: return False, "Price too low for momentum"
    if vr is None or vr < 1.0: return False, f"Volume ratio {vr} too low"
    if chg is None or abs(chg) < 1.0: return False, f"Change {chg}% too small"
    if mcap and mcap < 20e6: return False, "Too small for momentum"
    return True, f"VR {vr:.1f}x Chg {chg:+.1f}%"

def passes_david_ryan(stock):
    """Earnings acceleration: EPS > 10%, price > MA50, positive P/E"""
    price = _safe_float(stock.get("price"))
    eps = _safe_float(stock.get("eps_growth"))
    pe = _safe_float(stock.get("pe"))
    vr = _safe_float(stock.get("vol_ratio"))
    
    if eps is None or eps < 0.1: return False, "EPS growth too low"
    if pe is None or pe <= 0: return False, "No P/E"
    if price is None: return False, "No price"
    if vr is not None and vr < 0.5: return False, "Volume too light"
    return True, f"EPS {eps:.1f}% PE {pe:.0f}"

def passes_caruso(stock):
    """ATR-based: has price data, some volatility, positive direction"""
    price = _safe_float(stock.get("price"))
    chg = _safe_float(stock.get("change_pct"))
    
    if price is None or price < 2: return False, "Price too low"
    if chg is None: return False, "No change data"
    return True, "Passes volatility check"

def passes_shannon(stock):
    """AVWAP trend: price near MA50/200, clear trend structure"""
    price = _safe_float(stock.get("price"))
    ma50 = _safe_float(stock.get("ma50"))
    ma200 = _safe_float(stock.get("ma200"))
    
    if price is None: return False, "No price"
    if ma50 and abs(price - ma50) / ma50 > 0.3: return False, "Price too far from 50-MA"
    if ma200 and abs(price - ma200) / ma200 > 0.5: return False, "Price too far from 200-MA"
    return True, "Trend structure OK"

def passes_zanger(stock):
    """Corkscrew: high VR, significant change, price > 3"""
    price = _safe_float(stock.get("price"))
    vr = _safe_float(stock.get("vol_ratio"))
    chg = _safe_float(stock.get("change_pct"))
    
    if price is None or price < 3: return False, "Price too low"
    if vr is None or vr < 1.0: return False, "No unusual volume"
    if chg is None or abs(chg) < 0.5: return False, "Change too small"
    return True, f"VR {vr:.1f}x"

def passes_schmidt(stock):
    """Weekly SMA: price > MA50 > MA200 structure"""
    price = _safe_float(stock.get("price"))
    ma50 = _safe_float(stock.get("ma50"))
    ma200 = _safe_float(stock.get("ma200"))
    
    if price is None: return False, "No price"
    if ma50 and price < ma50: return False, "Price below 10-week SMA"
    if ma200 and price < ma200: return False, "Price below 30-week SMA"
    return True, "Price above both SMAs"

PERSONA_SCREENING = {
    "oneil": passes_oneil,
    "buffet": passes_buffett,
    "lynch": passes_lynch,
    "minervini": passes_minervini,
    "qullamaggie": passes_qullamaggie,
    "david-ryan": passes_david_ryan,
    "matt-caruso": passes_caruso,
    "brian-shannon": passes_shannon,
    "dan-zanger": passes_zanger,
    "nick-schmidt": passes_schmidt,
}

# ─── PHASE 2: ASSIGN STOCKS TO PERSONAS ──────────────────────────────────────

def assign_stocks_to_personas(flat_results):
    """
    Phase 2: Screen ALL valid stocks against EACH persona's documented criteria.
    Uses filter_by_persona_criteria for persona-specific methodology filters.
    Only assign stocks that PASS the persona's methodology.
    
    
    Returns dict of {persona_name: {ticker: stock_data}}
    """
    from collections import defaultdict
    
    valid_stocks = {t: d for t, d in flat_results.items() 
                    if d and "error" not in d and d.get("price")}
    
    persona_stocks = defaultdict(dict)
    assigned_tickers = set()  # FIX 4 — Track tickers already given to any persona
    
    for persona in PERSONAS:
        pass_count = 0
        fail_count = 0
        fail_reasons = {}
        for ticker, data in valid_stocks.items():
            passes, reason = filter_by_persona_criteria(persona, data)
            if passes:
                persona_stocks[persona][ticker] = data
                assigned_tickers.add(ticker)  # FIX 4 — Mark as assigned
                pass_count += 1
            else:
                fail_count += 1
                short_reason = reason.split(":")[0] if ":" in reason else reason[:30]
                fail_reasons[short_reason] = fail_reasons.get(short_reason, 0) + 1

        top_fails = sorted(fail_reasons.items(), key=lambda x: -x[1])[:5]
        fail_summary = ", ".join(f"{r}:{c}" for r, c in top_fails)
        print(f"[Arena]  Phase 2b: {persona}: {pass_count} PASS / {fail_count} FAIL (top fail reasons: {fail_summary})", flush=True)

    return dict(persona_stocks)





# ─── PHASE 3: SPAWN PERSONA SUBAGENTS VIA PROCESS POOL ──────────────────────

# Workers directory for individual persona analysis scripts
WORKERS_DIR = os.path.expanduser("~/.hermes/scripts/arena_workers")

PERSONA_WORKER_SCRIPT = os.path.join(WORKERS_DIR, "persona_worker.py")

# 21 indicators for reference
INDICATOR_NAMES = [
    "RSI", "MA50", "MA200", "MACD", "MACD_signal", "MACD_histogram",
    "BB_upper", "BB_middle", "BB_lower", "BB_width_pct",
    "ATR", "ATR_pct", "EMA9", "EMA21",
    "Volume", "Volume_ratio", "Stoch_K", "Stoch_D",
    "Price", "52w_high", "52w_low", "SMA20", "Change_pct"
]


def generate_persona_worker_script():
    """
    Write the persona_worker.py sub-script that handles individual persona analysis.
    Each worker:
      - Gets: stock list, SOUL.md content, yfinance data, TV indicator data
      - For EACH stock: writes 3000+ word analysis as individual file
      - Returns: list of {ticker, word_count, file_path}
    """
    os.makedirs(WORKERS_DIR, exist_ok=True)
    
    script = r'''#!/usr/bin/env python3
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
'''
    
    with open(PERSONA_WORKER_SCRIPT, "w") as f:
        f.write(script)
    os.chmod(PERSONA_WORKER_SCRIPT, 0o755)
    print(f"[Arena]  Worker script written: {PERSONA_WORKER_SCRIPT}", flush=True)

# ─── ACCURACY TRACKER ─────────────────────────────────────────────────────────

def run_accuracy_tracker():
    """Score last week's picks using current prices."""
    try:
        ACCURACY_DB = os.path.expanduser("~/.hermes_trading.db")
        if not os.path.isfile(ACCURACY_DB):
            print(f"[Arena]  Accuracy DB not found at {ACCURACY_DB}, skipping scoring", flush=True)
            return
        import sqlite3
        week_ago = (TODAY - datetime.timedelta(days=7)).isoformat()
        two_weeks_ago = (TODAY - datetime.timedelta(days=14)).isoformat()
        conn = sqlite3.connect(ACCURACY_DB)
        rows = conn.execute(
            "SELECT id, ticker, entry_price FROM accuracy_picks "
            "WHERE date >= ? AND date <= ? AND score_7d IS NULL",
            (two_weeks_ago, week_ago)
        ).fetchall()
        scored = 0
        for pick_id, ticker, entry_price in rows:
            try:
                stock = yf.Ticker(ticker)
                hist = stock.history(period="1mo")
                if not hist.empty:
                    current = float(hist['Close'].iloc[-1])
                    if entry_price and entry_price > 0:
                        score = (current / entry_price - 1) * 100
                        conn.execute(
                            "UPDATE accuracy_picks SET exit_price_7d = ?, score_7d = ? WHERE id = ?",
                            (current, round(score, 2), pick_id)
                        )
                        conn.commit()
                        scored += 1
            except Exception:
                pass
        conn.close()
        print(f"[Arena]  ✓ Accuracy scored {scored}/{len(rows)} picks from last week", flush=True)
    except Exception as e:
        print(f"[Arena]  ⚠️  Accuracy tracking failed (non-fatal): {e}", flush=True)


# ─── PHASE 3 ORCHESTRATOR ────────────────────────────────────────────────────

def run_persona_subprocess(persona, stocks, soul, indicators_all, tv_mcp_all, deepseek_key, deepseek_url):
    """
    Spawn a subprocess for one persona's stock analysis.
    Returns parsed JSON result or error dict.
    """
    params = {
        "persona": persona,
        "stocks": stocks,
        "soul": soul if soul else "",
        "date_str": DATE_STR,
        "comp_dir": COMP_DIR,
        "indicators": indicators_all,
        "tv_mcp": tv_mcp_all,
        "deepseek_key": deepseek_key or "",
        "deepseek_url": deepseek_url,
    }
    
    # Use hermes_env Python
    python_bin = os.path.expanduser("~/hermes_env/bin/python3")
    if not os.path.isfile(python_bin):
        python_bin = sys.executable
    
    try:
        proc = subprocess.Popen(
            [python_bin, PERSONA_WORKER_SCRIPT],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = proc.communicate(input=json.dumps(params), timeout=7200)
        
        if proc.returncode != 0:
            print(f"[Arena]  ❌ Worker for {persona} failed (exit={proc.returncode})", flush=True)
            if stderr:
                for line in stderr.split("\n")[-5:]:
                    if line.strip():
                        print(f"[{persona} ERR] {line.strip()}", flush=True)
            return {"persona": persona, "results": [], "error": f"exit {proc.returncode}"}
        
        # Parse result from stdout (after ---WORKER_RESULT--- marker)
        if "---WORKER_RESULT---" in stdout:
            result_json = stdout.split("---WORKER_RESULT---")[1].strip()
        else:
            result_json = stdout.strip().split("\n")[-1] if stdout.strip() else "{}"
        
        try:
            result = json.loads(result_json)
            return result
        except json.JSONDecodeError:
            print(f"[Arena]  ❌ Worker for {persona} returned invalid JSON", flush=True)
            return {"persona": persona, "results": [], "error": "invalid JSON"}
            
    except subprocess.TimeoutExpired:
        proc.kill()
        print(f"[Arena]  ❌ Worker for {persona} timed out (10 min)", flush=True)
        return {"persona": persona, "results": [], "error": "timeout"}
    except Exception as e:
        print(f"[Arena]  ❌ Worker for {persona} error: {e}", flush=True)
        return {"persona": persona, "results": [], "error": str(e)}


def run_all_personas(persona_stocks, indicators_all, tv_mcp_all):
    """
    Phase 3: Spawn persona subprocesses in batches of 3.
    Each subprocess writes individual stock analysis files.
    Returns aggregated results.
    """
    # Generate worker script if not exists
    if not os.path.isfile(PERSONA_WORKER_SCRIPT):
        generate_persona_worker_script()
    
    all_results = {}
    total_stocks_analyzed = 0
    total_words = 0
    
    persona_list = list(PERSONAS)
    n_personas = len(persona_list)
    
    for batch_start in range(0, n_personas, 3):
        batch = persona_list[batch_start:batch_start + 3]
        batch_num = (batch_start // 3) + 1
        n_batches = (n_personas + 2) // 3
        print(f"[Arena]  Phase 3 Batch {batch_num}/{n_batches}: {', '.join(batch)}", flush=True)
        
        futures = {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            for persona in batch:
                soul = load_persona_soul(persona)
                if not soul:
                    print(f"[Arena]  ⚠️  Skipping {persona} — no SOUL.md", flush=True)
                    all_results[persona] = {"persona": persona, "results": [], "error": "no SOUL.md"}
                
                # Skip personas with no assigned stocks
                if persona not in persona_stocks or not persona_stocks[persona]:
                    print(f"[Arena]  Skipping {persona} — no stocks passed screening", flush=True)
                    continue
                
                fut = pool.submit(
                    run_persona_subprocess,
                    persona, persona_stocks[persona], soul,
                    indicators_all, tv_mcp_all,
                    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL
                )
                futures[fut] = persona
            
            for fut in as_completed(futures):
                persona = futures[fut]
                try:
                    result = fut.result()
                    all_results[persona] = result
                    r = result.get("results", [])
                    persona_words = sum(ri.get("word_count", 0) for ri in r)
                    total_stocks_analyzed += len(r)
                    total_words += persona_words
                    print(f"[Arena]  ✓ {persona}: {len(r)} stocks analyzed, {persona_words} total words", flush=True)
                except Exception as e:
                    print(f"[Arena]  ❌ {persona} batch error: {e}", flush=True)
                    all_results[persona] = {"persona": persona, "results": [], "error": str(e)}
    
    print(f"[Arena]  Phase 3 complete: {total_stocks_analyzed} total stocks, {total_words} total words", flush=True)
    return all_results


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def scan_global_markets():
    """
    Phase 1: TV scanner + yfinance → global data.
    Returns (market_by_region, flat_results).
    """
    print(f"[Arena]  Phase 1: Scanning global markets...", flush=True)
    market_by_region, flat_results, from_cache = load_or_scan_market_data()
    
    if flat_results:
        flat_results = {t: d for t, d in flat_results.items() if d and "error" not in d}
    
    # Also compute TV MCP indicators for enriched data
    top_tickers = sorted(
        [v for v in flat_results.values() if v and "error" not in v],
        key=lambda x: (x.get("vol_ratio", 0) or 0) * (abs(x.get("change_pct", 0)) or 0),
        reverse=True,
    )[:50]
    top_ticker_symbols = [s["ticker"] for s in top_tickers if "ticker" in s]
    
    tv_mcp_data = {}
    if top_ticker_symbols:
        print(f"[Arena]  Phase 1b: Reading TV MCP indicators for top tickers...", flush=True)
        tv_mcp_data = read_tv_mcp_indicators(top_ticker_symbols)
        if tv_mcp_data:
            for ticker, mcp_vals in tv_mcp_data.items():
                if ticker in flat_results and flat_results[ticker]:
                    flat_results[ticker]["tv_mcp_indicators"] = mcp_vals
            print(f"[Arena]  TV MCP enhanced {len(tv_mcp_data)} tickers", flush=True)
    
    total_tickers = len(flat_results)
    print(f"[Arena]  Phase 1 complete: {total_tickers} tickers across {len(market_by_region)} regions", flush=True)
    
    return market_by_region, flat_results, tv_mcp_data


# FIX 7 — Direct Yahoo v8/v7 API calls (10-15x faster than yfinance)
_YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}


def yahoo_v8_history(ticker, range_str="1y", interval="1d"):
    """Fetch historical price data via Yahoo Finance v8 chart API."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range={range_str}&interval={interval}"
    try:
        resp = requests.get(url, headers=_YAHOO_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            return None
        r = result[0]
        timestamps = r.get("timestamp", [])
        quotes = r.get("indicators", {}).get("quote", [])
        if not quotes or not timestamps:
            return None
        q = quotes[0]
        return {
            "timestamp": timestamps,
            "open": q.get("open", []),
            "high": q.get("high", []),
            "low": q.get("low", []),
            "close": q.get("close", []),
            "volume": q.get("volume", []),
        }
    except Exception:
        return None


def yahoo_v7_quote(ticker):
    """Fetch real-time quote data via Yahoo Finance v7 quote API."""
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={ticker}"
    try:
        resp = requests.get(url, headers=_YAHOO_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        result = data.get("quoteResponse", {}).get("result", [])
        if not result:
            return None
        q = result[0]
        return {
            "price": q.get("regularMarketPrice") or q.get("previousClose"),
            "marketCap": q.get("marketCap"),
            "trailingPE": q.get("trailingPE"),
            "forwardPE": q.get("forwardPE"),
            "trailingEps": q.get("trailingEps"),
            "forwardEps": q.get("forwardEps"),
            "earningsQuarterlyGrowth": q.get("earningsQuarterlyGrowth"),
            "sector": q.get("sector"),
            "beta": q.get("beta"),
            "dividendYield": q.get("dividendYield"),
            "volume": q.get("regularMarketVolume"),
            "change_pct": q.get("regularMarketChangePercent"),
        }
    except Exception:
        return None


def _yf_fetch_with_retry(ticker_str, operation="history", period="1y"):
    """Fetch yfinance data with retry logic: try once, wait 1s, retry once."""
    with _yf_lock:
        stock = yf.Ticker(ticker_str)
        for attempt in range(2):
            try:
                if operation == "info":
                    return stock.info or {}
                return stock.history(period=period)
            except Exception:
                if attempt == 0:
                    time.sleep(1)
                else:
                    raise
    return None


def main():
    start_time = time.monotonic()

    print(f"[Arena]  === ARENA RUNNER — {DATE_STR} ===", flush=True)
    print(f"[Arena]  Architecture: Phase 1/2/3 — Individual stock files per persona", flush=True)

    # Resolve API key
    _resolve_api_key()
    if not DEEPSEEK_API_KEY:
        print("[Arena]  ⚠️  No DeepSeek API key configured. Analyses will use yfinance data only.", flush=True)

    # ─── MODE CHECK ───────────────────────────────────────────────────
    # TARGET_STOCKS mode: skip Phase 1 (TV scanner) and use TARGET_STOCK_LIST directly.
    # TV_SCAN mode: normal full pipeline (Phase 1 + 2 + 3).
    if MODE.upper() == "TARGET_STOCKS":
        print(f"[Arena]  MODE=TARGET_STOCKS — skipping Phase 1 (TV scanner), using {len(TARGET_STOCK_LIST)} target stocks", flush=True)
        tv_mcp_data = {}
        # Build flat_results using analyze_ticker() for full fundamentals (PE, EPS, RSI, MA50, MA200, etc.)
        flat_results = {}
        for ticker in TARGET_STOCK_LIST:
            try:
                result = analyze_ticker(ticker)
                if result:
                    flat_results[ticker] = result
                else:
                    print(f"[Arena]  ⚠ {ticker} — yfinance returned no data, skipping", flush=True)
            except Exception as e:
                print(f"[Arena]  ⚠ {ticker} — yfinance error: {e}", flush=True)
        print(f"[Arena]  Built flat_results for {len(flat_results)} tickers from TARGET_STOCK_LIST (with full fundamentals)", flush=True)
    else:
        # ─── PHASE 1: TV Scanner + yfinance + TV MCP ──────────────────
        market_by_region, flat_results, tv_mcp_data = scan_global_markets()

    # ─── PHASE 2: Assign stocks to personas ───────────────────────────
    print(f"[Arena]  Phase 2: Assigning stocks to {len(PERSONAS)} personas...", flush=True)
    persona_stocks = assign_stocks_to_personas(flat_results)
    
    # Compute indicators for all assigned stocks
    all_assigned_tickers = set()
    for p in persona_stocks:
        all_assigned_tickers.update(persona_stocks[p].keys())
    print(f"[Arena]  Total unique tickers across all personas: {len(all_assigned_tickers)}", flush=True)
    
    # Compute full indicators for assigned tickers
    print(f"[Arena]  Computing full 21 indicators for {len(all_assigned_tickers)} assigned tickers...", flush=True)
    indicators_all = compute_indicators_batch(list(all_assigned_tickers))
    if indicators_all:
        print(f"[Arena]  Indicators computed for {len(indicators_all)} tickers", flush=True)

    # ─── PHASE 3: Spawn persona analyses in batches of 3 ─────────────
    print(f"[Arena]  Phase 3: Running persona analyses in batches (max 3 concurrent)...", flush=True)
    all_results = run_all_personas(persona_stocks, indicators_all, tv_mcp_data)

    # Accuracy tracker
    run_accuracy_tracker()

    # Summary
    elapsed = time.monotonic() - start_time
    print(f"\n[Arena]  ✅ Arena complete in {elapsed:.0f}s — {DATE_STR}", flush=True)

    total_files = 0
    total_words = 0
    for persona, result in all_results.items():
        r = result.get("results", [])
        total_files += len(r)
        total_words += sum(ri.get("word_count", 0) for ri in r)
    print(f"[Arena]  Total: {total_files} stock files, {total_words} words", flush=True)

    if os.path.isdir(COMP_DIR):
        persona_dirs = [d for d in os.listdir(COMP_DIR) if os.path.isdir(os.path.join(COMP_DIR, d))]
        print(f"[Arena]  Output dirs: {len(persona_dirs)} persona folders in {COMP_DIR}", flush=True)
        for d in sorted(persona_dirs):
            pdir = os.path.join(COMP_DIR, d)
            files = [f for f in os.listdir(pdir) if DATE_STR in f]
            if files:
                print(f"[Arena]    {d}/: {len(files)} files", flush=True)


if __name__ == "__main__":
    main()
