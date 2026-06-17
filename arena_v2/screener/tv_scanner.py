#!/usr/bin/env python3
"""TradingView scanner API — scan all global markets.

This module replaces the TV scan functions from the old data_collector.py.
Scans every region defined in config.TV_MARKETS, returns raw tickers
per region and as a flat list of (ticker, region) pairs.
"""

import logging
from typing import Dict, List, Set, Tuple

import requests

from ..config import TV_MARKETS

logger = logging.getLogger("arena.tv_scanner")

# ─── API constants ────────────────────────────────────────────────────────

TV_SCAN_URL = "https://scanner.tradingview.com/{region}/scan"

TV_SCAN_COLUMNS = [
    "name", "close", "change", "change_abs", "volume",
    "description", "Recommend.All", "RSI", "BB.upper", "BB.lower",
    "EMA50", "EMA200", "SMA50", "SMA200", "Volatility.D",
]

TV_SCAN_FILTER = [{"left": "change", "operation": "greater", "right": 0}]


# ─── Single region scan ──────────────────────────────────────────────────

def tv_scan_region(
    region: str,
    sort_by: str = "volume",
    sort_order: str = "desc",
    range_size: int = 100,
) -> List[dict]:
    """Call TradingView scanner API for a single region.

    Args:
        region: TradingView region key (e.g. "america", "china", "india").
        sort_by: Column to sort results by.
        sort_order: "desc" or "asc".
        range_size: Number of results to return (max ~200).

    Returns:
        List of dicts with keys from TV_SCAN_COLUMNS.
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
            entry = dict(zip(TV_SCAN_COLUMNS[:len(d)], d))
            stocks.append(entry)
        return stocks
    except requests.RequestException as e:
        logger.warning("TV scan failed for region '%s' (sort=%s): %s", region, sort_by, e)
        return []
    except (KeyError, ValueError, TypeError) as e:
        logger.warning("TV scan parse error for region '%s': %s", region, e)
        return []


def tv_scan_tickers(region: str) -> Set[str]:
    """Scan a region for tickers, merging volume-sorted and gainers queries.

    Two passes (volume desc + change desc) to catch active stocks
    regardless of how they're moving.

    Args:
        region: TradingView region key.

    Returns:
        Set of ticker symbols (raw TradingView format).
    """
    vol_stocks = tv_scan_region(region, sort_by="volume", sort_order="desc")
    change_stocks = tv_scan_region(region, sort_by="change", sort_order="desc")

    seen: Set[str] = set()
    tickers: List[str] = []
    for s in vol_stocks + change_stocks:
        name = s.get("name", "")
        if name and name not in seen:
            seen.add(name)
            tickers.append(name)
    return set(tickers)


# ─── Global scan ─────────────────────────────────────────────────────────

def global_tv_scan() -> Tuple[Dict[str, List[str]], List[Tuple[str, str]]]:
    """Scan ALL market regions defined in TV_MARKETS.

    Every region is scanned — no exclusion, no rotation. All markets
    for all personas.

    Returns:
        by_region: {display_name: [ticker1, ticker2, ...]}
        all_ticker_pairs: [(ticker, region_display_name), ...]
    """
    by_region: Dict[str, List[str]] = {}
    all_ticker_pairs: List[Tuple[str, str]] = []
    total = 0

    for display_name, tv_region in TV_MARKETS.items():
        region_tickers = tv_scan_tickers(tv_region)
        by_region[display_name] = sorted(region_tickers)
        for t in region_tickers:
            all_ticker_pairs.append((t, display_name))
        total += len(region_tickers)
        logger.info("TV scan: %s → %d tickers", display_name, len(region_tickers))

    logger.info("TV scan total: %d tickers across %d regions", total, len(TV_MARKETS))
    return by_region, all_ticker_pairs


def convert_tv_ticker(ticker: str, region: str) -> str:
    """Convert a TradingView scanner ticker to yfinance-compatible format.

    Handles market-specific suffix conventions:
      - US/China: bare ticker
      - HK: strip leading zeros, add .HK
      - UK: replace '.' with '-', add .L
      - Others: append exchange suffix
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
        "Singapore": ".SI",
        "Canada": ".TO",
        "Australia": ".AX",
        "Shanghai": ".SS",
        "Shenzhen": ".SZ",
    }
    suffix = suffix_map.get(region, "")
    if not suffix:
        return ticker
    if region == "UK":
        ticker = ticker.rstrip(".")
        if "." in ticker:
            ticker = ticker.replace(".", "-")
        return ticker + ".L"
    if region == "Hong_Kong":
        while len(ticker) > 4 and ticker.startswith("0"):
            ticker = ticker[1:]
        return ticker + ".HK"
    return ticker + suffix
