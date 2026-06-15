#!/usr/bin/env python3
"""
engine/orchestrator.py — Thin coordinator for the Hermes Trading Arena.
Phases:
  0. Load config
  1. Collect market data (via data_collector)
  2. Filter & assign stocks to personas (via persona_filter)
  3. Spawn persona_runner subprocesses (max 4 concurrent via multiprocessing)
  4. Review gate checks all output files
  5. Accuracy tracker scores last week
  6. Summary
"""

import json
import os
import sys
import time
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed

from engine.config import (
    PERSONAS, OUTPUT_DIR, PROFILES_DIR, DATE_STR,
    DEEPSEEK_BASE_URL, CACHE_PATH,
)
from engine.utils import _resolve_api_key
from engine.data_collector import fetch_market_data
from engine.persona_filter import assign_stocks_to_personas
from engine.review_gate import reject_negative_analysis


# ─── CONFIG LOADING ──────────────────────────────────────────────────────────

def load_config_yaml():
    """Load config.yaml at repo root. Returns dict with mode, target_stocks, etc."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg_path = os.path.join(repo_root, "config.yaml")
    cfg = {"mode": "TARGET_STOCKS", "target_stocks": [], "max_concurrent_personas": 4}
    try:
        import yaml
        if os.path.isfile(cfg_path):
            with open(cfg_path) as f:
                loaded = yaml.safe_load(f) or {}
            cfg.update(loaded)
    except ImportError:
        print("[Orch]  yaml not available, using defaults", flush=True)
    except Exception as e:
        print(f"[Orch]  Config load error: {e}", flush=True)
    return cfg


# ─── TARGET STOCKS MODE ─────────────────────────────────────────────────────

def analyze_target_stocks(target_list):
    """When mode is TARGET_STOCKS, fetch data for a specific list."""
    from engine.data_collector import analyze_ticker
    results = {}
    for ticker in target_list:
        try:
            data = analyze_ticker(ticker)
            if data:
                results[ticker] = data
            else:
                print(f"[Orch]  ⚠ {ticker} — no data, skipping", flush=True)
        except Exception as e:
            print(f"[Orch]  ⚠ {ticker} — error: {e}", flush=True)
    return results


# ─── PERSONA RUNNER SUBPROCESS ───────────────────────────────────────────────

def _persona_worker(params):
    """
    Spawn a persona_runner subprocess for one persona.
    Called via ProcessPoolExecutor.
    """
    persona = params["persona"]
    stocks = params["stocks"]
    output_dir = params["output_dir"]

    if not stocks:
        return {"persona": persona, "results": [], "error": "no stocks"}

    payload = json.dumps({"persona": persona, "stocks": stocks, "output_dir": output_dir})
    python_bin = sys.executable
    script = os.path.join(os.path.dirname(os.path.dirname(__file__)), "engine", "persona_runner.py")

    try:
        proc = subprocess.Popen(
            [python_bin, script],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        stdout, stderr = proc.communicate(input=payload, timeout=7200)

        if proc.returncode != 0:
            return {"persona": persona, "results": [], "error": f"exit {proc.returncode}"}

        if "---WORKER_RESULT---" in stdout:
            result_json = stdout.split("---WORKER_RESULT---")[1].strip()
        else:
            return {"persona": persona, "results": [], "error": "no result marker"}

        return json.loads(result_json)
    except subprocess.TimeoutExpired:
        proc.kill()
        return {"persona": persona, "results": [], "error": "timeout"}
    except Exception as e:
        return {"persona": persona, "results": [], "error": str(e)}


# ─── REVIEW GATE ─────────────────────────────────────────────────────────────

def run_review_gate(persona_results):
    """Check all output files. Returns (kept, deleted)."""
    kept = 0
    deleted = 0
    for persona, result in persona_results.items():
        r = result.get("results", [])
        for item in r:
            fp = item.get("file_path")
            if fp and os.path.isfile(fp):
                if reject_negative_analysis(fp):
                    kept += 1
                else:
                    deleted += 1
    return kept, deleted


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    start = time.monotonic()
    cfg = load_config_yaml()
    max_workers = cfg.get("max_concurrent_personas", 4)

    print(f"[Orch]  === ARENA RUNNER — {DATE_STR} ===", flush=True)
    print(f"[Orch]  Mode: {cfg.get('mode', 'TARGET_STOCKS')}", flush=True)
    print(f"[Orch]  Max concurrent personas: {max_workers}", flush=True)

    # Resolve API key
    if not _resolve_api_key():
        print("[Orch]  ⚠ No DeepSeek API key configured", flush=True)

    # ─── Phase 1: Market Data ────────────────────────────────────────────
    if cfg.get("mode", "").upper() == "TARGET_STOCKS":
        target = cfg.get("target_stocks", [])
        print(f"[Orch]  Phase 1: Fetching {len(target)} target stocks...", flush=True)
        all_stocks = analyze_target_stocks(target)
    else:
        print(f"[Orch]  Phase 1: TV scanning global markets...", flush=True)
        all_stocks, from_cache = fetch_market_data(use_cache=True)
        print(f"[Orch]  Phase 1: {len(all_stocks)} tickers (cached={from_cache})", flush=True)

    # ─── Phase 2: Filter & Assign ───────────────────────────────────────
    print(f"[Orch]  Phase 2: Assigning stocks to {len(PERSONAS)} personas...", flush=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    persona_stocks = assign_stocks_to_personas(all_stocks)
    total_assigned = sum(len(v) for v in persona_stocks.values())

    # ─── Phase 3: Run Personas ───────────────────────────────────────────
    print(f"[Orch]  Phase 3: Running {len(PERSONAS)} personas ({total_assigned} stocks)...", flush=True)

    tasks = []
    for persona in PERSONAS:
        tasks.append({
            "persona": persona,
            "stocks": persona_stocks.get(persona, {}),
            "output_dir": OUTPUT_DIR,
        })

    all_results = {}
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        fut_map = {}
        for t in tasks:
            # Skip personas with no stocks
            if not t["stocks"]:
                all_results[t["persona"]] = {"persona": t["persona"], "results": [], "error": "no stocks"}
                print(f"[Orch]  Skipping {t['persona']} — no stocks assigned", flush=True)
                continue
            fut = pool.submit(_persona_worker, t)
            fut_map[fut] = t["persona"]

        for fut in as_completed(fut_map):
            persona = fut_map[fut]
            try:
                result = fut.result()
                all_results[persona] = result
                n = len(result.get("results", []))
                print(f"[Orch]  ✓ {persona}: {n} stocks analyzed", flush=True)
            except Exception as e:
                print(f"[Orch]  ❌ {persona}: {e}", flush=True)
                all_results[persona] = {"persona": persona, "results": [], "error": str(e)}

    # ─── Phase 4: Review Gate ────────────────────────────────────────────
    print(f"[Orch]  Phase 4: Review gate...", flush=True)
    kept, deleted = run_review_gate(all_results)
    print(f"[Orch]  Review: {kept} kept, {deleted} deleted", flush=True)

    # ─── Phase 5: Accuracy Tracker ───────────────────────────────────────
    print(f"[Orch]  Phase 5: Scoring last week's picks...", flush=True)
    try:
        from engine.accuracy_tracker import score_week_picks
        score_week_picks()
    except Exception as e:
        print(f"[Orch]  ⚠ Accuracy tracking skipped: {e}", flush=True)

    # ─── Summary ─────────────────────────────────────────────────────────
    elapsed = time.monotonic() - start
    total_files = sum(len(r.get("results", [])) for r in all_results.values())
    total_words = sum(
        sum(it.get("word_count", 0) for it in r.get("results", []))
        for r in all_results.values()
    )
    print(f"\n[Orch]  ✅ Complete in {elapsed:.0f}s", flush=True)
    print(f"[Orch]  Summary: {total_files} files, {total_words} words"
          f" ({kept} accepted, {deleted} rejected)", flush=True)


if __name__ == "__main__":
    main()
