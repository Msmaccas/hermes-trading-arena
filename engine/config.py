"""
engine/config.py — Central configuration for the Hermes Trading Arena.
All shared constants, persona lists, exchange mappings, and paths live here.
"""

import os
import datetime

# ─── PERSONAS ─────────────────────────────────────────────────────────────────

PERSONAS = [
    "oneil", "buffet", "lynch", "minervini", "qullamaggie",
    "david-ryan", "matt-caruso", "brian-shannon", "dan-zanger", "nick-schmidt",
]

# ─── DATE ─────────────────────────────────────────────────────────────────────

TODAY = datetime.date.today()
DATE_STR = TODAY.isoformat()

# ─── OUTPUT / PATHS ──────────────────────────────────────────────────────────

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(REPO_ROOT, "output")
PROFILES_DIR = os.path.join(REPO_ROOT, "profiles")
CACHE_PATH = "/tmp/hermes_market_data.json"

# ─── REGIONS FOR TV SCANNER ──────────────────────────────────────────────────

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

# ─── EXCHANGE SUFFIXES — for yfinance ticker conversion ──────────────────────

EXCHANGE_SUFFIXES = {
    "UK": ".L", "Japan": ".T", "Korea": ".KS", "India": ".NS",
    "Brazil": ".SA", "Hong_Kong": ".HK", "Taiwan": ".TW",
    "Turkey": ".IS", "Vietnam": ".VN",
}

# ─── DEEPSEEK API ────────────────────────────────────────────────────────────

DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# ─── YAHOO FINANCE API ───────────────────────────────────────────────────────

YAHOO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}

# ─── RATE LIMITS ─────────────────────────────────────────────────────────────

YF_MAX_WORKERS = 3
YF_RETRY_ATTEMPTS = 2
DEEPSEEK_RETRY_ATTEMPTS = 3
DEEPSEEK_RETRY_DELAY = 2  # seconds base, exponential backoff
