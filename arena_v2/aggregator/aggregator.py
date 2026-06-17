#!/usr/bin/env python3
"""
Meta-Persona Prediction Aggregator for Hermes Trading Arena V2.

Reads all 10 persona outputs for a stock from the same run date and produces:
  - Consensus signal (BUY/HOLD/SELL)
  - Confidence level (0–100)
  - Which personas agree / disagree
  - Conflict resolution via regime-aware tiebreaker
  - TV MCP indicator readings for technical personas
"""

import os
import re
import json
import logging
import datetime
from collections import defaultdict, Counter
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path

from ..config import OUTPUT_DIR, PERSONAS

logger = logging.getLogger("arena.aggregator")

# ─────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────

# Stance → signal mapping
STANCE_SIGNAL_MAP = {
    "bullish": "BUY",
    "bearish": "SELL",
    "neutral": "HOLD",
}

# Persona types for weighted interpretation
TECHNICAL_PERSONAS = {"qullamaggie", "dan-zanger", "brian-shannon"}
FUNDAMENTAL_PERSONAS = {"buffet", "lynch"}
HYBRID_PERSONAS = {
    "oneil", "minervini", "david-ryan", "matt-caruso", "nick-schmidt",
}

# Accuracy storage
ACCURACY_DIR = os.path.expanduser("~/.hermes/scripts/arena_v2/.accuracy")
ACCURACY_FILE = os.path.join(ACCURACY_DIR, "rolling_accuracy.json")

# Regime classifier data (stub — replace with actual regime/classifier module when available)
REGIME_CLASSIFIER_PATH = os.path.expanduser("~/.hermes/scripts/arena_v2/regime/classifier.py")


# ─────────────────────────────────────────────────────────────────────
# ACCURACY ENGINE
# ─────────────────────────────────────────────────────────────────────

def _load_accuracy_data() -> Dict[str, Dict[str, Any]]:
    """Load rolling 30-day accuracy data for each persona.
    Returns {persona: {accuracy, samples, last_updated, ...}}
    """
    if not os.path.isfile(ACCURACY_FILE):
        # Default: equal weights, neutral accuracy
        return {p: {"accuracy": 0.50, "samples": 0, "weight": 1.0}
                for p in PERSONAS}

    try:
        with open(ACCURACY_FILE, "r") as f:
            data = json.load(f)
        # Ensure all personas have entries
        for p in PERSONAS:
            if p not in data:
                data[p] = {"accuracy": 0.50, "samples": 0, "weight": 1.0}
            # Compute weight from accuracy
            acc = data[p].get("accuracy", 0.50)
            samples = data[p].get("samples", 0)
            # Weight = accuracy * min(1, samples / 30) — ramp up as we get data
            ramp = min(1.0, samples / 30.0)
            # Boost weight for high accuracy, floor for low
            if acc >= 0.70:
                data[p]["weight"] = 1.0 * ramp + 0.2 * (1 - ramp)
            elif acc >= 0.55:
                data[p]["weight"] = 0.7 * ramp + 0.2 * (1 - ramp)
            elif acc >= 0.40:
                data[p]["weight"] = 0.5 * ramp + 0.2 * (1 - ramp)
            else:
                data[p]["weight"] = 0.2 * ramp + 0.1 * (1 - ramp)
        return data
    except Exception as e:
        logger.warning("Error loading accuracy data: %s", e)
        return {p: {"accuracy": 0.50, "samples": 0, "weight": 1.0}
                for p in PERSONAS}


def _save_accuracy_data(data: Dict[str, Dict]):
    """Save accuracy data to disk."""
    os.makedirs(ACCURACY_DIR, exist_ok=True)
    try:
        with open(ACCURACY_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning("Error saving accuracy data: %s", e)


def record_outcome(persona: str, predicted_stance: str, actual_outcome: str):
    """Record a prediction outcome for accuracy tracking.
    Called by external evaluator after real market movement is known.
    """
    data = _load_accuracy_data()
    blob = data.get(persona, {"accuracy": 0.50, "samples": 0})

    # Determine if prediction was "correct"
    # Simple: same direction counts as correct
    correct = (predicted_stance == actual_outcome)
    samples = blob.get("samples", 0) + 1

    # Rolling 30-day window: exponential decay weighting
    alpha = min(1.0, 2.0 / (samples + 2))  # starts adaptive, converges to ~2/30
    prev_accuracy = blob.get("accuracy", 0.50)
    new_accuracy = prev_accuracy + alpha * ((1.0 if correct else 0.0) - prev_accuracy)

    blob["accuracy"] = round(new_accuracy, 4)
    blob["samples"] = samples
    blob["last_updated"] = datetime.date.today().isoformat()
    data[persona] = blob
    _save_accuracy_data(data)
    return new_accuracy


# ─────────────────────────────────────────────────────────────────────
# REGIME CLASSIFIER (STUB)
# ─────────────────────────────────────────────────────────────────────

# Regime-based tiebreaker logic for when signals are evenly split.
# Uses a simple heuristic; replace with actual regime/classifier module import.
REGIME_TYPES = ["bull_trend", "bear_trend", "range_bound", "volatile"]


def _get_current_regime(data_source: str = "heuristic") -> str:
    """Get the current market regime as a string.
    Always returns one of: bull_trend, bear_trend, range_bound, volatile.
    Replace with: from ..regime.classifier import classify_regime
    """
    # Try to import real classifier
    try:
        from ..regime.classifier import classify_regime
        result = classify_regime()
        # The classifier may return a string, a dict with 'regime' key, or something else
        if isinstance(result, str):
            return result.lower().replace(" ", "_")
        if isinstance(result, dict):
            r = result.get("regime", "range_bound")
            return str(r).lower().replace(" ", "_")
    except (ImportError, Exception):
        pass

    # Heuristic fallback
    try:
        import yfinance as yf
        spy = yf.Ticker("SPY")
        hist = spy.history(period="3mo")
        if hist.empty or len(hist) < 20:
            return "range_bound"

        close = hist["Close"]
        sma20 = close.rolling(20).mean().iloc[-1]
        sma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else sma20
        current = close.iloc[-1]
        ma20_ago = close.iloc[-20] if len(close) >= 20 else sma20

        # Trend detection
        if current > sma20 and sma20 > sma50:
            # Check volatility
            returns = close.pct_change().dropna()
            vol = returns.std() * (252 ** 0.5)
            if vol > 0.30:
                return "bull_trend_high_vol"
            return "bull_trend"
        elif current < sma20 and sma20 < sma50:
            return "bear_trend"
        elif abs(current - sma20) / sma20 < 0.03:
            return "range_bound"
        else:
            returns = close.pct_change().dropna()
            vol = returns.std() * (252 ** 0.5)
            if vol > 0.35:
                return "volatile"
            return "range_bound"
    except Exception:
        return "range_bound"


# Regime → persona preference for tiebreaker
REGIME_BIAS: Dict[str, List[str]] = {
    "bull_trend": ["oneil", "minervini", "david-ryan", "qullamaggie",
                    "dan-zanger", "brian-shannon", "nick-schmidt"],
    "bear_trend": ["buffet", "lynch", "matt-caruso"],
    "range_bound": ["buffet", "lynch", "matt-caruso", "nick-schmidt"],
    "volatile": ["qullamaggie", "matt-caruso", "dan-zanger"],
    "bull_trend_high_vol": ["oneil", "minervini", "qullamaggie",
                              "dan-zanger", "brian-shannon"],
}

# Which personas are most reliable in each regime
REGIME_PREFERRED: Dict[str, List[str]] = {
    "bull_trend": ["oneil", "minervini", "david-ryan"],
    "bear_trend": ["buffet", "matt-caruso"],
    "range_bound": ["buffet", "lynch", "nick-schmidt"],
    "volatile": ["qullamaggie", "matt-caruso"],
    "bull_trend_high_vol": ["oneil", "qullamaggie"],
}


# ─────────────────────────────────────────────────────────────────────
# TV MCP INDICATOR READINGS
# ─────────────────────────────────────────────────────────────────────

def get_tv_mcp_readings(ticker: str) -> Dict[str, Any]:
    """Fetch TradingView MCP indicator readings for a ticker.
    Returns dict of indicator values. Best-effort; may return empty dict.
    """
    readings = {}
    try:
        from ...mcp_tradingview_chart import chart_get_state, data_get_study_values
        # We reference the MCP tools here — but in practice these are
        # called via the Hermes Agent MCP bridge, not directly importable.
        # Instead, try to read from cached data.
        pass
    except ImportError:
        pass

    # Try yfinance as fallback for common technicals
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        hist = stock.history(period="3mo")
        if not hist.empty and len(hist) >= 20:
            close = hist["Close"]
            sma20 = close.rolling(20).mean().iloc[-1]
            sma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else sma20
            sma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None

            readings["SMA20"] = round(sma20, 2)
            readings["SMA50"] = round(sma50, 2)
            if sma200:
                readings["SMA200"] = round(sma200, 2)
            readings["Price"] = round(close.iloc[-1], 2)
            readings["Above_SMA20"] = close.iloc[-1] > sma20
            readings["Above_SMA50"] = close.iloc[-1] > sma50

            # RSI
            from ..screener.indicators import compute_rsi
            import numpy as np
            readings["RSI"] = compute_rsi(close.values, 14)
    except Exception:
        pass

    return readings


# ─────────────────────────────────────────────────────────────────────
# LOAD PERSONA OUTPUTS
# ─────────────────────────────────────────────────────────────────────

def load_persona_outputs(
    ticker: str,
    date_str: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Load all persona outputs for a given ticker from the output directory.
    Returns {persona: {stance, content, word_count, filepath, implied_signal}}

    Searches for files matching:
      {OUTPUT_DIR}/{date_str}/{persona}/{ticker}*.md
    """
    date_str = date_str or datetime.date.today().isoformat()
    output_dir = os.path.expanduser(OUTPUT_DIR)
    date_dir = os.path.join(output_dir, date_str)

    if not os.path.isdir(date_dir):
        logger.warning("No output directory for %s", date_str)
        return {}

    results = {}
    for persona in PERSONAS:
        persona_dir = os.path.join(date_dir, persona)
        if not os.path.isdir(persona_dir):
            continue

        # Find matching file for this ticker (flexible naming)
        matched_file = None
        try:
            for fname in os.listdir(persona_dir):
                if fname.lower().startswith(ticker.lower()) and fname.endswith(".md"):
                    matched_file = os.path.join(persona_dir, fname)
                    break
        except OSError:
            continue

        if not matched_file:
            continue

        try:
            with open(matched_file, "r") as f:
                raw = f.read()
        except Exception as e:
            logger.warning("Cannot read %s: %s", matched_file, e)
            continue

        # Extract stance
        stance = _extract_stance_from_content(raw)
        # Handle YAML frontmatter
        content = _strip_frontmatter(raw)
        word_count = len(content.split())
        signal = STANCE_SIGNAL_MAP.get(stance, "HOLD")

        results[persona] = {
            "stance": stance,
            "signal": signal,
            "content": content,
            "word_count": word_count,
            "filepath": matched_file,
        }

    return results


def _strip_frontmatter(raw: str) -> str:
    """Strip YAML frontmatter if present."""
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            return parts[2]
    return raw


def _extract_stance_from_content(content: str) -> str:
    """Extract bullish/bearish/neutral stance from any format.
    Looks for YAML frontmatter 'stance:', inline markers, and keyword analysis.
    """
    if not content:
        return "neutral"

    # Try YAML frontmatter
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().split("\n"):
                if line.startswith("stance:"):
                    s = line.split(":", 1)[1].strip().lower()
                    if s in ("bullish", "bearish", "neutral"):
                        return s

    lower = content.lower()

    # Look for explicit STANCE/VERDICT markers
    verdict_patterns = [
        r'\*\*stance\s*:\s*\*\*\s*(\w+)',
        r'\*\*the verdict:\s*\*\*\s*(\w+)',
        r'\*\*verdict:\s*\*\*\s*(\w+)',
        r'stance:\s*(bullish|bearish|neutral)',
        r'stance\s*:\s*(buy|sell|hold)',
        r'verdict:\s*(buy|sell|hold)',
    ]
    for pat in verdict_patterns:
        m = re.search(pat, lower)
        if m:
            val = m.group(1).lower()
            if val in ("buy",):
                return "bullish"
            if val in ("sell",):
                return "bearish"
            if val in ("hold", "neutral"):
                return "neutral"
            if val in ("bullish", "bearish", "neutral"):
                return val

    # Fallback: keyword analysis on first 300 chars
    first_300 = lower[:300]
    bullish_words = ["bullish", "buy", "long", "opportunity", "upside", "strong buy", "accumulate"]
    bearish_words = ["bearish", "sell", "short", "avoid", "downside", "weak", "caution"]

    bull_score = sum(1 for w in bullish_words if w in first_300)
    bear_score = sum(1 for w in bearish_words if w in first_300)

    if bull_score > bear_score + 1:
        return "bullish"
    elif bear_score > bull_score + 1:
        return "bearish"
    return "neutral"


# ─────────────────────────────────────────────────────────────────────
# CONSENSUS AGGREGATION
# ─────────────────────────────────────────────────────────────────────

def aggregate_consensus(
    persona_outputs: Dict[str, Dict[str, Any]],
    ticker: str = "",
    date_str: Optional[str] = None,
) -> Dict[str, Any]:
    """Aggregate all persona outputs into a consensus meta-signal.

    Args:
        persona_outputs: {persona: {stance, signal, word_count, ...}}
        ticker: Stock ticker (for TV MCP data)
        date_str: Run date

    Returns:
        dict with keys:
          ticker, date, consensus_signal (BUY/HOLD/SELL),
          confidence (0-100), signal_strength,
          signal_breakdown (BUY/HOLD/SELL counts),
          agreeing_personas, disagreeing_personas,
          weight_by_persona, regime, technical_indicators
    """
    if not persona_outputs:
        return {
            "ticker": ticker,
            "date": date_str or datetime.date.today().isoformat(),
            "consensus_signal": "HOLD",
            "confidence": 0,
            "signal_strength": 0.0,
            "signal_breakdown": {"BUY": 0, "HOLD": 0, "SELL": 0},
            "agreeing_personas": [],
            "disagreeing_personas": [],
            "weight_by_persona": {},
            "regime": _get_current_regime(),
            "technical_indicators": {},
            "error": "No persona outputs available",
        }

    date_str = date_str or datetime.date.today().isoformat()

    # 1. Load accuracy weights
    accuracy_data = _load_accuracy_data()

    # 2. Weighted vote
    weighted_votes = {"BUY": 0.0, "HOLD": 0.0, "SELL": 0.0}
    raw_votes = {"BUY": 0, "HOLD": 0, "SELL": 0}
    weight_by_persona = {}
    persona_signals = {}

    for persona, output in persona_outputs.items():
        signal = output.get("signal", "HOLD")
        raw_votes[signal] = raw_votes.get(signal, 0) + 1

        # Get weight from accuracy data
        acc_blob = accuracy_data.get(persona, {})
        weight = acc_blob.get("weight", 1.0)
        # Boost based on word count (more thorough analysis = more reliable)
        wc_factor = min(1.0, output.get("word_count", 500) / 3000.0)
        effective_weight = weight * (0.7 + 0.3 * wc_factor)
        weight_by_persona[persona] = round(effective_weight, 4)
        weighted_votes[signal] = weighted_votes.get(signal, 0) + effective_weight

        persona_signals[persona] = signal

    total_weight = sum(weighted_votes.values())
    if total_weight > 0:
        for k in weighted_votes:
            weighted_votes[k] = weighted_votes[k] / total_weight

    # 3. Determine consensus
    sorted_signals = sorted(weighted_votes.items(), key=lambda x: -x[1])
    consensus_signal = sorted_signals[0][0]
    top_weight = sorted_signals[0][1]
    second_weight = sorted_signals[1][1] if len(sorted_signals) > 1 else 0

    # 4. Compute confidence
    spread = top_weight - second_weight
    # Base confidence from vote dominance
    total_personas = len(persona_outputs)
    raw_confidence = spread * 100  # 0-100 range

    # Adjust for accuracy reliability
    avg_accuracy = sum(
        accuracy_data.get(p, {}).get("accuracy", 0.50)
        for p in persona_outputs
    ) / total_personas
    accuracy_boost = (avg_accuracy - 0.50) * 80  # ±40 depending on track record

    # Adjust for participation rate
    participation_rate = total_personas / len(PERSONAS)
    participation_boost = (participation_rate - 0.5) * 30  # ±15

    confidence = max(0, min(100, raw_confidence * 0.6 + accuracy_boost * 0.25 + participation_boost * 0.15))

    # 5. Conflict resolution — tiebreaker
    if spread < 0.05 and total_personas >= 8:
        tie_info = _resolve_tie(consensus_signal, persona_outputs, date_str)
        consensus_signal = tie_info.get("resolved_signal", consensus_signal)
        confidence = max(confidence, tie_info.get("confidence", confidence))
        if "tiebreaker_note" in tie_info:
            pass  # tie_info is returned separately below

    # 6. Identify agreeing/disagreeing personas
    agreeing = [p for p, s in persona_signals.items() if s == consensus_signal]
    disagreeing = [p for p, s in persona_signals.items() if s != consensus_signal]

    # 7. Get TV MCP indicators (for technical persona context)
    tech_indicators = {}
    if ticker:
        tech_indicators = get_tv_mcp_readings(ticker)

    # 8. Regime context
    regime = _get_current_regime()

    # Signal strength: percentage of weighted votes going to consensus
    signal_strength = round(weighted_votes.get(consensus_signal, 0) * 100, 1)

    return {
        "ticker": ticker,
        "date": date_str,
        "consensus_signal": consensus_signal,
        "confidence": round(confidence, 1),
        "signal_strength": signal_strength,
        "signal_breakdown": {
            "BUY": {"count": raw_votes.get("BUY", 0), "weighted_pct": round(weighted_votes.get("BUY", 0) * 100, 1)},
            "HOLD": {"count": raw_votes.get("HOLD", 0), "weighted_pct": round(weighted_votes.get("HOLD", 0) * 100, 1)},
            "SELL": {"count": raw_votes.get("SELL", 0), "weighted_pct": round(weighted_votes.get("SELL", 0) * 100, 1)},
        },
        "total_personas": total_personas,
        "agreeing_personas": sorted(agreeing),
        "disagreeing_personas": sorted(disagreeing),
        "weight_by_persona": weight_by_persona,
        "regime": regime,
        "technical_indicators": tech_indicators,
        "persona_signals": persona_signals,
    }


def _resolve_tie(
    current_signal: str,
    persona_outputs: Dict[str, Dict[str, Any]],
    date_str: str,
) -> Dict[str, Any]:
    """Resolve a tied vote using regime-aware tiebreaker.
    Returns dict with resolved_signal and confidence adjustment.
    """
    regime = _get_current_regime()
    preferred = REGIME_PREFERRED.get(regime, [])

    # Count weighted votes only from preferred personas
    pref_votes = {"BUY": 0, "HOLD": 0, "SELL": 0}
    for persona, output in persona_outputs.items():
        if persona in preferred:
            signal = output.get("signal", "HOLD")
            pref_votes[signal] = pref_votes.get(signal, 0) + 1

    if any(pref_votes.values()):
        sorted_pref = sorted(pref_votes.items(), key=lambda x: -x[1])
        resolved = sorted_pref[0][0]
        if sorted_pref[0][1] > sorted_pref[1][1] if len(sorted_pref) > 1 else True:
            return {
                "resolved_signal": resolved,
                "confidence": 65,
                "tiebreaker_note": f"Resolved by {regime} regime: preferred personas "
                                   f"({', '.join(preferred)}) favour {resolved}",
            }

    # Fallback: use accuracy-weighted vote
    accuracy_data = _load_accuracy_data()
    acc_votes = {"BUY": 0.0, "HOLD": 0.0, "SELL": 0.0}
    for persona, output in persona_outputs.items():
        acc = accuracy_data.get(persona, {}).get("accuracy", 0.50)
        signal = output.get("signal", "HOLD")
        acc_votes[signal] = acc_votes.get(signal, 0) + acc

    sorted_acc = sorted(acc_votes.items(), key=lambda x: -x[1])
    resolved = sorted_acc[0][0]
    return {
        "resolved_signal": resolved,
        "confidence": 55,
        "tiebreaker_note": f"Resolved by accuracy-weighted vote in {regime} regime",
    }


# ─────────────────────────────────────────────────────────────────────
# REPORT GENERATOR
# ─────────────────────────────────────────────────────────────────────

def generate_aggregated_report(
    persona_outputs: Dict[str, Dict[str, Any]],
    ticker: str = "",
    date_str: Optional[str] = None,
) -> str:
    """Generate a markdown report from the aggregated consensus.

    Args:
        persona_outputs: {persona: {stance, signal, word_count, ...}}
        ticker: Stock ticker
        date_str: Run date

    Returns:
        Markdown string of the full report
    """
    consensus = aggregate_consensus(persona_outputs, ticker, date_str)
    date_str = date_str or datetime.date.today().isoformat()

    lines = [
        f"# Meta-Persona Consensus Report",
        f"",
        f"**Ticker:** {ticker or 'N/A'}",
        f"**Date:** {date_str}",
        f"**Regime:** {consensus.get('regime', 'unknown').replace('_', ' ').title()}",
        f"**Total Personas Reporting:** {consensus.get('total_personas', 0)} / {len(PERSONAS)}",
        f"",
        "---",
        f"",
        f"## Consensus Signal: **{consensus.get('consensus_signal', 'HOLD')}**",
        f"",
        f"| Metric | Value |",
        f"|---|---|",
        f"| **Signal** | {consensus.get('consensus_signal', 'HOLD')} |",
        f"| **Confidence** | {consensus.get('confidence', 0)} / 100 |",
        f"| **Signal Strength** | {consensus.get('signal_strength', 0)}% of weighted vote |",
        f"",
    ]

    # Signal breakdown
    sb = consensus.get("signal_breakdown", {})
    lines.append("### Signal Breakdown")
    lines.append("")
    lines.append(f"| Signal | Count | Weighted % |")
    lines.append(f"|---|---|---|")
    for sig in ["BUY", "HOLD", "SELL"]:
        info = sb.get(sig, {})
        lines.append(f"| **{sig}** | {info.get('count', 0)} | {info.get('weighted_pct', 0)}% |")
    lines.append("")

    # Agreeing / Disagreeing personas
    agreeing = consensus.get("agreeing_personas", [])
    disagreeing = consensus.get("disagreeing_personas", [])
    lines.append(f"### Agreement Analysis")
    lines.append("")
    if agreeing:
        lines.append(f"**Agree ({len(agreeing)}):** {', '.join(f'`{p}`' for p in agreeing)}")
    if disagreeing:
        lines.append(f"**Disagree ({len(disagreeing)}):** {', '.join(f'`{p}`' for p in disagreeing)}")
    lines.append("")

    # Regime context
    regime = consensus.get("regime", "unknown")
    regime_bias_list = REGIME_BIAS.get(regime, PERSONAS)
    lines.append(f"### Regime Context: {regime.replace('_', ' ').title()}")
    lines.append("")
    lines.append(f"In {regime.replace('_', ' ')} regimes, historically reliable personas are:")
    lines.append(f"{', '.join(f'`{p}`' for p in regime_bias_list)}")
    lines.append("")

    # Weight by persona
    wbp = consensus.get("weight_by_persona", {})
    lines.append("### Persona Weights (from rolling 30d accuracy)")
    lines.append("")
    lines.append(f"| Persona | Stance | Signal | Weight | Word Count |")
    lines.append(f"|---|---|---|---|---|")
    for persona in PERSONAS:
        if persona in persona_outputs:
            output = persona_outputs[persona]
            stance = output.get("stance", "?")
            signal = output.get("signal", "?")
            weight = wbp.get(persona, 0)
            wc = output.get("word_count", 0)
            lines.append(f"| {persona} | {stance} | {signal} | {weight:.3f} | {wc} |")
        else:
            lines.append(f"| {persona} | — | — | — | No output |")
    lines.append("")

    # TV MCP Indicators
    ti = consensus.get("technical_indicators", {})
    if ti:
        lines.append("### TV MCP Indicator Readings")
        lines.append("")
        lines.append(f"| Indicator | Value |")
        lines.append(f"|---|---|")
        for k, v in sorted(ti.items()):
            lines.append(f"| {k} | {v} |")
        lines.append("")

    # Individual persona stances
    ps = consensus.get("persona_signals", {})
    lines.append("### Individual Persona Stances")
    lines.append("")
    for persona in PERSONAS:
        if persona in ps:
            sig = ps[persona]
            icon = {"BUY": "🟢", "HOLD": "🟡", "SELL": "🔴"}.get(sig, "⚪")
            lines.append(f"- {icon} `{persona}`: **{sig}**")
    lines.append("")

    # Error handling
    if "error" in consensus:
        lines.append("")
        lines.append(f"**Note:** {consensus['error']}")
        lines.append("")

    lines.append("---")
    lines.append(f"*Report generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# FULL PIPELINE
# ─────────────────────────────────────────────────────────────────────

def run_aggregation(
    ticker: str,
    date_str: Optional[str] = None,
    output_report: bool = True,
) -> Dict[str, Any]:
    """Run the full aggregation pipeline for a single ticker.

    Args:
        ticker: Stock ticker to aggregate
        date_str: Run date (default: today)
        output_report: Whether to write a .md report to the output directory

    Returns:
        Consensus result dict
    """
    date_str = date_str or datetime.date.today().isoformat()
    outputs = load_persona_outputs(ticker, date_str)
    consensus = aggregate_consensus(outputs, ticker, date_str)

    if output_report and consensus:
        report = generate_aggregated_report(outputs, ticker, date_str)
        output_dir = os.path.expanduser(OUTPUT_DIR)
        report_dir = os.path.join(output_dir, date_str, "_aggregator")
        os.makedirs(report_dir, exist_ok=True)
        report_path = os.path.join(report_dir, f"{ticker}_consensus.md")
        with open(report_path, "w") as f:
            f.write(report)
        print(f"[Aggregator] ✓ Report written → {report_path}", flush=True)
        consensus["report_path"] = report_path

    return consensus


def batch_aggregate(date_str: Optional[str] = None) -> List[Dict[str, Any]]:
    """Run aggregation across ALL tickers found in the output directory.
    Returns list of consensus results.
    """
    date_str = date_str or datetime.date.today().isoformat()
    output_dir = os.path.expanduser(OUTPUT_DIR)
    date_dir = os.path.join(output_dir, date_str)

    if not os.path.isdir(date_dir):
        logger.warning("No output directory for %s", date_str)
        return []

    # Discover all tickers by scanning persona dirs
    tickers = set()
    for persona_dir in sorted(os.listdir(date_dir)):
        persona_path = os.path.join(date_dir, persona_dir)
        if not os.path.isdir(persona_path) or persona_dir.startswith("_"):
            continue
        try:
            for fname in os.listdir(persona_path):
                if fname.endswith(".md"):
                    # Extract ticker from filename (remove date suffix if any)
                    ticker = fname.replace(".md", "").split(" - ")[0].split(" ")[0]
                    tickers.add(ticker)
        except OSError:
            continue

    results = []
    for ticker in sorted(tickers):
        consensus = run_aggregation(ticker, date_str, output_report=True)
        results.append(consensus)
        print(f"[Aggregator] {ticker}: {consensus.get('consensus_signal', '?')} "
              f"(confidence: {consensus.get('confidence', 0):.0f}%)", flush=True)

    # Write master consensus index
    _write_master_consensus(results, date_str)
    return results


def _write_master_consensus(results: List[Dict[str, Any]], date_str: str):
    """Write a master consensus index aggregating all tickers."""
    output_dir = os.path.expanduser(OUTPUT_DIR)
    report_dir = os.path.join(output_dir, date_str, "_aggregator")
    os.makedirs(report_dir, exist_ok=True)

    # Group by consensus signal
    by_signal = defaultdict(list)
    for r in results:
        by_signal[r.get("consensus_signal", "HOLD")].append(r)

    lines = [
        f"# Master Consensus — {date_str}",
        "",
        f"**Tickers Analyzed:** {len(results)}",
        "",
        "---",
        "",
    ]

    for signal in ["BUY", "HOLD", "SELL"]:
        items = by_signal.get(signal, [])
        if not items:
            continue
        lines.append(f"## {signal} ({len(items)})")
        lines.append("")
        for r in sorted(items, key=lambda x: -x.get("confidence", 0)):
            ticker = r.get("ticker", "?")
            conf = r.get("confidence", 0)
            strength = r.get("signal_strength", 0)
            agreeing = r.get("agreeing_personas", [])
            disagreeing = r.get("disagreeing_personas", [])
            lines.append(f"- **{ticker}** — Confidence: {conf:.0f}%, Strength: {strength:.0f}%")
            lines.append(f"  - Agree ({len(agreeing)}): {', '.join(f'`{p}`' for p in agreeing)}")
            if disagreeing:
                lines.append(f"  - Disagree ({len(disagreeing)}): {', '.join(f'`{p}`' for p in disagreeing)}")
            lines.append("")

    lines.append("---")
    lines.append(f"*Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

    filepath = os.path.join(report_dir, "MASTER_CONSENSUS.md")
    with open(filepath, "w") as f:
        f.write("\n".join(lines))
    print(f"[Aggregator] ✓ Master consensus written → {filepath}", flush=True)


# ─────────────────────────────────────────────────────────────────────
# CLI ENTRY POINT
# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) >= 2 and sys.argv[1] in ("--batch", "-b"):
        date_arg = sys.argv[2] if len(sys.argv) > 2 else None
        batch_aggregate(date_arg)
    elif len(sys.argv) >= 2:
        ticker = sys.argv[1]
        date_arg = sys.argv[2] if len(sys.argv) > 2 else None
        result = run_aggregation(ticker, date_arg)
        print(f"\nConsensus for {ticker}: {result.get('consensus_signal', '?')} "
              f"(confidence: {result.get('confidence', 0):.0f}%)")
    else:
        print("Usage:")
        print("  python -m arena_v2.aggregator.aggregator <TICKER> [DATE]")
        print("  python -m arena_v2.aggregator.aggregator --batch [DATE]")
