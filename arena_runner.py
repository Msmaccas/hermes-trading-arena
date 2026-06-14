#!/usr/bin/env python3
"""
arena_runner.py — Self-contained unified weekly trading competition engine.
MASTER entry point for the weekly trading competition.
Runs autonomously as a no_agent cron job (Sunday 8AM SGT, script=arena_runner.py).

THE CRITICAL DIFFERENCE: No hardcoded ticker lists. Scans ALL stocks in each market
via TradingView scanner API (8 regions, up to 800 stocks), then computes indicators
via yfinance for the entire universe.

Output:
  10_Trading/Competition/{Persona} - YYYY-MM-DD.md  (Obsidian vault)
"""

import os, sys, datetime, json, time, warnings, traceback, re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

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

# ─── 8 MARKET REGIONS — TradingView scanner regions ──────────────────────────

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


# ─── CONFIG LOADING ───────────────────────────────────────────────────────────

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


# ─── TV CDP HANDSHAKE (Fix 1) ────────────────────────────────────────────────

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
    Scan all 8 market regions via TradingView scanner API.
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

    Edge cases handled:
    - UK: replace embedded dots with dash (BT.A -> BT-A.L), strip trailing dots (NG. -> NG.L)
    - Hong Kong: strip leading zeros from 5-digit codes (09988 -> 9988.HK)
    - Everything else: append the region's exchange suffix
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
    """
    # Ticker is already converted to yfinance format by caller (load_or_scan_market_data)
    try:
        stock = yf.Ticker(ticker_str)
        hist = stock.history(period="1y")
        info = stock.info or {}
        if hist.empty or len(hist) < 20:
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
        eps_growth = info.get("earningsQuarterlyGrowth")
        sector = info.get("sector", "Unknown")

        return {
            "ticker": ticker_str,
            "price": round(price, 2),
            "change_pct": change_pct,
            "rsi": rsi,
            "ma50": round(ma50, 2) if ma50 else None,
            "ma200": round(ma200, 2) if ma200 else None,
            "vol_ratio": vol_ratio,
            "pe": pe,
            "eps_growth": eps_growth,
            "mcap": mcap,
            "sector": sector,
        }
    except Exception as e:
        return {"ticker": ticker_str, "error": str(e)}


def scan_market_data(ticker_list):
    """
    Scan all tickers using yfinance + ThreadPoolExecutor(max_workers=6).
    Returns a dict of {ticker: data_dict}.
    """
    results = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
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


# ─── TV MCP INDICATOR READING ────────────────────────────────────────────────

# Chrome CDP helpers for TradingView fallback
_CHROME_CDP = "http://127.0.0.1:9222"
_TV_CHART_URL = "https://www.tradingview.com/chart/"


def _chrome_list_tabs():
    """List open tabs via Chrome DevTools Protocol. Returns list of tab dicts."""
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
    """
    Evaluate JavaScript in a specific CDP tab via WebSocket.
    Uses CDP Runtime.evaluate.
    """
    import json as _json
    try:
        # Fetch the WebSocket URL for this tab
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
    """
    Use Chrome CDP to navigate to a TradingView chart for the given ticker,
    then extract visible study/indicator values from the page via JS injection.
    Returns dict of study values or None on failure.
    """
    try:
        # Check for websocket-client availability
        try:
            import websocket
        except ImportError:
            print(f"[Arena]  websocket-client not installed, skipping Chrome CDP reading", flush=True)
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

        # Inject JS to extract study/indicator values
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
    Read TradingView indicator values for the top tickers using a cascade:
    1. Try TV Desktop CDP (port 1234) with data_get_pine_lines/labels and capture_screenshot
    2. Compute standard indicators (RSI, MA50/200, MACD, BB, ATR) locally via yfinance + numpy
    3. Cycle 21 indicators 2-at-a-time via chart_manage_indicator on free TV plan

    Returns dict of {ticker: {indicator_name: value, ...}} or empty dict.
    """
    # Ensure hermes_env venv is on sys.path for websocket-client
    import sys as _sys
    _venv_site = os.path.expanduser('~/hermes_env/lib/python3.12/site-packages')
    if os.path.isdir(_venv_site) and _venv_site not in _sys.path:
        _sys.path.insert(0, _venv_site)

    # ─── Local indicator computation via yfinance + numpy ───────────────
    def _compute_local_indicators(ticker):
        """Compute standard indicators locally for a single ticker."""
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="6mo")
            if hist.empty or len(hist) < 30:
                return None
            close = hist["Close"].values
            high = hist["High"].values
            low = hist["Low"].values
            volume = hist["Volume"].values

            result = {}
            price = float(close[-1])

            # RSI(14)
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

            # MA50 / MA200
            if len(close) >= 50:
                result["MA50"] = round(float(np.mean(close[-50:])), 2)
            if len(close) >= 200:
                result["MA200"] = round(float(np.mean(close[-200:])), 2)

            # MACD (12, 26, 9)
            if len(close) >= 26:
                ema12 = _ema(close, 12)
                ema26 = _ema(close, 26)
                macd_line = ema12 - ema26
                signal = _ema(macd_line, 9)
                result["MACD"] = round(float(macd_line[-1]), 4)
                result["MACD_signal"] = round(float(signal[-1]), 4)
                result["MACD_histogram"] = round(float(macd_line[-1] - signal[-1]), 4)

            # Bollinger Bands (20, 2)
            if len(close) >= 20:
                sma20 = np.mean(close[-20:])
                std20 = np.std(close[-20:])
                result["BB_upper"] = round(float(sma20 + 2 * std20), 2)
                result["BB_middle"] = round(float(sma20), 2)
                result["BB_lower"] = round(float(sma20 - 2 * std20), 2)
                result["BB_width"] = round(float(4 * std20 / sma20 * 100), 2) if sma20 != 0 else 0

            # ATR(14)
            if len(high) >= 15:
                tr = np.maximum(high[1:] - low[1:],
                                np.maximum(np.abs(high[1:] - close[:-1]),
                                           np.abs(low[1:] - close[:-1])))
                result["ATR"] = round(float(np.mean(tr[-14:])), 4)
                result["ATR_pct"] = round(result["ATR"] / price * 100, 2) if price > 0 else 0

            # EMA 9, 21, 50, 200
            for period in [9, 21]:
                if len(close) >= period:
                    result[f"EMA{period}"] = round(float(_ema(close, period)[-1]), 2)

            # Volume
            if len(volume) >= 20:
                avg_vol = np.mean(volume[-20:])
                result["Volume"] = int(volume[-1])
                result["Volume_ratio"] = round(float(volume[-1] / avg_vol), 2) if avg_vol > 0 else 1.0

            # Stochastic (14,3)
            if len(close) >= 14:
                low14 = np.min(low[-14:])
                high14 = np.max(high[-14:])
                k = (close[-1] - low14) / (high14 - low14) * 100 if (high14 - low14) > 0 else 50
                result["Stoch_K"] = round(float(k), 2)
                # Simplified D (3-period smoothing)
                if len(close) >= 16:
                    k_vals = []
                    for i in range(-3, 0):
                        l14 = np.min(low[-14 + i:i])
                        h14 = np.max(high[-14 + i:i])
                        kv = (close[i] - l14) / (h14 - l14) * 100 if (h14 - l14) > 0 else 50
                        k_vals.append(kv)
                    result["Stoch_D"] = round(float(np.mean(k_vals)), 2)

            result["Price"] = round(price, 2)
            return result
        except Exception:
            return None

    def _ema(values, period):
        """Compute exponential moving average."""
        weights = np.exp(np.linspace(-1, 0, period))
        weights /= weights.sum()
        return np.convolve(values, weights, mode='valid')

    # ─── TV CDP: data_get_pine_lines / data_get_pine_labels / capture_screenshot ──
    def _tv_cdp_read(ticker):
        """Read from TV Desktop CDP using pine lines, labels, and screenshot."""
        try:
            # Navigate to ticker
            resp = requests.get(
                f"{TV_CDP_URL}/navigate",
                params={"symbol": ticker},
                timeout=10,
            )
            if resp.status_code != 200:
                return None
            time.sleep(1)

            data = {"ticker": ticker, "source": "tv_cdp"}

            # Get pine lines (price levels, support/resistance, indicators plotted as lines)
            try:
                r_lines = requests.get(
                    f"{TV_CDP_URL}/mcp_tradingview_data_get_pine_lines",
                    timeout=10,
                )
                if r_lines.status_code == 200:
                    lines_data = r_lines.json()
                    data["pine_lines"] = lines_data
            except Exception:
                pass

            # Get pine labels (annotated levels)
            try:
                r_labels = requests.get(
                    f"{TV_CDP_URL}/mcp_tradingview_data_get_pine_labels",
                    timeout=10,
                )
                if r_labels.status_code == 200:
                    labels_data = r_labels.json()
                    data["pine_labels"] = labels_data
            except Exception:
                pass

            # Capture screenshot for visual chart data
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

            # ─── Free TV plan: cycle indicators 2 at a time ─────────────
            STANDARD_INDICATORS = [
                "RSI", "MACD", "Bollinger Bands", "Moving Average Exponential",
                "Volume", "ATR", "Stochastic", "Moving Average", "Ichimoku Cloud",
                "Parabolic SAR", "Commodity Channel Index", "On Balance Volume",
                "Money Flow Index", "Williams %R", "Awesome Oscillator",
                "Chaikin Money Flow", "Rate of Change", "Elder-Ray Index",
                "Keltner Channels", "Donchian Channels", "VWAP",
            ]

            # Add EMA periods and MA periods config
            EMA_PERIODS = "9,21,50,200"
            MA_PERIODS = "50,200"

            read_indicators = []
            # Cycle through 2 at a time
            for i in range(0, len(STANDARD_INDICATORS), 2):
                pair = STANDARD_INDICATORS[i:i+2]
                indicator_data = {}
                for ind_name in pair:
                    try:
                        # Configure indicator input if needed
                        if ind_name == "Moving Average Exponential":
                            params = {"name": ind_name, "length": EMA_PERIODS}
                        elif ind_name == "Moving Average":
                            params = {"name": ind_name, "length": MA_PERIODS}
                        else:
                            params = {"name": ind_name}

                        # Add indicator
                        r_add = requests.post(
                            f"{TV_CDP_URL}/chart_manage_indicator",
                            json={"action": "add", **params},
                            timeout=10,
                        )
                        if r_add.status_code == 200:
                            time.sleep(0.5)
                            read_indicators.append(ind_name)
                            # Try to read its values via pine lines
                            try:
                                r_lines2 = requests.get(
                                    f"{TV_CDP_URL}/mcp_tradingview_data_get_pine_lines",
                                    timeout=10,
                                )
                                if r_lines2.status_code == 200:
                                    indicator_data[ind_name] = r_lines2.json()
                            except Exception:
                                pass

                            # Remove indicator before adding next pair
                            try:
                                requests.post(
                                    f"{TV_CDP_URL}/chart_manage_indicator",
                                    json={"action": "remove", "name": ind_name},
                                    timeout=5,
                                )
                            except Exception:
                                pass
                    except Exception:
                        pass

                if indicator_data:
                    data[f"indicators_{i//2}"] = indicator_data

            data["indicators_read"] = read_indicators
            data["indicators_count"] = len(read_indicators)
            print(f"[Arena]  TV CDP: read {len(read_indicators)} indicators for {ticker}", flush=True)

            return data

        except Exception:
            return None

    # ─── Main cascade ─────────────────────────────────────────────────
    enhanced = {}
    successful_tickers = 0

    # First, compute local indicators for ALL tickers (reliable baseline)
    print(f"[Arena]  Computing local indicators for {len(ticker_list)} tickers via yfinance...", flush=True)
    local_results = {}
    for ticker in ticker_list[:50]:
        loc = _compute_local_indicators(ticker)
        if loc:
            local_results[ticker] = loc
    if local_results:
        print(f"[Arena]  Local indicators computed for {len(local_results)} tickers", flush=True)

    # Attempt TV Desktop CDP for enhanced data
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

    # Merge local indicators as baseline, overlay TV CDP data on top
    merged = {}
    for ticker in ticker_list[:50]:
        entry = {}
        # Start with local indicators
        if ticker in local_results:
            entry["local_indicators"] = local_results[ticker]
        # Overlay TV CDP enhanced data
        if ticker in enhanced:
            entry["tv_cdp"] = enhanced[ticker]
        if entry:
            merged[ticker] = entry

    if merged:
        print(f"[Arena]  TV indicators: merged data for {len(merged)} tickers", flush=True)
        return merged

    # Fallback: return just local indicators if TV failed
    if local_results:
        print(f"[Arena]  TV CDP failed, returning local yfinance indicators only", flush=True)
        result = {}
        for ticker, loc in local_results.items():
            result[ticker] = {"local_indicators": loc}
        return result

    print(f"[Arena]  All indicator routes failed", flush=True)
    return {}

# ─── CACHE / LOAD ─────────────────────────────────────────────────────────────

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
            # Build a flat results dict from by_region
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

    # Step 1: TV scanner — get all tickers across 8 regions
    tv_by_region, all_ticker_pairs = global_tv_scan()
    # Convert each (bare_ticker, region) pair to yfinance format
    raw_to_yf = {}  # mapping: yf_ticker -> (bare_ticker, region) for cache
    yf_ticker_set = set()
    for bare_ticker, region in all_ticker_pairs:
        yf_ticker = convert_tv_ticker(bare_ticker, region)
        yf_ticker_set.add(yf_ticker)
        if yf_ticker not in raw_to_yf:
            raw_to_yf[yf_ticker] = (bare_ticker, region)
    ticker_list = sorted(yf_ticker_set)
    print(f"[Arena]  Total unique tickers from TV scan: {len(ticker_list)}", flush=True)

    # Skip known penny stocks and ETFs by checking ticker patterns
    # (Numeric-only tickers now carry exchange suffixes so they won't be wrongly filtered)
    ticker_list = [t for t in ticker_list if not (t[0].isdigit() and not any(t.endswith(suf) for suf in [".L", ".T", ".KS", ".NS", ".SA", ".HK", ".TW", ".IS", ".VN"]))]
    print(f"[Arena]  After ticker filter: {len(ticker_list)} tickers", flush=True)

    # Sort so real stock names come first (not numerical tickers)
    ticker_list.sort(key=lambda t: (t[0].isdigit(), t))
    print(f"[Arena]  First 5 tickers: {ticker_list[:5]}", flush=True)

    # Limit yfinance calls to prevent rate limiting
    MAX_YF_TICKERS = 300
    if len(ticker_list) > MAX_YF_TICKERS:
        ticker_list = ticker_list[:MAX_YF_TICKERS]
        print(f"[Arena]  Limiting to {MAX_YF_TICKERS} tickers for yfinance", flush=True)

    # Step 2: yfinance — compute indicators for all tickers
    print(f"[Arena]  Computing indicators via yfinance for {len(ticker_list)} tickers...", flush=True)
    raw_results = scan_market_data(ticker_list)

    # Step 3: Build by_region from TV scan results + yfinance data
    by_region = {}
    for region, tickers in tv_by_region.items():
        region_data = []
        for bare_t in tickers:
            yf_t = convert_tv_ticker(bare_t, region)
            if yf_t in raw_results and raw_results[yf_t]:
                region_data.append(raw_results[yf_t])
        if region_data:
            by_region[region] = region_data

    # Step 4: Cache the results (store region info so suffix conversion can be reapplied from cache)
    # Store the yf_ticker, bare_ticker, and region in each result for cache reconstruction
    # We augment the data dicts with metadata for cache-only use
    try:
        cache_by_region = {}
        for region, stocks in by_region.items():
            region_cache = []
            for s in stocks:
                if s and "ticker" in s:
                    entry = dict(s)
                    # Find the bare ticker for this yf_ticker
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

def call_deepseek(system_prompt, user_message, timeout=120):
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
        "max_tokens": 4096,
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


# ─── PERSISTENCE ──────────────────────────────────────────────────────────────

def save_persona_analysis(persona_name, analysis_text, top_picks, web_research_summary=None):
    """
    Save the persona's analysis to Obsidian vault.
    Path: 10_Trading/Competition/{Persona} - YYYY-MM-DD.md
    """
    filename = f"{persona_name} - {DATE_STR}.md"
    filepath = os.path.join(COMP_DIR, filename)

    os.makedirs(COMP_DIR, exist_ok=True)

    header = f"# {persona_name.title()} — {DATE_STR}\n\n"

    content = header
    content += "**Competition Analysis**\n\n"

    if top_picks:
        content += "## Top Picks\n\n"
        for i, pick in enumerate(top_picks[:5], 1):
            content += f"{i}. **{pick.get('ticker', '?')}**"
            if pick.get("price"):
                content += f" — ${pick['price']}"
            if pick.get("target"):
                content += f" → Target: ${pick['target']}"
            if pick.get("reason"):
                content += f"\n   *{pick['reason']}*"
            content += "\n\n"

    content += "## Analysis\n\n"
    content += analysis_text

    if web_research_summary:
        content += "\n\n## Web Research — Top Pick Catalysts\n\n"
        content += web_research_summary

    content += f"\n\n---\n*Generated {DATE_STR} via Arena Runner*"

    try:
        with open(filepath, "w") as f:
            f.write(content)
        file_size = os.path.getsize(filepath)
        print(f"[Arena]  ✓ Saved: {filename} ({file_size//1024}KB)", flush=True)
        return True
    except Exception as e:
        print(f"[Arena]  ❌ Failed to save {filename}: {e}", flush=True)
        return False


# ─── WEB RESEARCH ─────────────────────────────────────────────────────────────

def perform_web_research(ticker, persona_name):
    """
    Search for recent news/catalysts for a given ticker using web search.
    Returns a short summary string (or None).
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

        # Fallback: try a simple requests-based search
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


# ─── PERSONA ANALYSIS PIPELINE ────────────────────────────────────────────────

def extract_top_picks(analysis_text, prices_map=None):
    """
    Heuristically extract top 3-5 picks from an LLM analysis text.
    Returns a list of dicts: [{"ticker": "...", "price": ..., "target": ..., "reason": "..."}, ...]
    """
    picks = []
    if not analysis_text:
        return picks

    # Look for numbered lists referencing tickers
    lines = analysis_text.split("\n")
    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = re.match(r'^[\d\*\-\.\s#\)]+([A-Z]{1,5}(?:\.[A-Z]{2,5})?)\b', line)
        if m:
            ticker = m.group(1)
            skip_words = {"THE", "THIS", "THAT", "WITH", "FROM", "THEN", "WILL",
                          "HAVE", "BEEN", "CAN", "ALL", "ARE", "NOT", "FOR", "AND",
                          "ITS", "HAS", "WAS", "BUT", "YOU", "YOUR", "WHAT", "WHEN",
                          "WHY", "HOW", "WHO", "WHOM", "WHICH"}
            if ticker in skip_words:
                continue
            target = None
            price = None
            reason = line

            target_m = re.search(r'(?:target|price target|TP)[:\s]*\$?([\d,.]+)', line, re.IGNORECASE)
            if target_m:
                try:
                    target = float(target_m.group(1).replace(",", ""))
                except ValueError:
                    pass

            if prices_map and ticker in prices_map:
                pd = prices_map[ticker]
                if pd and "price" in pd and pd["price"]:
                    price = pd["price"]

            picks.append({
                "ticker": ticker,
                "price": price,
                "target": target,
                "reason": reason.strip(),
            })

    return picks[:5]


def run_persona_analysis(persona_name, market_json, prices_map):
    """
    Run analysis for a single persona.
    1. Load SOUL.md
    2. Call DeepSeek API
    3. Extract top picks
    4. Web research for top pick
    5. Save to Obsidian
    """
    soul_md = load_persona_soul(persona_name)
    if not soul_md:
        print(f"[Arena]  ⚠️  Skipping {persona_name} — no SOUL.md found", flush=True)
        return False

    market_data_str = json.dumps(market_json, indent=2, default=str)
    truncated = market_data_str[:8000] if len(market_data_str) > 8000 else market_data_str

    user_msg = (
        f"Here is the global market scan data for today. "
        f"Analyze using your exact methodology. "
        f"Identify your top 3-5 picks with specific price targets. "
        f"Use your exact voice.\n\n"
        f"Market Data:\n{truncated}"
    )

    print(f"[Arena]  🧠 Running {persona_name} analysis...", flush=True)

    analysis_text = call_deepseek(soul_md, user_msg)
    if not analysis_text:
        print(f"[Arena]  ❌ {persona_name} analysis failed", flush=True)
        return False

    top_picks = extract_top_picks(analysis_text, prices_map)

    # Web research for top pick only
    web_research = None
    if top_picks:
        top_ticker = top_picks[0]["ticker"]
        print(f"[Arena]  🔍 Web research for {persona_name}'s top pick: {top_ticker}", flush=True)
        web_research = perform_web_research(top_ticker, persona_name)

    save_persona_analysis(persona_name, analysis_text, top_picks, web_research)
    return True


def run_all_personas(market_json, prices_map):
    """
    Run analysis for all 10 personas sequentially.
    Each persona gets the full market context.
    Returns (success_count, fail_count).
    """
    success = 0
    fail = 0

    for persona in PERSONAS:
        ok = run_persona_analysis(persona, market_json, prices_map)
        if ok:
            success += 1
        else:
            fail += 1

    return success, fail


# ─── ACCURACY TRACKER ─────────────────────────────────────────────────────────

def run_accuracy_tracker():
    """
    Call accuracy_tracker functionality within try/except.
    Scores picks from the previous week using current prices.
    """
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


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    start_time = time.monotonic()

    print(f"[Arena]  === ARENA RUNNER — {DATE_STR} ===", flush=True)

    # Resolve API key
    _resolve_api_key()
    if not DEEPSEEK_API_KEY:
        print("[Arena]  ⚠️  No DeepSeek API key configured. Analyses will use local processing only.", flush=True)

    # ─── STEP 1: Fundamentals FIRST (yfinance for ALL tickers) ──────────
    print(f"[Arena]  Step 1/3: Fetching fundamentals via yfinance...", flush=True)
    market_by_region, flat_results, from_cache = load_or_scan_market_data()
    # FIX 5: Deduplicate flat_results tickers
    if flat_results:
        flat_results = {t: d for t, d in flat_results.items() if d and "error" not in d}
        print(f"[Arena]  Valid tickers after dedup: {len(flat_results)}", flush=True)

    # Build market_json for LLM context
    market_json = {
        "date": DATE_STR,
        "by_region": market_by_region,
        "summary": {},
    }
    for region, stocks in market_by_region.items():
        changes = [s.get("change_pct", 0) for s in stocks if s and s.get("change_pct") is not None]
        rsis = [s.get("rsi") for s in stocks if s and s.get("rsi") is not None]
        market_json["summary"][region] = {
            "count": len(stocks),
            "avg_change": round(sum(changes) / len(changes), 2) if changes else 0,
            "avg_rsi": round(sum(rsis) / len(rsis), 1) if rsis else 0,
        }

    total_tickers = sum(len(v) for v in market_by_region.values())
    print(f"[Arena]  Fundamentals collected: {total_tickers} tickers across {len(market_by_region)} regions", flush=True)

    # ─── STEP 2: TV MCP for technicals (if fundamentals succeeded) ─────
    print(f"[Arena]  Step 2/3: Connecting to TV MCP for enhanced technicals...", flush=True)
    sorted_by_activity = sorted(
        [v for v in flat_results.values() if v and "error" not in v],
        key=lambda x: (x.get("vol_ratio", 0) or 0) * (abs(x.get("change_pct", 0)) or 0),
        reverse=True,
    )
    top_tickers = [s["ticker"] for s in sorted_by_activity[:50] if "ticker" in s]
    if top_tickers:
        mcp_data = read_tv_mcp_indicators(top_tickers)
        if mcp_data:
            for ticker, mcp_vals in mcp_data.items():
                if ticker in flat_results and flat_results[ticker]:
                    flat_results[ticker]["tv_mcp_indicators"] = mcp_vals
            print(f"[Arena]  TV MCP enhanced {len(mcp_data)} tickers with technical indicators", flush=True)
        else:
            print(f"[Arena]  TV MCP returned no data (fundamentals already collected, continuing)", flush=True)
    else:
        print(f"[Arena]  No active tickers to enhance via TV MCP", flush=True)

    # ─── STEP 3: Generate analysis ─────────────────────────────────────
    print(f"[Arena]  Step 3/3: Running {len(PERSONAS)} persona analyses...", flush=True)
    success, fail = run_all_personas(market_json, flat_results)
    print(f"[Arena]  Persona analyses: {success} ok, {fail} failed", flush=True)

    # Accuracy tracker
    run_accuracy_tracker()

    # Summary
    elapsed = time.monotonic() - start_time
    print(f"\n[Arena]  ✅ Arena complete in {elapsed:.0f}s — {DATE_STR}", flush=True)

    if os.path.isdir(COMP_DIR):
        out_files = [f for f in os.listdir(COMP_DIR) if DATE_STR in f]
        print(f"[Arena]  Output: {len(out_files)} files in {COMP_DIR}", flush=True)


if __name__ == "__main__":
    main()
