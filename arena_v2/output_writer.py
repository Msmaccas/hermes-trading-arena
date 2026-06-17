#!/usr/bin/env python3
"""Output writer: save analysis files, master index, git push."""

import os, sys, json, datetime, logging, subprocess
from typing import Dict, Optional
from pathlib import Path

from .config import OUTPUT_DIR
from .generator import extract_stance

logger = logging.getLogger("arena")

# ─── Write individual analysis file ──────────────────────────────────────

def write_analysis_file(
    persona: str,
    ticker: str,
    content: str,
    stock_data: dict,
    date_str: str,
    output_dir: Optional[str] = None,
):
    """Write one analysis file.
    Path: {output_dir}/{date_str}/{persona}/{ticker}.md
    """
    base_dir = os.path.expanduser(output_dir or OUTPUT_DIR)
    persona_dir = os.path.join(base_dir, date_str, persona)
    os.makedirs(persona_dir, exist_ok=True)

    stance = extract_stance(content)

    # Build indicators list
    ind = stock_data.get("indicators", {}) or {}
    ind_keys = list(ind.keys())

    # Frontmatter
    frontmatter = [
        "---",
        f"persona: {persona}",
        f"ticker: {ticker}",
        f"date: {date_str}",
        f"stance: {stance}",
        f"indicators: {json.dumps(ind_keys)}",
        "---",
        "",
    ]

    # Sources footer
    sources = [
        "",
        "---",
        "### Sources",
        f"- Yahoo Finance: https://finance.yahoo.com/quote/{ticker}",
    ]

    full_content = "\n".join(frontmatter) + content + "\n".join(sources)

    filepath = os.path.join(persona_dir, f"{ticker}.md")
    with open(filepath, "w") as f:
        f.write(full_content)

    word_count = len(content.split())
    file_size = os.path.getsize(filepath)
    print(f"[Writer] ✓ {persona}/{ticker}.md — {word_count} words, {file_size // 1024}KB", flush=True)

    return filepath

# ─── Write master index file ────────────────────────────────────────────

def write_master_index(
    all_results: Dict[str, Dict[str, dict]],
    date_str: str,
    stock_data: Dict,
    output_dir: Optional[str] = None,
):
    """Write master index file.
    Path: {output_dir}/{date_str}/INDEX.md
    """
    base_dir = os.path.expanduser(output_dir or OUTPUT_DIR)
    date_dir = os.path.join(base_dir, date_str)
    os.makedirs(date_dir, exist_ok=True)

    lines = [
        f"# Trading Arena — {date_str}",
        "",
        f"**Total Personas:** {len(all_results)}",
        f"**Total Analyses:** {sum(len(stocks) for stocks in all_results.values())}",
        "",
        "## Overview",
        "",
    ]

    total_full = 0
    total_words = 0

    for persona in sorted(all_results.keys()):
        stocks = all_results[persona]
        if not stocks:
            continue

        persona_words = sum(
            s.get("word_count", 0) for s in stocks.values() if isinstance(s, dict)
        )
        total_full += len(stocks)
        total_words += persona_words

        lines.append(f"### {persona.title()}")
        lines.append(f"- **Analyses:** {len(stocks)} stocks")
        lines.append(f"- **Total Words:** {persona_words}")
        lines.append("")

        for ticker, result in sorted(stocks.items()):
            if isinstance(result, dict):
                wc = result.get("word_count", 0)
                stance = extract_stance(result.get("content", ""))
                success = result.get("success", False)
                mark = "✓" if success else "✗"
                lines.append(f"  - [{mark}] `{ticker}` — {wc} words, {stance}")
            else:
                lines.append(f"  - `{ticker}` — summary")

        lines.append("")

    lines.extend([
        "---",
        f"**Generated:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Total Full Analyses:** {total_full}",
        f"**Total Words:** {total_words}",
        "",
        "### Legend",
        "- ✓: Full analysis completed",
        "- ✗: Analysis failed or too short",
    ])

    filepath = os.path.join(date_dir, "INDEX.md")
    with open(filepath, "w") as f:
        f.write("\n".join(lines))
    print(f"[Writer] ✓ INDEX.md — {total_full} analyses, {total_words} words", flush=True)
    return filepath

# ─── Git push ────────────────────────────────────────────────────────────

def push_to_github(output_dir: Optional[str] = None, date_str: Optional[str] = None):
    """Git add, commit, push the output directory.
    Only if git repo is configured.
    """
    base_dir = os.path.expanduser(output_dir or OUTPUT_DIR)
    if not os.path.isdir(os.path.join(base_dir, ".git")):
        # Check if git is initialized
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=base_dir,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                print(f"[Writer] Not a git repo: {base_dir}, skipping git push", flush=True)
                return
        except Exception:
            print(f"[Writer] Git check failed, skipping push", flush=True)
            return

    try:
        subprocess.run(["git", "add", "-A"], cwd=base_dir, check=True, timeout=30)
        msg = f"Arena update {date_str or datetime.date.today().isoformat()}"
        subprocess.run(["git", "commit", "-m", msg], cwd=base_dir, timeout=30)
        subprocess.run(["git", "push"], cwd=base_dir, timeout=60)
        print(f"[Writer] ✓ Git push complete", flush=True)
    except subprocess.CalledProcessError as e:
        print(f"[Writer] Git operation: {e}", flush=True)
    except Exception as e:
        print(f"[Writer] Git error: {e}", flush=True)
