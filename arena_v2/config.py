#!/usr/bin/env python3
"""Configuration constants for Arena V2."""

import os
from pathlib import Path

# ─── Personas ─────────────────────────────────────────────────────────────────

PERSONAS = [
    "oneil", "buffet", "lynch", "minervini", "qullamaggie",
    "david-ryan", "matt-caruso", "brian-shannon", "dan-zanger", "nick-schmidt",
]

# ─── Exchange suffix mapping for global stocks via yfinance ──────────────────

EXCHANGE_SUFFIXES = {
    "UK": ".L",
    "Japan": ".T",
    "Korea": ".KS",
    "India": ".NS",
    "Brazil": ".SA",
    "Hong_Kong": ".HK",
    "Taiwan": ".TW",
    "Turkey": ".IS",
    "Vietnam": ".VN",
    "Singapore": ".SI",
    "Canada": ".TO",
    "Australia": ".AX",
    "Shanghai": ".SS",
    "Shenzhen": ".SZ",
}

# ─── TradingView scanner regions ────────────────────────────────────────────

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

# ─── Paths ───────────────────────────────────────────────────────────────────

PROFILES_DIR = os.path.expanduser("~/.hermes/profiles")
OUTPUT_DIR = os.path.expanduser("~/hermes-trading-arena/output")

# ─── DeepSeek API ───────────────────────────────────────────────────────────

DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_TIMEOUT = 600  # seconds (generous for 3000+ word generation)
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# ─── Concurrency limits ─────────────────────────────────────────────────────

MAX_STOCKS_PER_PERSONA = 5       # full 3000w analysis
SUMMARY_STOCKS_PER_PERSONA = 10  # additional summary-level
CONCURRENCY_LIMIT = 4            # parallel persona workers

# ─── API key resolution ────────────────────────────────────────────────────

def resolve_deepseek_api_key() -> str:
    """Resolve DeepSeek API key: env var -> ~/.hermes/.env -> ~/.hermes/config.yaml."""
    # 1. Environment variable
    env_key = os.environ.get("DEEPSEEK_API_KEY")
    if env_key:
        return env_key

    # 2. ~/.hermes/.env file
    env_path = os.path.expanduser("~/.hermes/.env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DEEPSEEK_API_KEY") and "=" in line:
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
    except Exception:
        pass

    # 3. ~/.hermes/config.yaml
    try:
        import yaml
        cfg_path = os.path.expanduser("~/.hermes/config.yaml")
        if os.path.isfile(cfg_path):
            with open(cfg_path) as f:
                cfg = yaml.safe_load(f)
            if isinstance(cfg, dict):
                key = cfg.get("deepseek", {}).get("api_key") or cfg.get("DEEPSEEK_API_KEY")
                if key:
                    return key
    except Exception:
        pass

    return ""
