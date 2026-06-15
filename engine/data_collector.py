"""
engine/data_collector.py — Market data fetching layer.
TV scanner -> Direct Yahoo API (v8/v7) -> yfinance fallback -> cache.
"""

import json, os, time
import numpy as np
import pandas as pd
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from engine.config import (TV_MARKETS, EXCHANGE_SUFFIXES, YAHOO_HEADERS,
                           CACHE_PATH, DATE_STR, YF_MAX_WORKERS, YF_RETRY_ATTEMPTS)

_yf_lock = Lock()
V8_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}?range={}&interval={}"
V7_URL = "https://query1.finance.yahoo.com/v7/finance/quote?symbols={}"
TV_SCAN = "https://scanner.tradingview.com/{}/scan"
TV_COLS = ["name","close","change","change_abs","volume","description",
           "Recommend.All","RSI","BB.upper","BB.lower","EMA50","EMA200","SMA50","SMA200","Volatility.D"]


def convert_tv_ticker(ticker, region):
    """Convert TradingView ticker to yfinance-compatible format."""
    if region in ("US", "China"):
        return ticker
    m = {"UK": ".L", "Japan": ".T", "Korea": ".KS", "India": ".NS",
         "Brazil": ".SA", "Hong_Kong": ".HK", "Taiwan": ".TW", "Turkey": ".IS", "Vietnam": ".VN"}
    if region == "UK":
        ticker = ticker.rstrip(".").replace(".", "-")
        return ticker + ".L"
    if region == "Hong_Kong":
        while len(ticker) > 4 and ticker.startswith("0"):
            ticker = ticker[1:]
        return ticker + ".HK"
    return ticker + m.get(region, "")


def tv_scan_region(region, sort_by="volume", rng=100):
    try:
        resp = requests.post(TV_SCAN.format(region), json={
            "columns": TV_COLS,
            "sort": {"sortBy": sort_by, "sortOrder": "desc"},
            "range": [0, rng],
            "filter": [{"left": "change", "operation": "greater", "right": 0}],
        }, timeout=30)
        resp.raise_for_status()
        return [dict(zip(TV_COLS[:len(i.get("d",[]))], i["d"]))
                for i in resp.json().get("data",[])]
    except Exception as e:
        print(f"[Data]  ⚠ TV scan failed for {region}: {e}", flush=True)
        return []


def tv_scan_tickers(region):
    seen = set()
    for s in tv_scan_region(region, "volume") + tv_scan_region(region, "change"):
        n = s.get("name", "")
        if n:
            seen.add(n)
    return seen


def global_tv_scan():
    by_region, all_pairs = {}, []
    for name, tv_region in TV_MARKETS.items():
        tickers = tv_scan_tickers(tv_region)
        by_region[name] = list(tickers)
        for t in tickers:
            all_pairs.append((t, name))
        print(f"[Data]  TV scan: {name} -> {len(tickers)} tickers", flush=True)
    return by_region, all_pairs


def yahoo_v8_history(ticker, rng="1y", interval="1d"):
    try:
        resp = requests.get(V8_URL.format(ticker, rng, interval), headers=YAHOO_HEADERS, timeout=15)
        resp.raise_for_status()
        r = resp.json().get("chart", {}).get("result", [None])[0]
        if not r or not r.get("timestamp"): return None
        q = r.get("indicators", {}).get("quote", [{}])[0]
        return {k: q.get(k, []) for k in ("open","high","low","close","volume")}
    except Exception:
        return None


def yahoo_v7_quote(ticker):
    try:
        resp = requests.get(V7_URL.format(ticker), headers=YAHOO_HEADERS, timeout=15)
        resp.raise_for_status()
        q = resp.json().get("quoteResponse", {}).get("result", [None])[0]
        if not q: return None
        return {"price": q.get("regularMarketPrice") or q.get("previousClose"),
                "marketCap": q.get("marketCap"), "trailingPE": q.get("trailingPE"),
                "forwardPE": q.get("forwardPE"), "trailingEps": q.get("trailingEps"),
                "forwardEps": q.get("forwardEps"),
                "earningsQuarterlyGrowth": q.get("earningsQuarterlyGrowth"),
                "sector": q.get("sector"), "beta": q.get("beta"),
                "dividendYield": q.get("dividendYield")}
    except Exception:
        return None


def compute_indicators(hist):
    close = np.array([c for c in hist["close"] if c is not None], dtype=float)
    if len(close) < 20: return None
    high = np.array([h for h in hist["high"] if h is not None], dtype=float)
    low = np.array([l for l in hist["low"] if l is not None], dtype=float)
    vol = np.array([v for v in hist.get("volume",[]) if v is not None], dtype=float)
    r, p = {}, float(close[-1])
    delta = np.diff(close)
    g = np.where(delta > 0, delta, 0); l_ = np.where(delta < 0, -delta, 0)
    ag = np.mean(g[-14:]); al = np.mean(l_[-14:]) if len(l_) >= 14 else np.mean(l_)
    r["RSI"] = 100.0 if al == 0 else round(100-100/(1+ag/al), 2)
    if len(close) >= 50: r["MA50"] = round(float(np.mean(close[-50:])), 2)
    if len(close) >= 200: r["MA200"] = round(float(np.mean(close[-200:])), 2)
    if len(close) >= 26:
        ew12 = pd.Series(close).ewm(span=12).mean().values
        ew26 = pd.Series(close).ewm(span=26).mean().values
        macd = ew12-ew26; sig = pd.Series(macd).ewm(span=9).mean().values
        r["MACD"] = round(float(macd[-1]), 4)
        r["MACD_signal"] = round(float(sig[-1]), 4)
        r["MACD_histogram"] = round(float(macd[-1]-sig[-1]), 4)
    if len(close) >= 20:
        s20 = np.mean(close[-20:]); std = np.std(close[-20:])
        r["BB_upper"] = round(float(s20+2*std), 2); r["BB_lower"] = round(float(s20-2*std), 2)
        r["BB_width_pct"] = round(4*std/s20*100, 2) if s20 else 0
    if len(high) >= 15:
        tr = np.maximum(high[1:]-low[1:], np.maximum(np.abs(high[1:]-close[:-1]), np.abs(low[1:]-close[:-1])))
        r["ATR"] = round(float(np.mean(tr[-14:])), 4)
        r["ATR_pct"] = round(r["ATR"]/p*100, 2) if p > 0 else 0
    if len(vol) >= 20:
        r["Volume"] = int(vol[-1]); r["Volume_ratio"] = round(float(vol[-1]/np.mean(vol[-20:])), 2)
    r["Price"] = round(p, 2)
    if len(close) >= 2: r["Change_pct"] = round(float((close[-1]-close[-2])/close[-2]*100), 2)
    return r


def score_ticker(t):
    known = [".L",".SA",".HK",".KS",".NS",".T",".VN",".IS",".TW"]
    s = next((x for x in known if t.endswith(x)), None)
    score = 10 if s is None else (5 if s in (".L",".SA",".HK") else (3 if s in (".KS",".NS",".T") else 1))
    if t[0].isdigit() and s is None: score -= 100
    return score


def analyze_ticker(ticker_str):
    v8, v7 = yahoo_v8_history(ticker_str), yahoo_v7_quote(ticker_str)
    if v8 and len([c for c in v8.get("close",[]) if c]) >= 20 and v7:
        ind = compute_indicators(v8)
        if not ind: return None
        p = ind.get("Price", 0)
        if p < 2 and (v7.get("marketCap") or 0) < 50e6: return None
        return {"ticker": ticker_str, "price": p, "rsi": ind.get("RSI"),
                "ma50": ind.get("MA50"), "ma200": ind.get("MA200"),
                "vol_ratio": ind.get("Volume_ratio"), "change_pct": ind.get("Change_pct"),
                "pe": v7.get("trailingPE") or v7.get("forwardPE"),
                "eps": v7.get("trailingEps") or v7.get("forwardEps"),
                "eps_growth": v7.get("earningsQuarterlyGrowth"),
                "mcap": v7.get("marketCap"), "sector": v7.get("sector","Unknown"),
                "beta": v7.get("beta"), "dividend_yield": v7.get("dividendYield")}
    import yfinance as yf
    with _yf_lock:
        stock = yf.Ticker(ticker_str)
        for _ in range(YF_RETRY_ATTEMPTS):
            try:
                hist = stock.history(period="1y"); break
            except Exception: time.sleep(1)
        else: return None
    if hist is None or hist.empty or len(hist) < 20: return None
    close, price = hist["Close"], float(hist["Close"].iloc[-1])
    mcap = stock.info.get("marketCap")
    if price < 2 and (mcap or 0) < 50e6: return None
    v = hist["Volume"]
    cs = pd.Series(close.values); d = cs.diff(); g = d.where(d>0,0); l_ = -d.where(d<0,0)
    ag = g.ewm(alpha=1/14).mean().iloc[-1]; al = l_.ewm(alpha=1/14).mean().iloc[-1]
    rsi = 100.0 if al == 0 else round(100-100/(1+ag/al),2)
    ma50 = round(float(close.rolling(50).mean().iloc[-1]),2) if len(close)>=50 else None
    ma200 = round(float(close.rolling(200).mean().iloc[-1]),2) if len(close)>=200 else None
    avg_v = float(v.iloc[-50:].mean()) if len(v)>=50 else float(v.mean())
    vr = round(float(v.iloc[-1]/avg_v),2) if avg_v>0 else 1.0
    chg = round((close.iloc[-1]/close.iloc[-2]-1)*100,2) if len(close)>1 else 0
    info = stock.info
    return {"ticker": ticker_str, "price": price, "rsi": rsi, "ma50": ma50,
            "ma200": ma200, "vol_ratio": vr, "change_pct": chg,
            "pe": info.get("trailingPE") or info.get("forwardPE"),
            "eps": info.get("trailingEps") or info.get("forwardEps"),
            "eps_growth": info.get("earningsQuarterlyGrowth"),
            "mcap": mcap, "sector": info.get("sector","Unknown"),
            "beta": info.get("beta"), "dividend_yield": info.get("dividendYield")}


def scan_market_data(ticker_list):
    results = {}
    with ThreadPoolExecutor(max_workers=YF_MAX_WORKERS) as pool:
        fm = {pool.submit(analyze_ticker, t): t for t in ticker_list}
        for f in as_completed(fm):
            try:
                d = f.result()
                if d: results[fm[f]] = d
            except Exception: pass
    return results


def fetch_market_data(use_cache=True):
    if use_cache and os.path.isfile(CACHE_PATH):
        try:
            with open(CACHE_PATH) as f: return json.load(f).get("results", {}), True
        except Exception: pass
    by_region, pairs = global_tv_scan()
    converted = set()
    for bare, region in pairs:
        yf_t = convert_tv_ticker(bare, region)
        converted.add(yf_t)
    ticker_list = sorted(c for c in converted
                         if not (c[0].isdigit() and not any(c.endswith(s) for s in [".L",".T",".KS",".NS",".SA",".HK",".TW",".IS",".VN"])))
    ticker_list.sort(key=score_ticker, reverse=True)
    raw = scan_market_data(ticker_list[:150])
    print(f"[Data]  Fetched market data for {len(raw)}/{min(150,len(ticker_list))} tickers", flush=True)
    try:
        with open(CACHE_PATH, "w") as f: json.dump({"date": DATE_STR, "results": raw}, f, default=str)
    except Exception: pass
    return raw, False
