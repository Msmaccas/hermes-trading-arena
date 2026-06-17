#!/usr/bin/env python3
"""Yahoo Finance data fetching + indicator computation.

Provides:
  - yahoo_v8_history()   — fast Yahoo v8 chart API (10-15x faster than yfinance)
  - yahoo_v7_quote()     — fast Yahoo v7 quote API
  - _yf_fetch_with_retry() — yfinance fallback with rate limiting
  - fetch_and_compute()  — full fetch + indicators for a single ticker
  - batch_fetch_tickers() — parallel fetch for many tickers
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import numpy as np
import requests

from .indicators import compute_all_indicators

logger = logging.getLogger("arena.yahoo_fetcher")

# ─── Rate limiting ────────────────────────────────────────────────────────

_YAHOO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}

_yf_lock = threading.Lock()


# ─── Direct Yahoo Finance API calls ───────────────────────────────────────

def yahoo_v8_history(
    ticker: str,
    range_str: str = "1y",
    interval: str = "1d",
) -> Optional[Dict[str, Any]]:
    """Fetch historical price data via Yahoo Finance v8 chart API.

    This is 10-15x faster than yfinance for fetching OHLCV data.

    Args:
        ticker: Yahoo Finance ticker symbol.
        range_str: Time range (e.g. "1y", "6mo", "3mo", "1mo").
        interval: Bar interval (e.g. "1d", "1wk", "1mo").

    Returns:
        Dict with keys: timestamp, open, high, low, close, volume (all lists).
        None on failure.
    """
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        f"?range={range_str}&interval={interval}"
    )
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
        close_list = [c for c in q.get("close", []) if c is not None]
        if len(close_list) < 20:
            return None
        return {
            "timestamp": timestamps,
            "open": q.get("open", []),
            "high": q.get("high", []),
            "low": q.get("low", []),
            "close": q.get("close", []),
            "volume": q.get("volume", []),
        }
    except requests.RequestException:
        return None
    except (KeyError, ValueError, TypeError):
        return None


def yahoo_v7_quote(ticker: str) -> Optional[Dict[str, Any]]:
    """Fetch real-time quote data via Yahoo Finance v7 quote API.

    Args:
        ticker: Yahoo Finance ticker symbol.

    Returns:
        Dict with keys: price, marketCap, trailingPE, forwardPE, trailingEps,
        forwardEps, earningsQuarterlyGrowth, sector, beta, dividendYield,
        volume, change_pct. None on failure.
    """
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
    except requests.RequestException:
        return None
    except (KeyError, ValueError, TypeError):
        return None


# ─── yfinance fallback ────────────────────────────────────────────────────

def _yf_fetch_with_retry(
    ticker_str: str,
    operation: str = "history",
    period: str = "1y",
) -> Any:
    """Fetch yfinance data with retry logic and thread-safe rate limiting.

    Args:
        ticker_str: Yahoo Finance ticker symbol.
        operation: "history" or "info".
        period: Time period for history.

    Returns:
        yfinance history DataFrame, info dict, or None on failure.
    """
    import yfinance as yf  # lazy import — slower fallback

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


# ─── Single ticker fetch + compute ────────────────────────────────────────

def fetch_and_compute(ticker: str) -> Optional[Dict[str, Any]]:
    """Fetch yfinance data + compute indicators for one ticker.

    Priority:
      1. Direct Yahoo v8 (history) + v7 (quote) APIs (fast).
      2. yfinance fallback (slower, more reliable for some tickers).

    Filters out penny stocks (price < $2 AND mcap < $50M).

    Args:
        ticker: Yahoo Finance ticker symbol.

    Returns:
        Structured dict:
            ticker, price, change_pct, mcap, pe, eps, eps_growth,
            sector, beta, dividend_yield, volume, indicators (dict)
        None if data insufficient or penny stock.
    """
    try:
        # Try direct Yahoo API first
        v8_data = yahoo_v8_history(ticker, range_str="1y", interval="1d")
        v7_data = yahoo_v7_quote(ticker)
        v8_ok = v8_data is not None and len(v8_data.get("close", [])) >= 20
        v7_ok = v7_data is not None

        if v8_ok and v7_ok:
            close_arr = np.array(
                [c for c in v8_data["close"] if c is not None], dtype=float
            )
            high_arr = np.array(
                [h for h in v8_data["high"] if h is not None], dtype=float
            )
            low_arr = np.array(
                [l for l in v8_data["low"] if l is not None], dtype=float
            )
            vol_arr = np.array(
                [v for v in v8_data["volume"] if v is not None], dtype=float
            )
            if len(close_arr) < 20:
                return None

            price = float(close_arr[-1])
            mcap = v7_data.get("marketCap")

            # Skip penny stocks (price < $2 AND mcap < $50M)
            if price < 2.0 and (mcap is None or mcap < 50e6):
                return None

            prev_close = float(close_arr[-2]) if len(close_arr) > 1 else price
            change_pct = round((price - prev_close) / prev_close * 100, 2)

            indicators = compute_all_indicators(close_arr, high_arr, low_arr, vol_arr)

            return {
                "ticker": ticker,
                "price": round(price, 2),
                "change_pct": change_pct,
                "mcap": mcap,
                "pe": v7_data.get("trailingPE") or v7_data.get("forwardPE"),
                "eps": v7_data.get("trailingEps") or v7_data.get("forwardEps"),
                "eps_growth": v7_data.get("earningsQuarterlyGrowth"),
                "sector": v7_data.get("sector", "Unknown"),
                "beta": v7_data.get("beta"),
                "dividend_yield": v7_data.get("dividendYield"),
                "volume": v7_data.get("volume"),
                "indicators": indicators,
            }

        # Fallback: yfinance
        hist = _yf_fetch_with_retry(ticker, operation="history", period="1y")
        info = _yf_fetch_with_retry(ticker, operation="info", period="1y")
        if hist is None or hist.empty or len(hist) < 20:
            return None

        close = hist["Close"]
        high = hist["High"]
        low = hist["Low"]
        volume = hist["Volume"]
        price = float(close.iloc[-1])
        mcap = info.get("marketCap")

        if price < 2.0 and (mcap is None or mcap < 50e6):
            return None

        prev_close = float(close.iloc[-2]) if len(close) > 1 else price
        change_pct = round((price - prev_close) / prev_close * 100, 2)

        close_arr = close.values
        high_arr = high.values
        low_arr = low.values
        vol_arr = volume.values
        indicators = compute_all_indicators(close_arr, high_arr, low_arr, vol_arr)

        return {
            "ticker": ticker,
            "price": round(price, 2),
            "change_pct": change_pct,
            "mcap": mcap,
            "pe": info.get("trailingPE") or info.get("forwardPE"),
            "eps": info.get("trailingEps") or info.get("forwardEps"),
            "eps_growth": info.get("earningsQuarterlyGrowth"),
            "sector": info.get("sector", "Unknown"),
            "beta": info.get("beta"),
            "dividend_yield": info.get("dividendYield"),
            "volume": info.get("volume"),
            "indicators": indicators,
        }
    except Exception as e:
        logger.debug("fetch_and_compute failed for %s: %s", ticker, e)
        return None


# ─── Batch fetch ──────────────────────────────────────────────────────────

def batch_fetch_tickers(
    ticker_list: List[str],
    max_workers: int = 4,
    rate_limit_delay: float = 0.5,
) -> Dict[str, Dict[str, Any]]:
    """Fetch all tickers in parallel with rate limiting.

    Uses ThreadPoolExecutor for concurrent Yahoo Finance API calls.
    Rate limit delay between completions to avoid Yahoo's rate limits.

    Args:
        ticker_list: List of Yahoo Finance ticker symbols.
        max_workers: Max concurrent workers (default 4).
        rate_limit_delay: Seconds to wait between tickers (default 0.5).

    Returns:
        Dict of {ticker: data_dict} only for successful fetches.
    """
    results: Dict[str, Dict[str, Any]] = {}
    total = len(ticker_list)
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        fut_map = {pool.submit(fetch_and_compute, t): t for t in ticker_list}

        for fut in as_completed(fut_map):
            t = fut_map[fut]
            try:
                data = fut.result()
                if data:
                    results[t] = data
                else:
                    logger.debug("%s: no data returned", t)
            except Exception as e:
                logger.debug("%s: error %s", t, e)
            completed += 1
            if completed % 25 == 0:
                logger.info(
                    "Fetched %d/%d tickers (%d success so far)",
                    completed, total, len(results)
                )
            if rate_limit_delay > 0:
                time.sleep(rate_limit_delay)

    logger.info("Batch fetch: %d/%d tickers succeeded", len(results), total)
    return results
