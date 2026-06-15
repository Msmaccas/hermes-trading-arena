"""
engine/utils.py — Shared utilities for the Hermes Trading Arena.
Safe conversions, API key resolution, DeepSeek calls, file writing.
"""

import os
import sys
import json
import time
import requests

from engine.config import (
    DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
    DEEPSEEK_RETRY_ATTEMPTS, DEEPSEEK_RETRY_DELAY,
)


# ─── SAFE CONVERSIONS ────────────────────────────────────────────────────────

def _safe_float(val):
    """Convert value to float, return None if not possible."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val):
    """Convert value to int, return None if not possible."""
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


# ─── API KEY RESOLUTION ───────────────────────────────────────────────────────

def _resolve_api_key():
    """Resolve DeepSeek API key: env -> ~/.hermes/.env -> ~/.hermes/config.yaml."""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key

    # Try ~/.hermes/.env
    env_path = os.path.expanduser("~/.hermes/.env")
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
                    if k == "DEEPSEEK_API_KEY" and v:
                        return v
    except (FileNotFoundError, PermissionError):
        pass

    # Try ~/.hermes/config.yaml
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
    except ImportError:
        pass
    except Exception:
        pass

    return None


# ─── DEEPSEEK API CALL ───────────────────────────────────────────────────────

def call_deepseek(system_prompt, user_message,
                  api_key=None, base_url=None, timeout=300, max_tokens=8192):
    """
    Call the DeepSeek chat API with retry logic (3 attempts, exponential backoff).
    Returns response text or None on total failure.
    """
    if api_key is None:
        api_key = _resolve_api_key()
    if not api_key:
        print("[Utils]  ❌ DeepSeek API key not configured", flush=True)
        return None
    if base_url is None:
        base_url = DEEPSEEK_BASE_URL

    headers = {
        "Authorization": f"Bearer {api_key}",
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

    last_error = None
    for attempt in range(DEEPSEEK_RETRY_ATTEMPTS):
        try:
            resp = requests.post(
                f"{base_url}/chat/completions",
                headers=headers, json=payload, timeout=timeout,
            )
            resp.raise_for_status()
            result = resp.json()
            return result["choices"][0]["message"]["content"]
        except Exception as e:
            last_error = e
            if attempt < DEEPSEEK_RETRY_ATTEMPTS - 1:
                delay = DEEPSEEK_RETRY_DELAY * (2 ** attempt)
                print(f"[Utils]  DeepSeek retry {attempt + 1}/{DEEPSEEK_RETRY_ATTEMPTS}"
                      f" in {delay}s: {e}", flush=True)
                time.sleep(delay)

    print(f"[Utils]  ❌ DeepSeek API failed after {DEEPSEEK_RETRY_ATTEMPTS} attempts: {last_error}", flush=True)
    return None


# ─── FILE WRITING ─────────────────────────────────────────────────────────────

def write_analysis_file(ticker, persona, content, output_dir):
    """
    Write an analysis file to output/{persona}/{ticker} - YYYY-MM-DD.md.
    Returns the file path.
    """
    from engine.config import DATE_STR
    persona_dir = os.path.join(output_dir, persona)
    os.makedirs(persona_dir, exist_ok=True)
    filename = f"{ticker} - {DATE_STR}.md"
    filepath = os.path.join(persona_dir, filename)
    with open(filepath, "w") as f:
        f.write(content)
    return filepath


# ─── TICKER FIXES ─────────────────────────────────────────────────────────────

def _fix_uk_ticker(ticker):
    """Fix UK ticker format: strip trailing dot, replace internal dot with dash."""
    ticker = ticker.rstrip(".")
    if "." in ticker:
        ticker = ticker.replace(".", "-")
    return ticker + ".L"
