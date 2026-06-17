#!/usr/bin/env python3
"""
Persona Output Anomaly Detector for Hermes Trading Arena V2.

Checks:
  1. QUOTE CHECKER — After each analysis file is written, read it back.
     Extract text between quote marks. Fuzzy-match against the persona's
     SOUL.md quote database. Flag any mismatch.
  2. METHODOLOGY KEYWORD CHECK — Each persona has specific methodology terms.
     If wrong terms appear in wrong persona → CROSS-CONTAMINATION flag.
  3. DRIFT DETECTION — Track over time per persona: avg sentence length,
     jargon density, first-person usage, contraction rate.
     Deviate >2σ from baseline = drift flag.
  4. LOGGING — All anomalies written to ~/hermes-trading-arena/output/anomalies/{date}.md
"""

import os
import re
import json
import logging
import datetime
import difflib
from collections import defaultdict
from typing import Dict, List, Optional, Tuple, Set
from pathlib import Path

from ..config import PROFILES_DIR, OUTPUT_DIR, PERSONAS

logger = logging.getLogger("arena.anomaly")

# ─────────────────────────────────────────────────────────────────────
# PERSONA-SPECIFIC METHODOLOGY KEYWORDS
# ─────────────────────────────────────────────────────────────────────

PERSONA_KEYWORDS: Dict[str, Set[str]] = {
    "oneil": {
        "can slim", "canslim", "can slime", "eps", "earnings per share",
        "relative strength", "rs rating", "cup with handle", "cup-with-handle",
        "flat base", "ascending base", "pivot point", "pivot buy point",
        "ibd", "investor's business daily", "institutional sponsorship",
        "accumulation/distribution", "acc/dis", "follow-through day",
        "distribution day", "market direction",
    },
    "buffet": {
        "moat", "economic moat", "owner earnings", "intrinsic value",
        "circle of competence", "margin of safety", "competitive advantage",
        "wonderful business", "fairy tale", "purchase price",
        "float", "insurance float", "book value", "berkshire",
        "acquire", "acquisition", "durable competitive",
    },
    "lynch": {
        "peg", "peg ratio", "tenbagger", "ten bagger",
        "dividend reinvestment", "earnings acceleration",
        "p/e ratio", "growth at a reasonable price", "garp",
        "category", "slow grower", "stalwart", "fast grower",
        "cyclical", "turnaround", "asset play", "story stock",
        "dow jones", "div yield", "earnings multiple",
    },
    "minervini": {
        "vcp", "vcp contraction", "volatility contraction pattern",
        "sepa", "specific entry point analysis", "sepa methodology",
        "contraction", "tight", "risk first", "vcp pattern",
        "trend template", "lower lows", "tight range",
        "ma breakout", "momentum", "relative strength",
        "superformance", "minervini",
    },
    "qullamaggie": {
        "episodic pivot", "episodic", "pivot",
        "breakaway gap", "post-earnings drift",
        "high relative volume", "high relative strength",
        "monthly chart", "weekly chart", "strong weekly close",
        "secondary breakout", "resistance break",
    },
    "david-ryan": {
        "earnings acceleration", "accelerating earnings",
        "fourth consecutive", "eps surprise", "earnings growth",
        "three time champion", "ibd champion",
        "institutional buying", "volume surge",
        "relative strength line", "rs line",
        "breakout volume", "holding period",
    },
    "matt-caruso": {
        "atr", "average true range", "position sizing",
        "risk management", "risk per trade", "stop loss",
        "2% rule", "market structure", "higher timeframe",
        "risk/reward", "r:r", "bet sizing", "edge",
        "position management", "scaling in", "scaling out",
    },
    "brian-shannon": {
        "avwap", "anchored vwap", "volume weighted average price",
        "vwap", "volume profile", "hypo volume bars",
        "volume at price", "value area", "point of control",
        "auction market", "order flow", "cumulative delta",
        "bid/ask imbalance", "tick volume", "responsive vs initiative",
    },
    "dan-zanger": {
        "corkscrew", "corkscrew pattern", "chart pattern momentum",
        "megaphone top", "flag", "pennant", "wedge",
        "bull flag", "bear flag", "ascending triangle",
        "double bottom", "head and shoulders",
        "pennant breakout", "flag breakout",
        "power breakout", "momentum shift",
    },
    "nick-schmidt": {
        "weekly chart", "weekly", "10/30 sma", "sma10", "sma30",
        "uptrend", "trend structure", "pullback",
        "trend line", "swing", "swing high", "swing low",
        "impulse move", "correction", "trend continuation",
        "distribution", "accumulation", "higher low",
    },
}

# ─────────────────────────────────────────────────────────────────────
# UTILITY FUNCTIONS
# ─────────────────────────────────────────────────────────────────────

def _extract_all_quotes_from_soul(persona: str) -> List[Tuple[str, str]]:
    """Extract all verbatim quotes + sources from a persona's SOUL.md."""
    from ..persona_engine import extract_verbatim_quotes

    soul_path = os.path.join(PROFILES_DIR, persona, "SOUL.md")
    try:
        with open(soul_path, "r") as f:
            content = f.read()
    except FileNotFoundError:
        logger.warning("SOUL.md not found for %s", persona)
        return []
    except Exception as e:
        logger.warning("Error reading SOUL.md for %s: %s", persona, e)
        return []

    return extract_verbatim_quotes(content, persona)


def _extract_quotes_from_text(text: str) -> List[str]:
    """Extract text between double-quote marks from analysis content."""
    quotes = []
    # Standard double quotes: "..."
    for m in re.finditer(r'"([^"]{10,})"', text):
        quotes.append(m.group(1).strip())
    # Fancy/curly quotes: \u201c...\u201d
    for m in re.finditer(r'\u201c([^\u201d]{10,})\u201d', text):
        quotes.append(m.group(1).strip())
    # Single quotes for phrase-length only
    for m in re.finditer(r"'([^']{20,})'", text):
        quotes.append(m.group(1).strip())
    return quotes


def _fuzzy_match_quote(candidate: str, known_quotes: List[Tuple[str, str]], threshold: float = 0.75) -> Optional[str]:
    """Fuzzy-match a candidate quote against known quote texts.
    Returns the matched known quote text if similarity >= threshold, else None.
    """
    best_score = 0.0
    best_match = None
    for q_text, _ in known_quotes:
        # Normalize both
        c_norm = re.sub(r'\s+', ' ', candidate.strip().lower())
        q_norm = re.sub(r'\s+', ' ', q_text.strip().lower())
        score = difflib.SequenceMatcher(None, c_norm, q_norm).ratio()

        # Also check substring containment
        if len(c_norm) > 30 and (c_norm in q_norm or q_norm in c_norm):
            score = max(score, 0.90)

        if score > best_score:
            best_score = score
            best_match = q_text

    if best_score >= threshold:
        return best_match
    return None


# ─────────────────────────────────────────────────────────────────────
# 1. QUOTE CHECKER
# ─────────────────────────────────────────────────────────────────────

def check_quotes(
    persona: str,
    analysis_text: str,
    known_quotes: Optional[List[Tuple[str, str]]] = None,
) -> List[dict]:
    """Check that quoted text in the analysis matches the persona's SOUL.md quotes.
    Returns list of anomaly dicts: {type, severity, detail, quote, match}
    """
    if known_quotes is None:
        known_quotes = _extract_all_quotes_from_soul(persona)

    if not known_quotes:
        return []  # No quotes to check against, skip

    anomalies = []
    extracted = _extract_quotes_from_text(analysis_text)
    # Filter out very short fragments and source citations like URLs
    extracted = [q for q in extracted if len(q) > 20 and 'http' not in q[:30]]

    for candidate in extracted:
        match = _fuzzy_match_quote(candidate, known_quotes)
        if match is None:
            anomalies.append({
                "type": "quote_mismatch",
                "severity": "medium",
                "persona": persona,
                "detail": f"Unverified quote in {persona}'s analysis: "
                          f'"{candidate[:120]}{"..." if len(candidate) > 120 else ""}"',
                "quote": candidate,
                "match": None,
            })

    return anomalies


# ─────────────────────────────────────────────────────────────────────
# 2. METHODOLOGY KEYWORD CROSS-CONTAMINATION CHECK
# ─────────────────────────────────────────────────────────────────────

def check_cross_contamination(persona: str, text: str) -> List[dict]:
    """Check if text for one persona uses methodology keywords belonging to another.
    Returns list of anomaly dicts.
    """
    anomalies = []
    text_lower = text.lower()

    our_keywords = PERSONA_KEYWORDS.get(persona, set())
    for other_persona, keywords in PERSONA_KEYWORDS.items():
        if other_persona == persona:
            continue

        for kw in keywords:
            # Skip keywords that naturally belong to multiple personas
            shared_keywords = {"eps", "earnings", "weekly", "weekly chart",
                               "atr", "vwap", "stop loss", "risk/reward",
                               "relative strength", "volume", "momentum"}
            if kw in shared_keywords:
                continue

            if kw in text_lower:
                anomalies.append({
                    "type": "cross_contamination",
                    "severity": "high",
                    "persona": persona,
                    "detail": f"Found '{kw}' (methodology keyword for {other_persona}) "
                              f"in {persona}'s analysis",
                    "keyword": kw,
                    "source_persona": other_persona,
                })

    return anomalies


# ─────────────────────────────────────────────────────────────────────
# 3. DRIFT DETECTION
# ─────────────────────────────────────────────────────────────────────

# Baseline storage path
DRIFT_BASELINE_DIR = os.path.join(PROFILES_DIR, "..", ".arena_drift")
DRIFT_BASELINE_PATH = os.path.join(DRIFT_BASELINE_DIR, "drift_baselines.json")


def _compute_text_metrics(text: str) -> Dict[str, float]:
    """Compute stylistic metrics for a piece of text."""
    if not text:
        return {"avg_sentence_length": 0, "jargon_density": 0,
                "first_person_rate": 0, "contraction_rate": 0,
                "quote_density": 0}

    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 5]
    total_sentences = len(sentences) if sentences else 1

    # Avg sentence length (words)
    word_counts = [len(s.split()) for s in sentences]
    avg_sentence_length = sum(word_counts) / total_sentences if word_counts else 0

    # Jargon density: count methodology keywords / total words
    all_jargon = set()
    for kws in PERSONA_KEYWORDS.values():
        all_jargon.update(kws)
    total_words = len(text.split())
    jargon_count = sum(1 for kw in all_jargon if kw in text.lower())
    jargon_density = jargon_count / total_words if total_words > 0 else 0

    # First-person usage (I, me, my, we, our)
    first_person_pattern = re.compile(r'\b(?:I|me|my|mine|myself|we|us|our|ours)\b', re.IGNORECASE)
    first_person_count = len(first_person_pattern.findall(text))
    first_person_rate = first_person_count / total_words if total_words > 0 else 0

    # Contraction rate
    contraction_pattern = re.compile(r"\b\w+'(?:m|re|ve|ll|d|t|s|nt)\b", re.IGNORECASE)
    contraction_count = len(contraction_pattern.findall(text))
    contraction_rate = contraction_count / total_words if total_words > 0 else 0

    # Quote density
    quote_chars = text.count('"') + text.count('\u201c') + text.count('\u201d') + text.count("'")
    quote_density = quote_chars / len(text) if len(text) > 0 else 0

    return {
        "avg_sentence_length": round(avg_sentence_length, 2),
        "jargon_density": round(jargon_density, 6),
        "first_person_rate": round(first_person_rate, 6),
        "contraction_rate": round(contraction_rate, 6),
        "quote_density": round(quote_density, 6),
        "total_words": total_words,
        "total_sentences": total_sentences,
    }


def _load_drift_baselines() -> Dict[str, Dict]:
    """Load drift baselines from JSON file."""
    baseline_path = os.path.expanduser(DRIFT_BASELINE_PATH)
    if os.path.isfile(baseline_path):
        try:
            with open(baseline_path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_drift_baselines(baselines: Dict):
    """Save drift baselines to JSON file."""
    baseline_path = os.path.expanduser(DRIFT_BASELINE_PATH)
    os.makedirs(os.path.dirname(baseline_path), exist_ok=True)
    try:
        with open(baseline_path, "w") as f:
            json.dump(baselines, f, indent=2)
    except Exception as e:
        logger.warning("Could not save drift baselines: %s", e)


def check_drift(persona: str, text: str) -> List[dict]:
    """Check if current analysis text drifts from baseline metrics.
    Returns list of anomaly dicts if >2σ deviation detected.
    """
    baselines = _load_drift_baselines()
    current = _compute_text_metrics(text)

    anomalies = []
    persona_bl = baselines.get(persona, {})

    if not persona_bl or "samples" not in persona_bl or persona_bl["samples"] < 3:
        # Not enough samples — record and skip
        if persona not in baselines:
            baselines[persona] = {
                "samples": 1,
                "mean": current,
                "m2": {k: 0.0 for k in current} if isinstance(current, dict) else {},
            }
        else:
            # Welford's online update
            bl = baselines[persona]
            n = bl["samples"]
            for metric in ["avg_sentence_length", "jargon_density",
                           "first_person_rate", "contraction_rate"]:
                if metric not in current or metric not in bl.get("mean", {}):
                    continue
                val = current[metric]
                if not isinstance(val, (int, float)):
                    continue
                delta = val - bl["mean"].get(metric, val)
                bl["mean"][metric] = bl["mean"].get(metric, 0) + delta / (n + 1)
                delta2 = val - bl["mean"][metric]
                bl["m2"][metric] = bl["m2"].get(metric, 0) + delta * delta2
            bl["samples"] = n + 1
        _save_drift_baselines(baselines)
        return []

    # We have enough samples — compute std and check for drift
    bl = persona_bl
    n = bl["samples"]
    for metric in ["avg_sentence_length", "jargon_density",
                   "first_person_rate", "contraction_rate"]:
        if metric not in current or metric not in bl.get("mean", {}):
            continue
        mean = bl["mean"][metric]
        variance = bl["m2"].get(metric, 0) / (n - 1) if n > 1 else 0
        std = variance ** 0.5
        val = current[metric]
        if std > 0 and abs(val - mean) > 2 * std:
            direction = "above" if val > mean else "below"
            anomalies.append({
                "type": "drift",
                "severity": "medium",
                "persona": persona,
                "detail": f"Drift detected: {metric} ({val:.4f}) is {direction} "
                          f"2σ from baseline ({mean:.4f} ± {2*std:.4f})",
                "metric": metric,
                "current_value": round(val, 6),
                "baseline_mean": round(mean, 6),
                "baseline_std": round(std, 6),
            })

    # Update baseline with this sample
    n = bl["samples"]
    for metric in ["avg_sentence_length", "jargon_density",
                   "first_person_rate", "contraction_rate"]:
        if metric not in current or metric not in bl.get("mean", {}):
            continue
        val = current[metric]
        if not isinstance(val, (int, float)):
            continue
        delta = val - bl["mean"][metric]
        bl["mean"][metric] = bl["mean"][metric] + delta / (n + 1)
        delta2 = val - bl["mean"][metric]
        bl["m2"][metric] = bl["m2"].get(metric, 0) + delta * delta2
    bl["samples"] = n + 1
    _save_drift_baselines(baselines)

    return anomalies


# ─────────────────────────────────────────────────────────────────────
# 4. READ OUTPUT FILE
# ─────────────────────────────────────────────────────────────────────

def read_analysis_file(filepath: str) -> Tuple[str, str]:
    """Read an analysis file and extract its content and persona.
    Returns (persona, content).
    Supports YAML-frontmatter format and simple header format.
    Falls back to persona name from directory structure.
    """
    try:
        with open(filepath, "r") as f:
            raw = f.read()
    except Exception as e:
        logger.warning("Cannot read %s: %s", filepath, e)
        return ("", "")

    persona = ""

    # Try YAML frontmatter parsing (output_writer.py format)
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1]
            content = parts[2]
            for line in frontmatter.strip().split("\n"):
                if line.startswith("persona:"):
                    persona = line.split(":", 1)[1].strip()
        else:
            content = raw
    else:
        content = raw

    # Fallback: extract persona from directory path
    # Path: .../{date_str}/{persona}/{ticker}.md
    if not persona:
        parts = filepath.replace(os.sep, "/").split("/")
        for i, part in enumerate(parts):
            if part in PERSONAS:
                persona = part
                break

    return persona, content


# ─────────────────────────────────────────────────────────────────────
# 5. LOG ANOMALIES
# ─────────────────────────────────────────────────────────────────────

def log_anomalies(anomalies: List[dict], date_str: str):
    """Write all anomalies to anomaly log file."""
    anomaly_dir = os.path.expanduser("~/hermes-trading-arena/output/anomalies")
    os.makedirs(anomaly_dir, exist_ok=True)
    filepath = os.path.join(anomaly_dir, f"{date_str}.md")

    if not anomalies:
        # Write a clean log (or append to existing)
        mode = "a" if os.path.isfile(filepath) else "w"
        with open(filepath, mode) as f:
            if mode == "w":
                f.write(f"# Anomaly Report — {date_str}\n\n")
                f.write("_No anomalies detected._\n\n")
        print(f"[Anomaly] ✓ No anomalies — {filepath}", flush=True)
        return filepath

    # Group by type
    by_type = defaultdict(list)
    for a in anomalies:
        by_type[a["type"]].append(a)

    severity_order = {"high": 0, "medium": 1, "low": 2}
    by_type_sorted = dict(sorted(
        by_type.items(),
        key=lambda kv: min((severity_order.get(a.get("severity", "low"), 3)
                            for a in kv[1]), default=3)
    ))

    lines = [
        f"# Anomaly Report — {date_str}",
        "",
        f"**Total Anomalies:** {len(anomalies)}",
        f"**Generated:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        "",
    ]

    for anomaly_type, items in by_type_sorted.items():
        lines.append(f"## {anomaly_type.replace('_', ' ').title()} ({len(items)})")
        lines.append("")

        for idx, a in enumerate(items, 1):
            severity = a.get("severity", "low")
            icon = {"high": "🚨", "medium": "⚠️", "low": "ℹ️"}.get(severity, "ℹ️")
            lines.append(f"### {icon} #{idx} — {a.get('persona', '?')}")
            lines.append("")
            lines.append(f"- **Type:** {a['type']}")
            lines.append(f"- **Severity:** {severity.upper()}")
            lines.append(f"- **Persona:** {a.get('persona', '?')}")
            lines.append(f"- **Detail:** {a['detail']}")

            # Type-specific fields
            if a["type"] == "cross_contamination":
                lines.append(f"- **Keyword:** `{a.get('keyword', '?')}`")
                lines.append(f"- **Source Persona:** {a.get('source_persona', '?')}")
            elif a["type"] == "drift":
                lines.append(f"- **Metric:** `{a.get('metric', '?')}`")
                lines.append(f"- **Current:** {a.get('current_value', '?')}")
                lines.append(f"- **Baseline Mean:** {a.get('baseline_mean', '?')}")
                lines.append(f"- **Baseline Std:** {a.get('baseline_std', '?')}")
            elif a["type"] == "quote_mismatch":
                lines.append(f"- **Quoted text:** \"{a.get('quote', '')[:80]}{'...' if len(a.get('quote', '')) > 80 else ''}\"")

            lines.append("")

    with open(filepath, "w") as f:
        f.write("\n".join(lines))

    print(f"[Anomaly] ✓ {len(anomalies)} anomalies logged → {filepath}", flush=True)
    return filepath


# ─────────────────────────────────────────────────────────────────────
# 6. FULL ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────

def run_checks_on_file(filepath: str, date_str: str) -> List[dict]:
    """Run all anomaly checks on a single analysis file."""
    persona, content = read_analysis_file(filepath)
    if not content:
        return []

    all_anomalies = []

    # 1. Quote check
    try:
        quote_anomalies = check_quotes(persona, content)
        all_anomalies.extend(quote_anomalies)
    except Exception as e:
        logger.warning("Quote check failed for %s: %s", filepath, e)

    # 2. Cross-contamination check
    if persona:
        try:
            cc_anomalies = check_cross_contamination(persona, content)
            all_anomalies.extend(cc_anomalies)
        except Exception as e:
            logger.warning("Cross-contamination check failed for %s: %s", filepath, e)

    # 3. Drift check
    if persona:
        try:
            drift_anomalies = check_drift(persona, content)
            all_anomalies.extend(drift_anomalies)
        except Exception as e:
            logger.warning("Drift check failed for %s: %s", filepath, e)

    return all_anomalies


def run_detection(date_str: Optional[str] = None) -> str:
    """Run anomaly detection across ALL output files for a given date.
    Logs results and returns the anomaly file path.
    """
    date_str = date_str or datetime.date.today().isoformat()
    output_dir = os.path.expanduser(OUTPUT_DIR)
    date_dir = os.path.join(output_dir, date_str)

    if not os.path.isdir(date_dir):
        logger.warning("No output directory for %s at %s", date_str, date_dir)
        # Create empty anomaly report
        return log_anomalies([], date_str)

    # Scan all .md files in date_dir/persona/ticker.md
    all_anomalies = []
    processed = 0

    for persona_dir in sorted(os.listdir(date_dir)):
        persona_path = os.path.join(date_dir, persona_dir)
        if not os.path.isdir(persona_path) or persona_dir == "anomalies":
            continue

        for filename in sorted(os.listdir(persona_path)):
            if not filename.endswith(".md"):
                continue
            filepath = os.path.join(persona_path, filename)
            anomalies = run_checks_on_file(filepath, date_str)
            all_anomalies.extend(anomalies)
            processed += 1

            if anomalies:
                for a in anomalies:
                    print(f"[Anomaly] {a['severity'].upper()} {a['type']}: "
                          f"{a['persona']}/{filename} — {a['detail'][:100]}", flush=True)

    print(f"[Anomaly] Processed {processed} files, found {len(all_anomalies)} anomalies", flush=True)
    return log_anomalies(all_anomalies, date_str)


# ─────────────────────────────────────────────────────────────────────
# CLI ENTRY POINT
# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    result_path = run_detection(date_arg)
    print(f"\nAnomaly report written to: {result_path}")
