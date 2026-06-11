#!/usr/bin/env python3
"""
build_global_analysis.py — Global Equity Scanner + Multi-Persona ML Pipeline

Scans all 8 TV regions -> 60 stocks -> 6 personas -> sklearn accuracy/clustering -> Obsidian + GitHub

Phase breakdown:
  1. TV scanner (8 regions, 30 top movers each)
  2. Smart stock assignment (6 personas x 10, no overlap, max 3 US)
  3. Yahoo Finance OHLCV + fundamental enrichment
  4. Pandas/Numpy technical indicators (RSI, ATR, MACD, SMA, Trend Template, AVWAP)
  5. Persona engine analysis (360 analyses)
  6. Scikit-learn accuracy/feature-importance + KMeans redundancy detection
  7. Report generation + Obsidian copy + Git push
"""

import json
import os
import sys
import time
import traceback
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
import yfinance as yf

# ---------------------------------------------------------------------------
# Optional sklearn
# ---------------------------------------------------------------------------
_SKLEARN_AVAILABLE = False
try:
    from sklearn.cluster import KMeans
    from sklearn.decomposition import PCA
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    _SKLEARN_AVAILABLE = True
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HERMES_HOME = os.path.expanduser("~/hermes_home")
ANALYSES_DIR = os.path.join(HERMES_HOME, "global_analyses")
CACHE_PATH = os.path.join(HERMES_HOME, "global_scan_cache.json")
OBSIDIAN_DIR = "/Users/jiayanghan/Mind Palace Obsidian current/10_Trading/global_scan/"
TODAY = datetime.now().strftime("%Y-%m-%d")
YEAR_AGO = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

_REGIONS: Dict[str, str] = {
    "america": "US",
    "china": "China",
    "hongkong": "HK",
    "india": "India",
    "japan": "Japan",
    "korea": "Korea",
    "taiwan": "Taiwan",
    "brazil": "Brazil",
}

TV_COLUMNS = "name,close,change,volume,RSI,Perf.1M,Perf.3M,market_cap_basic,sector,change_abs"

# Default fields for persona engine stock_data dict (matches SN_DATA/ETOR_DATA schema)
DEFAULT_FUNDAMENTAL_KEYS = {
    "net_income_growth_pct": 0.0, "net_income_m": 0.0,
    "gross_margin_pct": 0.0, "gross_margin_change_bps": 0,
    "net_margin_pct": 0.0, "net_margin_change_bps": 0,
    "inventory_m": 0.0, "inventory_pct_of_revenue": 0.0,
    "cfo_shares_sold": 0, "cfo_sold_pct": 0, "cfo_sale_value_k": 0.0,
    "insider_sales_note": "", "chairman_sold_m": 0.0, "chairman_sold_note": "",
    "recalled_units_m": 0.0, "recalled_product": "", "class_actions": [],
    "short_seller": "", "tariffs_pct": 0.0, "tariff_note": "",
    "new_categories_count": 0, "new_products_per_year": 0,
    "marketing_spend_m": 0.0, "food_prep_category_change_pct": 0.0,
    "beauty_home_revenue_m": 0.0, "beauty_home_growth_pct": 0.0,
    "buyback_announced_m": 0.0, "buyback_date": "",
    "distribution_days": 0, "distribution_days_period": "",
    "market_regime": "unknown", "follow_through_day": False,
    "filing_period": "current", "sector_avg_pe": 0.0, "insider_activity": "",
}

PERSONA_KEYS = ["oneil", "minervini", "qullamaggie", "lynch", "buffett", "david-ryan"]

_BUFFETT_STABLE_SECTORS = {
    "Financial Services", "Consumer Defensive", "Consumer Staples",
    "Healthcare", "Utilities", "Communication Services",
    "Insurance", "Banks", "Banks - Diversified", "Banks - Regional",
    "Asset Management", "Insurance - Property & Casualty",
    "Insurance - Life", "Insurance - Reinsurance",
    "Beverages - Non-Alcoholic", "Beverages - Alcoholic",
    "Confectioners", "Packaged Foods", "Household & Personal Products",
    "Drug Manufacturers - General", "Drug Manufacturers - Specialty & Generic",
    "Medical Devices", "Diagnostics & Research", "Health Information Services",
    "Telecom Services", "Utilities - Regulated Electric",
    "Utilities - Regulated Gas", "Utilities - Renewable",
    "Software - Infrastructure",
}

STABLE_SECTORS_LOWER = {s.lower() for s in _BUFFETT_STABLE_SECTORS}


# ═══════════════════════════════════════════════════════════════════════════
# Phase 1 — TradingView Scanner
# ═══════════════════════════════════════════════════════════════════════════

def scan_market(market: str) -> List[Dict[str, Any]]:
    url = f"https://scanner.tradingview.com/{market}/scan"
    columns = TV_COLUMNS.split(",")
    payload = {
        "symbols": {"tickers": [], "query": {"types": []}},
        "columns": columns,
        "filter": [
            {"left": "close", "operation": "greater", "right": 10},
            {"left": "market_cap_basic", "operation": "greater", "right": 5e8},
            {"left": "volume", "operation": "greater", "right": 1e5},
        ],
        "sort": {"sortBy": "change", "sortOrder": "desc"},
        "range": [0, 30],
    }
    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [WARN] TV scan failed for {market}: {e}")
        return []

    results = []
    for item in data.get("data", []):
        try:
            d = item.get("d", [])
            s = item.get("s", "")
            if len(d) < len(columns):
                continue
            results.append({
                "tv_ticker": s, "name": d[0], "close": d[1], "change": d[2],
                "volume": d[3], "rsi": d[4], "perf_1m": d[5], "perf_3m": d[6],
                "market_cap_basic": d[7], "sector": d[8],
                "change_abs": d[9] if len(d) > 9 else 0.0,
                "region": _REGIONS.get(market, market),
            })
        except (IndexError, TypeError):
            continue
    return results


def run_global_scan() -> Dict[str, List[Dict[str, Any]]]:
    print("\n=== Phase 1: Global Screen (TradingView Scanner) ===")
    all_results: Dict[str, List[Dict[str, Any]]] = {}
    for market in _REGIONS:
        print(f"  Scanning {market} ({_REGIONS[market]})...", end=" ", flush=True)
        results = scan_market(market)
        print(f"{len(results)} stocks found")
        all_results[market] = results
        time.sleep(0.5)
    os.makedirs(os.path.dirname(CACHE_PATH) or ".", exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"  Raw cache saved to {CACHE_PATH}")
    return all_results


def convert_tv_ticker(tv_ticker: str, region: str) -> str:
    if ":" not in tv_ticker:
        return tv_ticker
    exchange, symbol = tv_ticker.split(":", 1)
    # US stocks: yfinance uses dashes not dots (BRK.B -> BRK-B)
    symbol_yf = symbol.replace(".", "-") if region == "US" else symbol
    if region == "US":
        return symbol_yf
    elif region == "China":
        return symbol_yf + (".SS" if exchange == "SSE" else ".SZ")
    elif region == "HK":
        # yfinance expects 4-digit HK codes with leading zeros
        padded = symbol_yf.zfill(4)
        return padded + ".HK"
    elif region == "India":
        # BSE stocks use .BO suffix; NSE uses .NS
        return symbol_yf + (".BO" if exchange == "BSE" else ".NS")
    elif region == "Japan":
        return symbol_yf + ".T"
    elif region == "Korea":
        return symbol_yf + ".KS"
    elif region == "Taiwan":
        # TWSE main board -> .TW, TPEX OTC -> .TWO
        return symbol_yf + (".TWO" if exchange == "TPEX" else ".TW")
    elif region == "Brazil":
        return symbol_yf + ".SA"
    return symbol_yf


# ═══════════════════════════════════════════════════════════════════════════
# Phase 2 — Smart Stock Assignment
# ═══════════════════════════════════════════════════════════════════════════

def build_universe(all_results: Dict[str, List[Dict[str, Any]]]) -> pd.DataFrame:
    rows = []
    for market, stocks in all_results.items():
        region = _REGIONS[market]
        for s in stocks:
            ticker_yf = convert_tv_ticker(s["tv_ticker"], region)
            rows.append({
                "tv_ticker": s["tv_ticker"], "ticker_yf": ticker_yf,
                "name": s["name"], "close": float(s["close"]),
                "change": float(s["change"]), "volume": float(s["volume"]),
                "rsi": s["rsi"] if s["rsi"] is not None else 50.0,
                "perf_1m": float(s["perf_1m"]) if s["perf_1m"] is not None else 0.0,
                "perf_3m": float(s["perf_3m"]) if s["perf_3m"] is not None else 0.0,
                "mktcap": float(s["market_cap_basic"]),
                "sector": s["sector"] or "Unknown",
                "region": region, "change_abs": float(s["change_abs"]),
            })
    df = pd.DataFrame(rows).dropna(subset=["close", "mktcap"])
    df = df.drop_duplicates(subset=["ticker_yf"], keep="first")
    df = df.sort_values("perf_1m", ascending=False).reset_index(drop=True)
    return df


def assign_stocks(df: pd.DataFrame) -> Dict[str, List[str]]:
    print("\n=== Phase 2: Smart Stock Assignment ===")

    scoring = {
        "oneil": lambda r: (3.0 if r["perf_1m"] > 0 else 0.0)
                          + (2.0 if 10 <= r["close"] <= 50 else 0.0)
                          + (1.0 if 50 <= r.get("rsi", 50) <= 80 else 0.0)
                          + r["perf_1m"] / 10.0,
        "minervini": lambda r: (3.0 if r.get("rsi", 0) > 50 else 0.0)
                              + (1.0 if r.get("rsi", 50) < 75 else 0.0)
                              + (1.0 if r["perf_1m"] > -5 else 0.0)
                              + r["perf_1m"] / 5.0,
        "qullamaggie": lambda r: r["change"] * 2.0 + r["perf_1m"],
        "lynch": lambda r: (r["perf_3m"] / 5.0 if r["perf_1m"] > 0 else 0.0) + r["perf_1m"] / 3.0,
        "buffett": lambda r: (1.0 if r["perf_1m"] > -10 else 0.0) + r["perf_3m"] / 10.0,
        "david-ryan": lambda r: r["change"] * 1.5 + r["perf_1m"] * 2.0
                               + (2.0 if r.get("rsi", 50) > 60 else 0.0),
    }
    region_prefs = {
        "oneil": ["US"], "minervini": ["US"],
        "qullamaggie": ["China", "India", "HK", "Korea", "Taiwan", "Japan", "Brazil"],
        "lynch": ["India", "Brazil", "HK", "China"],
        "buffett": ["Japan", "HK"],
        "david-ryan": ["Taiwan", "India", "Korea"],
    }
    us_caps = {"oneil": 2, "minervini": 1}

    used_tickers: set = set()
    watchlists: Dict[str, List[str]] = {k: [] for k in PERSONA_KEYS}

    for pk in PERSONA_KEYS:
        prefs = region_prefs[pk]
        avail = df[~df["ticker_yf"].isin(used_tickers)].copy()
        if avail.empty:
            continue
        if "US" in prefs:
            cap = us_caps.get(pk, 3)
            candidates = pd.concat([avail[avail["region"] != "US"], avail[avail["region"] == "US"].head(cap)])
        else:
            candidates = avail[avail["region"].isin(prefs)]
        if candidates.empty:
            candidates = avail[avail["region"] != "US"]
        if candidates.empty:
            candidates = avail

        scorer = scoring[pk]
        candidates = candidates.copy()
        candidates["_score"] = candidates.apply(scorer, axis=1)
        candidates = candidates.sort_values("_score", ascending=False)
        tickers = candidates.head(10)["ticker_yf"].tolist()

        # Enforce US cap
        us_count = sum(1 for t in tickers if df[df["ticker_yf"] == t]["region"].iloc[0] == "US")
        max_us = us_caps.get(pk, 3)
        if us_count > max_us and max_us < 3:
            extra = us_count - max_us
            non_us = candidates[candidates["region"] != "US"]["ticker_yf"].tolist()
            fixed, replaced = [], 0
            for t in tickers:
                is_us = df[df["ticker_yf"] == t]["region"].iloc[0] == "US"
                if is_us and replaced < extra:
                    for nu in non_us:
                        if nu not in fixed and nu not in used_tickers:
                            fixed.append(nu)
                            replaced += 1
                            break
                else:
                    fixed.append(t)
            tickers = fixed[:10]

        watchlists[pk] = tickers
        used_tickers.update(tickers)
        us_in = sum(1 for t in tickers if df[df["ticker_yf"] == t]["region"].iloc[0] == "US")
        print(f"  {pk}: {len(tickers)} stocks ({us_in} US)")

    total_us = sum(1 for t in used_tickers if df[df["ticker_yf"] == t]["region"].iloc[0] == "US")
    print(f"  Total unique: {len(used_tickers)}, US: {total_us}")
    rc = df[df["ticker_yf"].isin(used_tickers)]["region"].value_counts()
    print(f"  Regions:\n{rc.to_string()}")
    return watchlists


# ═══════════════════════════════════════════════════════════════════════════
# Phase 3 — Yahoo Finance Data Enrichment
# ═══════════════════════════════════════════════════════════════════════════

def compute_indicators(df_ohlcv: pd.DataFrame) -> Dict[str, Any]:
    if df_ohlcv.empty or len(df_ohlcv) < 200:
        return {}
    close, high, low, volume = df_ohlcv["Close"], df_ohlcv["High"], df_ohlcv["Low"], df_ohlcv["Volume"]

    # RSI(14) Wilder EMA
    diff = close.diff()
    gain = diff.where(diff > 0, 0.0)
    loss = -diff.where(diff < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi14 = 100 - 100 / (1 + rs)

    # ATR(14)
    hl, hpc, lpc = high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()
    tr = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    atr = tr.ewm(span=14, min_periods=14, adjust=False).mean()
    atr_pct = atr / close * 100

    # MACD
    ema12, ema26 = close.ewm(span=12, adjust=False).mean(), close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_sig = macd.ewm(span=9, adjust=False).mean()
    macd_hist = macd - macd_sig

    # SMAs
    sma20 = close.rolling(20, min_periods=10).mean()
    sma50 = close.rolling(50, min_periods=30).mean()
    sma150 = close.rolling(150, min_periods=100).mean()
    sma200 = close.rolling(200, min_periods=120).mean()
    vol50 = volume.rolling(50, min_periods=20).mean()

    # AVWAP
    idx = df_ohlcv.index
    month_starts = [idx[0]] + [idx[i] for i in range(1, len(idx)) if idx[i].month != idx[i-1].month]
    avwap = pd.Series(np.nan, index=idx)
    for ms in month_starts:
        mask = idx >= ms
        if mask.any():
            sub = df_ohlcv.loc[mask]
            cpv = (sub["Close"] * sub["Volume"]).cumsum()
            cv = sub["Volume"].cumsum()
            avwap.loc[mask] = cpv / cv.replace(0, np.nan)

    high_52w = close.rolling(min(252, len(close)), min_periods=60).max()
    tt = ((close > sma50) & (sma50 > sma150) & (close > sma200) & (sma50 > sma200)).astype(int)

    def sf(val, idx=-1):
        if isinstance(val, pd.Series) and len(val):
            v = val.iloc[idx]
            return float(v) if not np.isnan(v) else 0.0
        return 0.0

    return {
        "rsi_14": sf(rsi14), "atr_pct": sf(atr_pct),
        "macd": sf(macd), "macd_signal": sf(macd_sig), "macd_hist": sf(macd_hist),
        "ema12": sf(ema12), "ema26": sf(ema26),
        "sma20": sf(sma20), "sma50": sf(sma50), "sma150": sf(sma150), "sma200": sf(sma200),
        "vol_50d": sf(vol50),
        "vol_vs_avg_pct": ((sf(volume)/sf(vol50))-1)*100 if sf(vol50) > 0 else 0,
        "avwap": sf(avwap),
        "off_52w_high_pct": (sf(close)-sf(high_52w))/sf(high_52w)*100 if sf(high_52w) > 0 else 0,
        "trend_template": int(sf(tt)),
        "price_above_ma50_pct": (sf(close)-sf(sma50))/sf(sma50)*100 if sf(sma50) > 0 else 0,
        "price_above_ma200_pct": (sf(close)-sf(sma200))/sf(sma200)*100 if sf(sma200) > 0 else 0,
    }


def fetch_stock_data(ticker_yf: str) -> Optional[Dict[str, Any]]:
    max_retries = 3
    retry_delay = 2.0

    for attempt in range(1, max_retries + 1):
        try:
            ticker = yf.Ticker(ticker_yf)
            df = ticker.history(period="1y")

            if df is None or df.empty:
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                return None

            tech = compute_indicators(df)
            info = {}
            try:
                info = ticker.info or {}
            except Exception:
                pass

            close_vals = df["Close"]
            if isinstance(close_vals, pd.DataFrame):
                price_val = float(close_vals.iloc[-1, 0])
            else:
                price_val = float(close_vals.iloc[-1])

            sd: Dict[str, Any] = {
                "ticker": ticker_yf,
                "price": price_val,
                "sector": info.get("sector", "Unknown"),
                "industry": info.get("industry", "Unknown"),
                "mktcap_b": (info.get("marketCap", 0) or 0) / 1e9,
                "pe_ratio": float(info["trailingPE"]) if info.get("trailingPE") and info["trailingPE"] is not None else 0.0,
                "eps_growth_pct": float(info.get("earningsGrowth", 0) or 0) * 100,
                "rev_growth_pct": float(info.get("revenueGrowth", 0) or 0) * 100,
                "roe_pct": float(info.get("returnOnEquity", 0) or 0) * 100,
                "inst_own_pct": float(info.get("heldPercentInstitutions", 0) or 0) * 100,
                "off_52w_high_pct": tech.get("off_52w_high_pct", 0.0),
                "rsi_14": tech.get("rsi_14", 50.0),
                "vol_vs_avg_pct": tech.get("vol_vs_avg_pct", 0.0),
                "price_above_ma50_pct": tech.get("price_above_ma50_pct", 0.0),
                "price_above_ma200_pct": tech.get("price_above_ma200_pct", 0.0),
                "debt_m": (info.get("totalDebt", 0) or 0) / 1e6,
                "cash_m": (info.get("totalCash", 0) or 0) / 1e6,
                "atr_pct": tech.get("atr_pct", 0.0),
                "macd": tech.get("macd", 0.0),
                "macd_hist": tech.get("macd_hist", 0.0),
                "trend_template": tech.get("trend_template", 0),
                "avwap": tech.get("avwap", 0.0),
                "sma20": tech.get("sma20", 0.0),
                "sma50": tech.get("sma50", 0.0),
                "sma200": tech.get("sma200", 0.0),
            }

            pos, risks = [], []
            pe, rev, roe, rsi = sd["pe_ratio"], sd["rev_growth_pct"], sd["roe_pct"], sd["rsi_14"]
            off, atr_pct = sd["off_52w_high_pct"], sd["atr_pct"]
            if 0 < pe < 20: pos.append(f"P/E {pe:.1f}x reasonable")
            if rev > 10: pos.append(f"Revenue growth {rev:.1f}%")
            if roe > 15: pos.append(f"ROE {roe:.1f}% solid")
            if sd["trend_template"] == 1: pos.append("Trend template PASS")
            if sd.get("macd_hist", 0) > 0: pos.append("MACD positive")
            if rsi < 30: pos.append("RSI oversold")
            if pe > 30: risks.append(f"P/E {pe:.1f}x expensive")
            if rev < 0: risks.append(f"Revenue declining {abs(rev):.1f}%")
            if 0 < roe < 10: risks.append(f"ROE {roe:.1f}% low")
            if off < -30: risks.append(f"{-off:.0f}% off 52w high")
            if rsi > 70: risks.append("RSI overbought")
            if atr_pct > 5: risks.append(f"High vol ({atr_pct:.1f}% ATR)")
            sd["key_positives"] = "; ".join(pos) if pos else "Mixed signals"
            sd["key_risks"] = "; ".join(risks) if risks else "No clear risks"
            sd["insider_activity"] = "Check SEC filings"

            for k, v in DEFAULT_FUNDAMENTAL_KEYS.items():
                sd.setdefault(k, v)
            sd["net_income_growth_pct"] = sd["eps_growth_pct"]
            sd["revenue_growth_pct"] = sd["rev_growth_pct"]
            sd["net_income_m"] = (info.get("netIncomeToCommon", 0) or 0) / 1e6
            return sd

        except Exception as e:
            if attempt < max_retries:
                time.sleep(retry_delay)
                continue
            print(f"    [ERROR] {ticker_yf} after {max_retries} retries: {e}")
            return None
    return None


def enrich_all_stocks(watchlists: Dict[str, List[str]]) -> Dict[str, Dict[str, Any]]:
    print("\n=== Phase 3: Data Enrichment (Yahoo Finance) ===")
    all_tickers = set(t for tt in watchlists.values() for t in tt)
    print(f"  Unique tickers: {len(all_tickers)}")
    stock_data_map: Dict[str, Dict[str, Any]] = {}
    failures = []

    with ThreadPoolExecutor(max_workers=5) as ex:
        fut_map = {ex.submit(fetch_stock_data, t): t for t in all_tickers}
        for fut in as_completed(fut_map):
            t = fut_map[fut]
            r = fut.result()
            if r:
                stock_data_map[t] = r
                print(f"  ok {t}: ${r['price']:.2f}")
            else:
                failures.append(t)
                print(f"  FAILED {t}")
            time.sleep(0.3)

    print(f"  Success: {len(stock_data_map)}/{len(all_tickers)}")
    if failures:
        print(f"  Failures: {failures}")
    return stock_data_map


# ═══════════════════════════════════════════════════════════════════════════
# Phase 4 — Persona Engine Analysis
# ═══════════════════════════════════════════════════════════════════════════

def run_persona_analyses(watchlists, stock_data_map, pe_module) -> pd.DataFrame:
    print("\n=== Phase 4: Persona Engine Analysis ===")
    rows, total, done = [], sum(len(t) for t in watchlists.values()), 0
    for pk in PERSONA_KEYS:
        pdir = os.path.join(ANALYSES_DIR, pk)
        os.makedirs(pdir, exist_ok=True)
        for ticker in watchlists.get(pk, []):
            done += 1
            sd = stock_data_map.get(ticker)
            if not sd:
                print(f"  [{done}/{total}] SKIP {ticker}->{pk}: no data")
                continue
            try:
                a = pe_module.analyze_persona(pk, sd, verbose=False)
                with open(os.path.join(pdir, f"{ticker}_{pk}_{TODAY}.md"), "w") as f:
                    f.write(pe_module.format_analysis(a))
                rows.append({
                    "persona": pk, "ticker": ticker, "date": TODAY,
                    "verdict": a.verdict, "conviction": a.conviction,
                    "price_at_call": sd.get("price", 0),
                    "sector": sd.get("sector", ""), "rsi_14": sd.get("rsi_14", 0),
                    "off_52w_high_pct": sd.get("off_52w_high_pct", 0),
                    "trend_template": sd.get("trend_template", 0),
                })
                print(f"  [{done}/{total}] ok {ticker}->{pk}: {a.verdict} ({a.conviction:.2f})")
            except Exception as e:
                print(f"  [{done}/{total}] FAIL {ticker}->{pk}: {e}")
    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════════════════
# Phase 5 — Scikit-Learn ML
# ═══════════════════════════════════════════════════════════════════════════

def run_ml_analysis(results_df: pd.DataFrame) -> Dict[str, Any]:
    print("\n=== Phase 5: ML Accuracy Engine ===")
    ins: Dict[str, Any] = {"verdict_distribution": {}, "persona_agreement_matrix": {},
                           "redundant_personas": [], "conviction_weighted_consensus": [],
                           "persona_tendencies": {}}
    if results_df.empty:
        return ins

    vd = results_df.groupby(["persona", "verdict"]).size().unstack(fill_value=0)
    ins["verdict_distribution"] = vd.to_dict()
    print(f"\n  Verdicts:\n{vd.to_string()}")

    pivot = results_df.pivot_table(index="ticker", columns="persona", values="verdict", aggfunc="first")
    if pivot.shape[1] >= 2:
        ps = list(pivot.columns)
        am = pd.DataFrame(1.0, index=ps, columns=ps)
        for p1 in ps:
            for p2 in ps:
                if p1 < p2:
                    both = pivot[[p1, p2]].dropna()
                    agree = (both[p1] == both[p2]).mean() if len(both) else 0
                    am.loc[p1, p2] = am.loc[p2, p1] = agree
        ins["persona_agreement_matrix"] = am.to_dict()
        print(f"\n  Agreement matrix:\n{am.to_string(float_format=lambda x: f'{x:.0%}')}")

        if _SKLEARN_AVAILABLE and len(ps) >= 3:
            try:
                dm = 1 - am.values
                np.fill_diagonal(dm, 0)
                km = KMeans(n_clusters=min(3, len(ps)-1), random_state=42, n_init=10).fit(dm)
                cl = {}
                for i, p in enumerate(ps):
                    cl.setdefault(int(km.labels_[i]), []).append(p)
                ins["redundant_clusters"] = {str(k): v for k, v in cl.items()}
                print(f"\n  KMeans clusters:")
                for c, mem in cl.items():
                    if len(mem) > 1:
                        sub = am.loc[mem, mem]
                        avg = sub.values[np.triu_indices_from(sub.values, k=1)].mean()
                        print(f"    C{c}: {', '.join(mem)} (agree {avg:.0%})")
                        if avg > 0.6:
                            ins["redundant_personas"].extend(mem[1:])
                    else:
                        print(f"    C{c}: {mem[0]} (distinct)")
            except Exception as e:
                print(f"  KMeans failed: {e}")

    # Consensus
    ts = results_df.groupby("ticker").agg(
        conviction=("conviction", "mean"),
        consensus_verdict=("verdict", lambda x: x.mode().iloc[0] if not x.mode().empty else "HOLD"),
        num_opinions=("persona", "count"),
        price_at_call=("price_at_call", "first"),
    ).reset_index()
    ins["conviction_weighted_consensus"] = ts.to_dict("records")
    print(f"\n  Top consensus:")
    for _, r in ts.sort_values("conviction", ascending=False).head(10).iterrows():
        print(f"    {r['ticker']:10s} | {r['consensus_verdict']:6s} | conv {r['conviction']:.2f}")

    # Tendencies
    for p in PERSONA_KEYS:
        pd_ = results_df[results_df["persona"] == p]
        if len(pd_) > 0:
            ins["persona_tendencies"][p] = {
                "avg_conviction": float(pd_["conviction"].mean()),
                "verdicts": pd_["verdict"].value_counts().to_dict(),
                "buy_pct": float((pd_["verdict"] == "BUY").mean()),
                "sell_pct": float((pd_["verdict"].isin(["SELL", "AVOID"])).mean()),
                "hold_pct": float((pd_["verdict"] == "HOLD").mean()),
            }
    return ins


# ═══════════════════════════════════════════════════════════════════════════
# Phase 6 — Output Reports
# ═══════════════════════════════════════════════════════════════════════════

def _top_picks(rd: pd.DataFrame, ins: Dict) -> str:
    lines = [f"# Global Equity Scan -- Top Picks by Persona\n**{TODAY}**\n---\n"]
    for pk in PERSONA_KEYS:
        pd_ = rd[rd["persona"] == pk]
        if pd_.empty:
            continue
        lines.append(f"## {pk.title()}")
        for _, r in pd_.sort_values("conviction", ascending=False).head(3).iterrows():
            lines.append(f"- **{r['ticker']}** | {r['verdict']} ({r['conviction']:.2f}) | ${r['price_at_call']:.2f} | RSI {r['rsi_14']:.1f}")
        lines.append("")
    lines.append("---\n## Consensus Top 10\n")
    for item in sorted(ins.get("conviction_weighted_consensus", []), key=lambda x: x["conviction"], reverse=True)[:10]:
        lines.append(f"- **{item['ticker']}** | {item['consensus_verdict']} (conv {item['conviction']:.2f}) | ${item['price_at_call']:.2f} | {int(item['num_opinions'])} opinions")
    if ins.get("redundant_personas"):
        lines.append("\n---\n## Redundancy\n")
        for p in ins["redundant_personas"]:
            lines.append(f"- {p}")
    return "\n".join(lines)


def _consensus_matrix(rd: pd.DataFrame, ins: Dict = None) -> str:
    lines = [f"# Consensus Matrix\n**{TODAY}** | {rd['ticker'].nunique()} stocks\n"]
    hdr = "| Ticker | Price | Sector | " + " | ".join(p.title() for p in PERSONA_KEYS) + " |"
    sep = "|-------|-------|--------|" + "|".join("--------" for _ in PERSONA_KEYS) + "|"
    lines.extend([hdr, sep])
    pv = rd.pivot_table(index="ticker", columns="persona", values="verdict", aggfunc="first")
    cv = rd.pivot_table(index="ticker", columns="persona", values="conviction", aggfunc="first")
    info = rd.drop_duplicates("ticker").set_index("ticker")[["price_at_call", "sector"]].to_dict("index")
    for ticker in sorted(pv.index):
        i = info.get(ticker, {})
        row = f"| {ticker} | ${i.get('price_at_call',0):.1f} | {(i.get('sector','') or '')[:20]} "
        for p in PERSONA_KEYS:
            if p in pv.columns and pd.notna(pv.loc[ticker, p]):
                c = cv.loc[ticker, p] if p in cv.columns else 0
                row += f"| {pv.loc[ticker, p]} ({c:.2f}) "
            else:
                row += "| - "
        lines.append(row + "|")
    return "\n".join(lines)


def _ml_insights(ins: Dict, _unused: Dict = None) -> str:
    lines = [f"# ML Insights\n**{TODAY}**\n"]
    lines.append("## Verdict Distribution\n```")
    for p, d in ins.get("verdict_distribution", {}).items():
        lines.append(f"{p:15s} {dict(d)}")
    lines.append("```\n## Agreement Matrix\n")
    am = ins.get("persona_agreement_matrix", {})
    if am:
        ps = list(am.keys())
        lines.append("| Persona | " + " | ".join(p[:10] for p in ps) + " |")
        lines.append("|--------|" + "|".join("----------" for _ in ps) + "|")
        for p1 in ps:
            row = f"| {p1[:10]:8s}"
            for p2 in ps:
                val = am.get(p1, {}).get(p2, 0)
                row += f" | {val:.0%}" if isinstance(val, (int, float)) else " | -"
            lines.append(row + " |")
    lines.append("\n## Redundancy\n")
    for c, mem in ins.get("redundant_clusters", {}).items():
        lines.append(f"- Cluster {c}: {', '.join(mem)}")
    lines.append("\n## Persona Tendencies\n")
    lines.append("| Persona | Conviction | BUY% | SELL% | HOLD% |")
    lines.append("|--------|-----------|------|-------|-------|")
    for p in PERSONA_KEYS:
        t = ins.get("persona_tendencies", {}).get(p, {})
        if t:
            lines.append(f"| {p:12s} | {t.get('avg_conviction',0):.2f} | {t.get('buy_pct',0):.0%} | {t.get('sell_pct',0):.0%} | {t.get('hold_pct',0):.0%} |")
    return "\n".join(lines)


def write_reports(rd: pd.DataFrame, ins: Dict) -> List[str]:
    print("\n=== Phase 6: Output ===")
    os.makedirs(ANALYSES_DIR, exist_ok=True)
    paths = []
    for name, fn in [(f"{TODAY}_top_picks.md", _top_picks), (f"{TODAY}_consensus_matrix.md", _consensus_matrix), (f"{TODAY}_ml_insights.md", _ml_insights)]:
        p = os.path.join(ANALYSES_DIR, name)
        with open(p, "w") as f:
            f.write(fn(rd, ins))
        print(f"  ok {p}")
        paths.append(p)
    return paths


def copy_to_obsidian():
    print("\n=== Obsidian ===")
    if not os.path.exists(OBSIDIAN_DIR):
        try:
            os.makedirs(OBSIDIAN_DIR, exist_ok=True)
        except Exception as e:
            print(f"  [WARN] {e}"); return
    for item in os.listdir(ANALYSES_DIR):
        src, dst = os.path.join(ANALYSES_DIR, item), os.path.join(OBSIDIAN_DIR, item)
        if os.path.isfile(src):
            with open(src) as f: c = f.read()
            with open(dst, "w") as f: f.write(c)
            print(f"  Copied {item}")


def push_to_github():
    print("\n=== GitHub ===")
    for cmd, timeout in [
        (["git", "add", "-A"], 30),
        (["git", "commit", "-m", f"Global equity scan + ML [{TODAY}]"], 30),
        (["git", "push", "origin", "main"], 60),
    ]:
        try:
            r = subprocess.run(cmd, cwd=HERMES_HOME, capture_output=True, text=True, timeout=timeout)
            if r.stdout.strip(): print(f"  {r.stdout.strip()}")
            if r.stderr.strip(): print(f"  stderr: {r.stderr.strip()}")
        except Exception as e:
            print(f"  [WARN] {cmd[0]}: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    print(f"{'='*60}\n  GLOBAL EQUITY SCANNER + ML PIPELINE\n  {TODAY}\n{'='*60}")
    t0 = time.time()

    # Phase 1
    ar = run_global_scan()
    n_found = sum(len(v) for v in ar.values())
    print(f"\n  Total found: {n_found}")

    # Phase 2
    df = build_universe(ar)
    print(f"  Universe: {len(df)} stocks")
    wl = assign_stocks(df)
    assigned = set(t for tt in wl.values() for t in tt)
    print(f"  Assigned: {len(assigned)}")

    # Phase 3
    sm = enrich_all_stocks(wl)
    print(f"  Enriched: {len(sm)} stocks")
    if not sm:
        print("[FATAL] No stock data."); sys.exit(1)

    # Phase 4
    sys.path.insert(0, HERMES_HOME)
    import persona_engine as pe
    rd = run_persona_analyses(wl, sm, pe)
    print(f"  Analyses: {len(rd)}")
    if rd.empty:
        print("[FATAL] No analyses."); sys.exit(1)
    rd.to_csv(os.path.join(ANALYSES_DIR, f"{TODAY}_results.csv"), index=False)

    # Phase 5
    ins = run_ml_analysis(rd)

    # Phase 6
    paths = write_reports(rd, ins)
    copy_to_obsidian()
    push_to_github()

    sec = time.time() - t0
    print(f"\n{'='*60}\n  DONE in {sec:.0f}s")
    for p in paths:
        print(f"    ok {p}")
    print(f"{'='*60}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
