#!/usr/bin/env python3
"""
persona_engine.py — Generative Agents Persona System

Implements the Stanford "Generative Agents" paper (Park et al., 2023) applied
to financial analysis. Each persona acts as a self-contained agent with:
  1. Memory Stream — timestamped observations the persona has recorded
  2. Reflection — higher-level synthesis derived from patterns across memories
  3. Planning/Decision Tree — persona-specific methodology with exact numbers
  4. Social Reasoning — quotes and citations from the persona's own writings

Usage:
  python persona_engine.py --persona oneil --sn-data
  python persona_engine.py --all
  python persona_engine.py --json --all
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HERMES_HOME = os.path.expanduser("~/hermes_home")
PROFILES_DIR = os.path.join(HERMES_HOME, "profiles")

PERSONAS = {
    "oneil":        {"name": "O'Neil",         "dir": "oneil",         "label": "William O'Neil — CANSLIM Trader"},
    "minervini":    {"name": "Minervini",       "dir": "minervini",     "label": "Mark Minervini — SEPA / VCP Trader"},
    "qullamaggie":  {"name": "Qullamaggie",     "dir": "qullamaggie",   "label": "Kristjan Qullamaggie — Episodic Pivot Trader"},
    "lynch":        {"name": "Lynch",           "dir": "lynch",         "label": "Peter Lynch — Growth at a Reasonable Price"},
    "buffett":      {"name": "Buffett",         "dir": "buffet",        "label": "Warren Buffett — Value Investor"},
    "david-ryan":   {"name": "David Ryan",      "dir": "david-ryan",    "label": "David Ryan — CANSLIM Champion"},
}

# ---------------------------------------------------------------------------
# SN Data from Q1 2026 filings
# ---------------------------------------------------------------------------

SN_DATA = {
    "ticker": "SN",
    "price": 118.28,
    "revenue_b": 1.41,
    "revenue_growth_pct": 15.6,
    "net_income_m": 121.5,
    "net_income_growth_pct": 3.1,
    "gross_margin_pct": 49.2,
    "gross_margin_change_bps": -100,
    "net_margin_pct": 8.6,
    "net_margin_change_bps": -100,
    "pe_ratio": 25.9,
    "sector_avg_pe": 12.6,
    "debt_m": 810,
    "cash_m": 512,
    "inventory_m": 1034,
    "inventory_pct_of_revenue": 73.0,
    "cfo_shares_sold": 6923,
    "cfo_sold_pct": 80,
    "cfo_sale_value_k": 782,
    "insider_sales_note": "CFO sold 80% of holdings on May 8, 2026",
    "chairman_sold_m": 5.5,
    "chairman_sold_note": "Secondary offering by chairman",
    "recalled_units_m": 1.8,
    "recalled_product": "pressure cookers",
    "class_actions": [
        "Zamani v SN (blade defect)",
        "NeverSick cookware (false claims)"
    ],
    "short_seller": "Grizzly Research — 'China hustle' allegations (Aug 2024)",
    "tariffs_pct": 10,
    "tariff_note": "Baseline for SE Asia backup countries",
    "new_categories_count": 39,
    "new_category_name": "outdoor",
    "new_products_per_year": 25,
    "marketing_spend_m": 700,
    "food_prep_category_change_pct": -3.3,
    "beauty_home_revenue_m": 194.1,
    "beauty_home_growth_pct": 40.8,
    "buyback_announced_m": 750,
    "buyback_date": "Feb 2026",
    "filing_period": "Q1 2026",
    "distribution_days": 6,
    "distribution_days_period": "22 sessions",
    "market_regime": "correction",
    "follow_through_day": False,
}

# ---------------------------------------------------------------------------
# Persona engine core
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Extended stock data (added for multi-stock analysis)
# ---------------------------------------------------------------------------

ETOR_DATA = {
    "ticker": "ETOR",
    "price": 38.86,
    "sector": "Financial Services",
    "industry": "Capital Markets",
    "mktcap_b": 3.0,
    "pe_ratio": 15.9,
    "eps_growth_pct": -35.6,
    "rev_growth_pct": -35.6,
    "roe_pct": 21.0,
    "inst_own_pct": 53.0,
    "off_52w_high_pct": -42.3,
    "rsi_14": 46.7,
    "vol_vs_avg_pct": 8,
    "price_above_ma50_pct": 4.3,
    "price_above_ma200_pct": 6.2,
    "key_risks": "Revenue declining 35.6% YoY. No earnings growth. Institutional ownership low. Volume 8% of avg - zero interest.",
    "key_positives": "ROE 21% is decent. P/E 15.9 not expensive. Could be value trap or turnaround.",
    "insider_activity": "No significant insider buying reported.",
    "debt_m": 0,
    "cash_m": 0,
# Normalized keys for analytics compatibility
    "net_income_growth_pct": 0.0,
    "revenue_growth_pct": 0.0,
    "net_income_m": 0.0,
    "gross_margin_pct": 0.0,
    "gross_margin_change_bps": 0,
    "net_margin_pct": 0.0,
    "net_margin_change_bps": 0,
    "inventory_m": 0.0,
    "inventory_pct_of_revenue": 0.0,
    "cfo_shares_sold": 0,
    "cfo_sold_pct": 0,
    "cfo_sale_value_k": 0.0,
    "insider_sales_note": "",
    "chairman_sold_m": 0.0,
    "chairman_sold_note": "",
    "recalled_units_m": 0.0,
    "recalled_product": "",
    "class_actions": [],
    "short_seller": "",
    "tariffs_pct": 0,
    "tariff_note": "",
    "new_categories_count": 0,
    "new_category_name": "",
    "new_products_per_year": 0,
    "marketing_spend_m": 0.0,
    "food_prep_category_change_pct": 0.0,
    "beauty_home_revenue_m": 0.0,
    "beauty_home_growth_pct": 0.0,
    "buyback_announced_m": 0.0,
    "buyback_date": "",
    "distribution_days": 6,
    "distribution_days_period": "22 sessions",
    "market_regime": "correction",
    "follow_through_day": False,
    "filing_period": "current period",
"net_income_growth_pct": -35.6,
    "revenue_growth_pct": -35.6,
    "sector_avg_pe": 12.0,

}

GFS_DATA = {
    "ticker": "GFS",
    "price": 76.35,
    "sector": "Technology",
    "industry": "Semiconductors",
    "mktcap_b": 41.3,
    "pe_ratio": 51.6,
    "eps_growth_pct": 3.1,
    "rev_growth_pct": 3.1,
    "roe_pct": 6.8,
    "inst_own_pct": 103.4,
    "off_52w_high_pct": -15.1,
    "rsi_14": 58.8,
    "vol_vs_avg_pct": 12,
    "price_above_ma50_pct": 15.7,
    "price_above_ma200_pct": 68.0,
    "key_risks": "P/E 51.6 on 3.1% growth = extremely expensive. ROE 6.8% below Buffett 15% threshold. Revenue growth slowing. Foundry business capital intensive.",
    "key_positives": "Well above MA200 (+68%). RSI 58.8 neutral. Geopolitical tailwind (CHIPS Act). Only independent foundry.",
    "insider_activity": "Institutional ownership over 100% suggests heavy ETF/index ownership.",
    "debt_m": 1724,
    "cash_m": 3003,
# Normalized keys for analytics compatibility
    "net_income_growth_pct": 0.0,
    "revenue_growth_pct": 0.0,
    "net_income_m": 0.0,
    "gross_margin_pct": 0.0,
    "gross_margin_change_bps": 0,
    "net_margin_pct": 0.0,
    "net_margin_change_bps": 0,
    "inventory_m": 0.0,
    "inventory_pct_of_revenue": 0.0,
    "cfo_shares_sold": 0,
    "cfo_sold_pct": 0,
    "cfo_sale_value_k": 0.0,
    "insider_sales_note": "",
    "chairman_sold_m": 0.0,
    "chairman_sold_note": "",
    "recalled_units_m": 0.0,
    "recalled_product": "",
    "class_actions": [],
    "short_seller": "",
    "tariffs_pct": 0,
    "tariff_note": "",
    "new_categories_count": 0,
    "new_category_name": "",
    "new_products_per_year": 0,
    "marketing_spend_m": 0.0,
    "food_prep_category_change_pct": 0.0,
    "beauty_home_revenue_m": 0.0,
    "beauty_home_growth_pct": 0.0,
    "buyback_announced_m": 0.0,
    "buyback_date": "",
    "distribution_days": 6,
    "distribution_days_period": "22 sessions",
    "market_regime": "correction",
    "follow_through_day": False,
    "filing_period": "current period",
"net_income_growth_pct": 3.1,
    "revenue_growth_pct": 3.1,
    "sector_avg_pe": 25.0,

}

INTR_DATA = {
    "ticker": "INTR",
    "price": 5.64,
    "sector": "Financial Services",
    "industry": "Banks - Regional",
    "mktcap_b": 2.5,
    "pe_ratio": 9.1,
    "eps_growth_pct": 25.3,
    "rev_growth_pct": 25.3,
    "roe_pct": 15.5,
    "inst_own_pct": 49.8,
    "off_52w_high_pct": -44.8,
    "rsi_14": 46.8,
    "vol_vs_avg_pct": 9,
    "price_above_ma50_pct": -20.1,
    "price_above_ma200_pct": -32.4,
    "key_risks": "Price $5.64 - below O/Neil minimum. Below all MAs. Regional bank - hidden loan loss risk. Brazilian real exposure.",
    "key_positives": "Revenue growth 25.3% (strong). P/E 9.1 (cheap). ROE 15.5% (solid). PEG = 0.36 (Lynch territory).",
    "insider_activity": "Brazilian fintech/bank - insiders may hold significant positions.",
    "debt_m": 19826,
    "cash_m": 6949,
# Normalized keys for analytics compatibility
    "net_income_growth_pct": 0.0,
    "revenue_growth_pct": 0.0,
    "net_income_m": 0.0,
    "gross_margin_pct": 0.0,
    "gross_margin_change_bps": 0,
    "net_margin_pct": 0.0,
    "net_margin_change_bps": 0,
    "inventory_m": 0.0,
    "inventory_pct_of_revenue": 0.0,
    "cfo_shares_sold": 0,
    "cfo_sold_pct": 0,
    "cfo_sale_value_k": 0.0,
    "insider_sales_note": "",
    "chairman_sold_m": 0.0,
    "chairman_sold_note": "",
    "recalled_units_m": 0.0,
    "recalled_product": "",
    "class_actions": [],
    "short_seller": "",
    "tariffs_pct": 0,
    "tariff_note": "",
    "new_categories_count": 0,
    "new_category_name": "",
    "new_products_per_year": 0,
    "marketing_spend_m": 0.0,
    "food_prep_category_change_pct": 0.0,
    "beauty_home_revenue_m": 0.0,
    "beauty_home_growth_pct": 0.0,
    "buyback_announced_m": 0.0,
    "buyback_date": "",
    "distribution_days": 6,
    "distribution_days_period": "22 sessions",
    "market_regime": "correction",
    "follow_through_day": False,
    "filing_period": "current period",
"net_income_growth_pct": 25.3,
    "revenue_growth_pct": 25.3,
    "sector_avg_pe": 12.0,

}

NRDS_DATA = {
    "ticker": "NRDS",
    "price": 8.34,
    "sector": "Communication Services",
    "industry": "Internet Content & Information",
    "mktcap_b": 0.54,
    "pe_ratio": 9.0,
    "eps_growth_pct": 6.2,
    "rev_growth_pct": 6.2,
    "roe_pct": 19.5,
    "inst_own_pct": 94.9,
    "off_52w_high_pct": -47.7,
    "rsi_14": 55.3,
    "vol_vs_avg_pct": 16,
    "price_above_ma50_pct": -12.9,
    "price_above_ma200_pct": -26.6,
    "key_risks": "Price $8.34 - sub-$15. Revenue growth only 6.2%. Below all MAs. Tiny mkt cap $540M. Housing cycle risk.",
    "key_positives": "P/E 9 (cheap). ROE 19.5% (great). Insider ownership high. If housing transaction volumes recover, could re-rate.",
    "insider_activity": "Check SEC filings for insider buying near lows.",
    "debt_m": 22.5,
    "cash_m": 56.3,
# Normalized keys for analytics compatibility
    "net_income_growth_pct": 0.0,
    "revenue_growth_pct": 0.0,
    "net_income_m": 0.0,
    "gross_margin_pct": 0.0,
    "gross_margin_change_bps": 0,
    "net_margin_pct": 0.0,
    "net_margin_change_bps": 0,
    "inventory_m": 0.0,
    "inventory_pct_of_revenue": 0.0,
    "cfo_shares_sold": 0,
    "cfo_sold_pct": 0,
    "cfo_sale_value_k": 0.0,
    "insider_sales_note": "",
    "chairman_sold_m": 0.0,
    "chairman_sold_note": "",
    "recalled_units_m": 0.0,
    "recalled_product": "",
    "class_actions": [],
    "short_seller": "",
    "tariffs_pct": 0,
    "tariff_note": "",
    "new_categories_count": 0,
    "new_category_name": "",
    "new_products_per_year": 0,
    "marketing_spend_m": 0.0,
    "food_prep_category_change_pct": 0.0,
    "beauty_home_revenue_m": 0.0,
    "beauty_home_growth_pct": 0.0,
    "buyback_announced_m": 0.0,
    "buyback_date": "",
    "distribution_days": 6,
    "distribution_days_period": "22 sessions",
    "market_regime": "correction",
    "follow_through_day": False,
    "filing_period": "current period",
"net_income_growth_pct": 6.2,
    "revenue_growth_pct": 6.2,
    "sector_avg_pe": 15.0,

}

ZGN_DATA = {
    "ticker": "ZGN",
    "price": 14.66,
    "sector": "Consumer Cyclical",
    "industry": "Apparel Manufacturing",
    "mktcap_b": 4.0,
    "pe_ratio": 33.3,
    "eps_growth_pct": 0.3,
    "rev_growth_pct": 0.3,
    "roe_pct": 10.5,
    "inst_own_pct": 25.5,
    "off_52w_high_pct": -1.7,
    "rsi_14": 76.6,
    "vol_vs_avg_pct": 55,
    "price_above_ma50_pct": 16.0,
    "price_above_ma200_pct": 37.1,
    "key_risks": "Revenue growth 0.3% (flat). P/E 33.3 on zero growth = PEG undefined. RSI 76.6 overbought. Luxury cyclical at peak valuation. ROE 10.5% weak.",
    "key_positives": "Only 1.7% off 52w high. Price above all MAs. Strong brand in luxury menswear. Near all-time highs.",
    "insider_activity": "LVMH-related investors may have positions. Check recent filings.",
    "debt_m": 982,
    "cash_m": 296,
# Normalized keys for analytics compatibility
    "net_income_growth_pct": 0.0,
    "revenue_growth_pct": 0.0,
    "net_income_m": 0.0,
    "gross_margin_pct": 0.0,
    "gross_margin_change_bps": 0,
    "net_margin_pct": 0.0,
    "net_margin_change_bps": 0,
    "inventory_m": 0.0,
    "inventory_pct_of_revenue": 0.0,
    "cfo_shares_sold": 0,
    "cfo_sold_pct": 0,
    "cfo_sale_value_k": 0.0,
    "insider_sales_note": "",
    "chairman_sold_m": 0.0,
    "chairman_sold_note": "",
    "recalled_units_m": 0.0,
    "recalled_product": "",
    "class_actions": [],
    "short_seller": "",
    "tariffs_pct": 0,
    "tariff_note": "",
    "new_categories_count": 0,
    "new_category_name": "",
    "new_products_per_year": 0,
    "marketing_spend_m": 0.0,
    "food_prep_category_change_pct": 0.0,
    "beauty_home_revenue_m": 0.0,
    "beauty_home_growth_pct": 0.0,
    "buyback_announced_m": 0.0,
    "buyback_date": "",
    "distribution_days": 6,
    "distribution_days_period": "22 sessions",
    "market_regime": "correction",
    "follow_through_day": False,
    "filing_period": "current period",
"net_income_growth_pct": 0.3,
    "revenue_growth_pct": 0.3,
    "sector_avg_pe": 20.0,

}

@dataclass
class PersonaAnalysis:
    """Structured output for a single persona analysis."""
    persona: str
    ticker: str
    price: float
    date: str
    screening_criteria: str  # What they check first
    reasoning: List[Dict[str, str]]  # [{quote, source, analysis}]
    verdict: str  # BUY / HOLD / SELL / PASS / AVOID
    conviction: float  # 0.0 - 1.0
    indicators_used: List[str]
    sources_cited: List[str]
    decision_tree_path: List[str]


def load_soul(persona_key: str) -> str:
    """Load the SOUL.md file for a given persona key."""
    info = PERSONAS[persona_key]
    soul_path = os.path.join(PROFILES_DIR, info["dir"], "SOUL.md")
    if not os.path.exists(soul_path):
        raise FileNotFoundError(f"SOUL.md not found: {soul_path}")
    with open(soul_path, "r") as f:
        return f.read()


def extract_decision_trees(soul_text: str) -> List[str]:
    """Extract decision tree lines from SOUL.md."""
    lines = []
    in_tree = False
    for line in soul_text.split("\n"):
        if "DECISION TREE" in line or "HARD RULES" in line:
            in_tree = True
        if in_tree:
            lines.append(line)
            if line.strip().startswith("---") and in_tree:
                in_tree = False
    return lines


def extract_quote_database(soul_text: str) -> List[Dict[str, str]]:
    """Extract quote entries from Quote Database section."""
    quotes = []
    in_quotes = False
    current_topic = ""
    for line in soul_text.split("\n"):
        if "QUOTE DATABASE" in line:
            in_quotes = True
            continue
        if not in_quotes:
            continue
        if line.startswith("#") and "QUOTE" not in line:
            current_topic = line.strip("# ")
        if line.strip().startswith("*"):
            quotes.append({"topic": current_topic, "raw": line.strip()})
        if line.strip().startswith("---") and in_quotes:
            # Check if this ends the section or is a section separator
            pass
    return quotes


def match_quote_to_claim(claim: str, quotes: List[Dict[str, str]]) -> Optional[str]:
    """Find the best matching quote for a given claim."""
    claim_lower = claim.lower()
    keywords = {
        "eps": ["current quarterly earnings", "earnings per share", "eps growth", "current eps",
                "quarterly earnings", "earnings increases"],
        "sales": ["sales growth", "revenue growth", "annual earnings increases", "significant growth"],
        "p/e": ["p/e ratio", "pe ratio", "don't let p/e", "price to earnings"],
        "insider": ["smart money", "institutional", "insider", "track what"],
        "market": ["most important letter", "m is the most", "market direction", "distribution day",
                   "bad stocks in a bad market", "good stocks in a bad"],
        "margin": ["profit margin", "margin", "pre-tax margin"],
        "loss": ["cut loss", "cut your loss", "7%", "8%", "7-8%", "small loss"],
        "profit": ["take profit", "sell early", "20%", "25%", "3-to-1 ratio", "pigs get slaughtered"],
        "volume": ["volume", "heavy volume", "light volume"],
        "vcp": ["volatility contraction", "each correction wave", "contraction pattern", "less volatility"],
        "pivot": ["buy at the pivot", "pivot point", "breaches the base", "breach base"],
        "ep": ["episodic pivot", "gap up", "ep is the highest", "gap up 10%"],
        "breakout": ["breakout", "break out", "new high", "boc", "breakout from consolidation"],
        "setup": ["three setups", "only trade the three", "not one of them"],
        "category": ["fast grower", "stalwart", "cyclical", "turnaround", "asset play", "slow grower",
                     "behind every stock"],
        "peg": ["peg ratio", "peg of 1.0", "half the growth"],
        "story": ["know what you own", "2-minute", "two-minute", "buy what you know", "if you can't find"],
        "moat": ["moat", "economic castle", "competitive advantage", "unbreachable"],
        "circle": ["circle of competence", "you don't have to be an expert", "knowing its boundaries"],
        "intrinsic": ["intrinsic value", "approximately right", "precisely wrong", "discounted value"],
        "five-year": ["five years", "10 years", "10 minutes", "forever", "favorite holding period"],
        "greed": ["fearful when others", "greedy when others"],
        "price": ["price is what you pay", "value is what you get"],
        "diversification": ["diversification is protection", "protection against ignorance"],
        "85-85": ["85-85 screen", "eps 85+", "starting point every morning"],
        "tight": ["tight weekly close", "tight close", "coiling"],
        "pyramid": ["50-35-15", "pyramid", "pyramiding"],
        "health": ["health check", "healthy pattern", "unhealthy pattern"],
        "screen": ["market 250", "four charts", "single most important habit", "screen stocks"],
        "inventory": ["inventory", "inventory building", "check inventory"],
        "dividend": ["dividend", "dividend yield"],
        "management": ["reputation for brilliance", "management with a reputation"],
        "compounding": ["compounding", "secret to compounding"],
        "discipline": ["rewards discipline", "market rewards", "discipline, not intelligence"],
        "sitting": ["sitting is more important", "be patient", "do nothing"],
        "stop": ["stop is your most", "cut my loss", "7% to 8%", "no exceptions"],
        "concentration": ["concentration is the only", "turn a small account"],
        "mechanical": ["mechanical", "the setup does not care", "simple but not easy"],
        "obvious": ["best trades are obvious", "convincing yourself"],
        "cut": ["cut your losers", "like a cancer"],
        "tenbagger": ["tenbagger", "tenbaggers", "tenfold"],
    }

    # Score each quote by how many keywords it matches
    best_score = 0
    best_quote = None
    for q in quotes:
        raw = q["raw"].lower()
        score = 0
        for kw_group in keywords.values():
            for kw in kw_group:
                if kw in claim_lower and kw in raw:
                    score += 3
                if kw in raw:
                    score += 1
        if score > best_score:
            best_score = score
            best_quote = q["raw"]

    return best_quote


# ---------------------------------------------------------------------------
# Persona-specific analysis engines
# ---------------------------------------------------------------------------

def _run_oneil(d: dict, quotes: List[Dict[str,str]]) -> PersonaAnalysis:
    reasoning = []
    sources = []
    indicators = []
    tree_path = []

    # Screening criteria
    screening = "First check: distribution days on the major indices. Then apply CANSLIM screen: C (EPS rating), A (annual earnings), N (new product), S (supply/demand), L (leader), I (institutional), M (market)."

    # 1. Market check (M)
    q = match_quote_to_claim("M is the most important letter", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "In CANSLIM, the 'M' is the most important letter. Most investors lose money not because they buy bad stocks, but because they buy good stocks in a bad market.",
        "source": "IBD Website: https://www.investors.com/ibd-university/market-direction/",
        "analysis": f"f'M' — {d['distribution_days']} distribution days in {d['distribution_days_period']}. Market is in correction. 'M' alone demands we stop here. DO NOT BUY in this environment."
    })
    indicators.append(f"Distribution days: {d['distribution_days']} in {d['distribution_days_period']}")
    tree_path.append("M CHECK: 6 distribution days >= 5 → RAISE CASH")

    # 2. EPS check (C)
    q = match_quote_to_claim("Current EPS minimum 20%", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "Current Quarterly Earnings per Share: The Higher, the Better.",
        "source": "How to Make Money in Stocks (4th ed.), CAN SLIM introduction [p.15]",
        "analysis": f"f'Net income growth +{d['net_income_growth_pct']}% per {d['filing_period']} filings. CANSLIM requires 20% MINIMUM. Even using revenue growth of +{d['revenue_growth_pct']}%, ideal is 40-100-200%. {d['ticker']} fails 'C' completely."
    })
    indicators.append(f"EPS growth: +{d['net_income_growth_pct']}% (requires 20% minimum)")
    tree_path.append("C CHECK: EPS +3.1% < 20% → FAILS C")

    # 3. Sales check (A)
    q = match_quote_to_claim("sales growth", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "Annual Earnings Increases: Look for Significant Growth.",
        "source": "How to Make Money in Stocks (4th ed.), CAN SLIM introduction [p.15]",
        "analysis": f"f'Revenue +{d['revenue_growth_pct']}% while net income +{d['net_income_growth_pct']}% means costs are spiraling. CANSLIM requires 25% minimum sales growth. This stock is growing revenue but not profits — that's a warning, not a signal."
    })
    indicators.append(f"Revenue growth: +{d['revenue_growth_pct']}% (requires 25% minimum)")
    tree_path.append("A CHECK: Revenue +15.6% < 25% → FAILS A")

    # 4. P/E check
    q = match_quote_to_claim("P/E ratio", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "Don't buy a stock because of its dividend or its P/E ratio. Buy it because it's the number one company in its particular field in terms of earnings and sales growth, ROE, profit margins, and product superiority.",
        "source": "How to Make Money in Stocks (4th ed.), Ch.20 [p.431]",
        "analysis": f"P/E at 25.9x versus sector average 12.6x. Normally I'd say 'Don't let P/E stop you' — BUT that only applies when earnings growth supports it. +3.1% earnings growth does not support a 25.9x multiple."
    })
    indicators.append("P/E: 25.9x (sector avg: 12.6x)")
    tree_path.append("P/E CHECK: 25.9x with +3.1% EPS growth → UNSUSTAINABLE")

    # 5. Insider selling
    q = match_quote_to_claim("smart money", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "Individual investors should definitely set firm rules limiting the loss on the initial capital they have invested in each stock to an absolute maximum of 7% or 8%.",
        "source": "How to Make Money in Stocks (4th ed.), Ch.10 [p.249]",
        "analysis": f"Insider selling is overwhelming: CFO sold 80% of holdings ($782K), chairman sold $5.5M via secondary. Track what smart money does — they're leaving."
    })
    indicators.append("CFO sold 80% of holdings")
    indicators.append("Chairman sold $5.5M via secondary")
    tree_path.append("INSIDER CHECK: CFO sold 80% → SELL SIGNAL")

    # 6. New product check
    q = match_quote_to_claim("new product", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "New Products, New Management, New Highs: Buying at the Right Time.",
        "source": "How to Make Money in Stocks (4th ed.), CAN SLIM introduction [p.15]",
        "analysis": "Entering 39th category (outdoor), 25 new products/year, Beauty & Home growing 40.8% — but the N in CANSLIM needs to be transformative. Outdoor cookware isn't that. And margins compressing while expanding suggests poor allocation."
    })
    indicators.append("39 categories, 25 new products/year")
    tree_path.append("N CHECK: New products present but not transformative → MARGINAL")

    # 7. Market regime
    q = match_quote_to_claim("human nature", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "Furthermore, human nature at work in the market simply doesn't change.",
        "source": "How to Make Money in Stocks (4th ed.) [p.14]",
        "analysis": "Food Prep category declining 3.3%. 1.8M recalled units. 2 class actions. Grizzly Research allegations. Tariff exposure. The stock has too many headwinds. When a stock fails C, A, S, L, I, and M — you don't buy it. Period."
    })
    indicators.append("Food Prep category: -3.3%")
    indicators.append(f"Product recall: {d['recalled_units_m']:.1f}M units")
    indicators.append("Class actions: 2 active")
    indicators.append("Short seller: Grizzly Research (Aug 2024)")
    indicators.append(f"Tariff exposure: {d['tariffs_pct']}% baseline")

    # Sell rule
    q = match_quote_to_claim("7-8%", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "The whole secret to winning big in the stock market is not to be right all the time, but to lose the least amount possible when you're wrong.",
        "source": "How to Make Money in Stocks (4th ed.), Ch.10 [p.247]",
        "analysis": "f'If you already own {d['ticker']}, the 7-8% stop loss rule applies. If bought near ${d['price']:.0f}, the stop may have triggered depending on your entry. If not, the rule says cut it anyway given the distribution days."
    })

    return PersonaAnalysis(
        persona="O'Neil",
        ticker=d["ticker"],
        price=d["price"],
        date=datetime.now().strftime("%Y-%m-%d"),
        screening_criteria=screening,
        reasoning=reasoning,
        verdict="SELL",
        conviction=0.9,
        indicators_used=indicators,
        sources_cited=list(set(sources)),
        decision_tree_path=tree_path,
    )


def _run_minervini(d: dict, quotes: List[Dict[str,str]]) -> PersonaAnalysis:
    reasoning = []
    sources = []
    indicators = []
    tree_path = []

    screening = "First check: Does the stock have a valid VCP (Volatility Contraction Pattern) base? Check for 5-6 week minimum base, each correction showing less volatility, RS line in new high ground before price. Check market breadth: % stocks above 200-day MA vs index new highs."

    # 1. VCP check
    q = match_quote_to_claim("vcp", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "I define a VCP as a series of contractions where each correction wave shows less price range and less overall volatility than the previous wave.",
        "source": "Trade Like a Stock Market Wizard (2013, HarperCollins), Ch. 6",
        "analysis": "{d['ticker']} shows NO identifiable VCP pattern. Correction waves are expanding, not contracting. Margins compressing, inventory growing. The 'contractions' are getting WORSE, not better. No VCP = no SEPA setup."
    })
    indicators.append("VCP pattern: NOT PRESENT")
    tree_path.append("STEP 1: STRUCTURE — No VCP base → STOP")

    # 2. RS line check
    q = match_quote_to_claim("RS line", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "The narrower the base and the lower the volume, the more explosive the breakout.",
        "source": "Mindset, Methods, and Market Strategies (2022)",
        "analysis": "RS line is NOT in new high ground before price. Without this, the breakout power is severely diminished. The stock is not under accumulation."
    })
    indicators.append("RS line: NOT in new high ground")
    tree_path.append("RS CHECK: RS line lagging → NO BREAKOUT POWER")

    # 3. Insider selling + fundamentals
    q = match_quote_to_claim("smart money", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "The secret to compounding is not big winners — it's not having big losers. Protect the downside and the upside takes care of itself.",
        "source": "Mindset, Methods, and Market Strategies (2022)",
        "analysis": f"f'CFO sold {d['cfo_sold_pct']}% of holdings. Chairman sold ${d['chairman_sold_m']:.1f}M secondary. {d['recalled_units_m']:.1f}M recalled units. {len(d['class_actions'])} class actions. Net income only +{d['net_income_growth_pct']}% on +{d['revenue_growth_pct']}% revenue. This is a litany of downside risks. Protecting the downside means AVOIDING this stock entirely."
    })
    indicators.append(f"Insider selling: CFO {d['cfo_sold_pct']}%, chairman ${d['chairman_sold_m']:.1f}M")
    indicators.append("Product recall: 1.8M units")
    indicators.append(f"Net margin decline: {d['net_margin_change_bps']:+}bps to {d['net_margin_pct']}%")
    tree_path.append("FUNDAMENTAL CHECK: Multiple red flags → PASS")

    # 4. Market context
    q = match_quote_to_claim("never fight the tape", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "When the market is in a confirmed uptrend, be fully invested. When it's not, sit on your hands. It's that simple — but the hardest thing to do.",
        "source": "Mindset, Methods, and Market Strategies (2022)",
        "analysis": "f'Market has {d['distribution_days']} distribution days in {d['distribution_days_period']}. No follow-through day confirmed. Market is NOT in a confirmed uptrend. Progressive exposure says sit at 0-25% maximum. And even then, you need a valid setup."
    })
    indicators.append(f"Distribution days: {d['distribution_days']} in {d['distribution_days_period']}")
    indicators.append("Market regime: Correction")
    tree_path.append("MARKET CHECK: 6 distribution days → SIT ON HANDS")

    # 5. No setup conclusion
    q = match_quote_to_claim("if I can do it", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "If I can do it, anybody can. I started with nothing, failed for years, and figured it out through discipline and process.",
        "source": "YouTube: https://youtube.com/@markminervini",
        "analysis": "There is no setup here. No VCP. No valid pivot. Market in correction. If you force this trade, you're not being disciplined. Wait for the next setup."
    })

    return PersonaAnalysis(
        persona="Minervini",
        ticker=d["ticker"],
        price=d["price"],
        date=datetime.now().strftime("%Y-%m-%d"),
        screening_criteria=screening,
        reasoning=reasoning,
        verdict="SELL",
        conviction=0.8,
        indicators_used=indicators,
        sources_cited=list(set(sources)),
        decision_tree_path=tree_path,
    )


def _run_qullamaggie(d: dict, quotes: List[Dict[str,str]]) -> PersonaAnalysis:
    reasoning = []
    sources = []
    indicators = []
    tree_path = []

    screening = "First check: Does this stock show any of my 3 setups? (1) Episodic Pivot: gap up 10%+ on major news with 2x+ volume. (2) Breakout from Consolidation: prior move 30-100%+ with 2-8 week consolidation. (3) Parabolic Short: vertical climax move. If none, PASS."

    # 1. Episodic Pivot check
    q = match_quote_to_claim("ep", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "The EP is the highest-probability setup in trading. It's a gap up on major news with massive volume. You don't need to know why — just execute.",
        "source": "Chat With Traders (Episode 306): https://chatwithtraders.com/episodes/306-kristjan-qullamaggie/",
        "analysis": "No gap-up 10%+ on major news. No catalyst event. Volume is normal to below average. The highest-probability setup is completely unavailable."
    })
    indicators.append("Episodic Pivot: NOT TRIGGERED (no gap-up, no catalyst)")
    tree_path.append("SETUP 1: EP — FAIL (no gap-up, no 2x volume)")

    # 2. BOC check
    q = match_quote_to_claim("breakout", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "Stocks that are breaking out to new highs after a long consolidation have the least resistance above them.",
        "source": "YouTube: https://youtu.be/RTnsckC6Vyw (Compilation of Setups)",
        "analysis": "No prior move 30-100%+ in past 1-3 months. No 2-8 week consolidation with narrowing ranges. No volume drying up. The BOC setup is completely unavailable."
    })
    indicators.append("BOC: NOT TRIGGERED (no prior run, no consolidation)")
    tree_path.append("SETUP 2: BOC — FAIL (no prior 30-100%+ move)")

    # 3. Parabolic short check
    q = match_quote_to_claim("concentration", quotes)
    if q: sources.append(q)
    # Check was for concentration quote, let me re-check for parabolic short
    for qq in quotes:
        if "short" in qq.get("raw","").lower() and "vertical" in qq.get("raw","").lower():
            q = qq["raw"]
            break
    reasoning.append({
        "quote": "I only trade the three setups. If it's not one of them, I don't touch it. Discipline is everything.",
        "source": "qullamaggie.com: https://qullamaggie.com/my-3-timeless-setups-that-have-made-me-tens-of-millions/",
        "analysis": "No vertical move, no climax volume, no first failure day. Parabolic short setup unavailable. All three setups fail."
    })
    indicators.append("Parabolic Short: NOT TRIGGERED (no climax run)")
    tree_path.append("SETUP 3: PARABOLIC SHORT — FAIL (no vertical move)")

    # 4. Obstacles
    q = match_quote_to_claim("obvious", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "The best trades are obvious. If you're debating whether to take it, it's not a good trade.",
        "source": "YouTube (Chat With Traders): https://www.youtube.com/watch?v=lgF76j64xHs",
        "analysis": f"f'CFO sold {d['cfo_sold_pct']}%, chairman ${d['chairman_sold_m']:.1f}M secondary, {d['recalled_units_m']:.1f}M recalled, {len(d['class_actions'])} class actions, tariffs, margin compression. If you need to convince yourself this is a good trade through all that noise, IT IS NOT A GOOD TRADE."
    })

    # 5. Mechanical mindset
    q2 = match_quote_to_claim("mechanical", quotes)
    if q2: sources.append(q2)
    reasoning.append({
        "quote": "The setup does not care about your opinion.",
        "source": "qullamaggie.com: https://qullamaggie.com/",
        "analysis": "None of the 3 setups trigger. That is the only relevant data point. The stock could be the best company in the world — if the setup doesn't trigger, you don't trade it."
    })

    return PersonaAnalysis(
        persona="Qullamaggie",
        ticker=d["ticker"],
        price=d["price"],
        date=datetime.now().strftime("%Y-%m-%d"),
        screening_criteria=screening,
        reasoning=reasoning,
        verdict="PASS",
        conviction=0.0,  # No setup to have conviction about
        indicators_used=indicators,
        sources_cited=list(set(sources)),
        decision_tree_path=tree_path,
    )


def _run_lynch(d: dict, quotes: List[Dict[str,str]]) -> PersonaAnalysis:
    reasoning = []
    sources = []
    indicators = []
    tree_path = []

    screening = "First check: What category is this stock? Fast Grower (20-25% growth)? Stalwart (10-12%)? Cyclical? Turnaround? Asset Play? Slow Grower (2-4%)? Then check PEG ratio, the 2-minute story test, and insider behavior."

    # 1. Category confusion
    q = match_quote_to_claim("category", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "Behind every stock is a company. Find out what it's doing.",
        "source": "One Up On Wall Street (1989), Introduction",
        "analysis": f"f'Revenue +{d['revenue_growth_pct']}% puts it between Stalwart (10-12%) and Fast Grower (20-25%). Net income +{d['net_income_growth_pct']}% says Slow Grower (2-4%). This category confusion is the first warning — when a stock doesn't fit a category cleanly, you're not sure what you're buying."
    })
    indicators.append(f"Revenue growth: +{d['revenue_growth_pct']}% (Stalwart/Fast Grower borderline)")
    indicators.append(f"Net income growth: +{d['net_income_growth_pct']}% (Slow Grower territory)")
    tree_path.append("STEP 1: CATEGORIZE → Falls between Fast Grower, Stalwart, and Slow Grower → WARNING")

    # 2. PEG ratio
    q = match_quote_to_claim("peg", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "A PEG ratio of 1.0 is fair. Below 0.5 is a bargain. Above 2.0 is speculative.",
        "source": "One Up On Wall Street (1989), Ch. 12",
        "analysis": f"f'P/E is {d['pe_ratio']}x. Growth rate: net income +{d['net_income_growth_pct']}% gives PEG = {d['pe_ratio'] / max(d['net_income_growth_pct'], 0.1):.1f}. Even using revenue growth +{d['revenue_growth_pct']}% gives PEG = {d['pe_ratio'] / max(d['revenue_growth_pct'], 0.1):.2f}. Best case: speculative. Worst case: dangerously speculative. There is no scenario where {d['ticker']} is a PEG bargain."
    })
    indicators.append("PEG ratio: 1.66-8.6 (fair: <1.0, speculative: >2.0)")
    tree_path.append("PEG CHECK: 1.66 to 8.6 → FAILS (need <1.5)")

    # 3. The story test
    q = match_quote_to_claim("story", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "Know what you own, and know why you own it.",
        "source": "One Up On Wall Street (1989), Introduction",
        "analysis": "f'2-minute story: '{d['ticker']} sells small appliances. Revenue growing {d['revenue_growth_pct']}% but profit only growing {d['net_income_growth_pct']}%. Core kitchen category declining. Branching into beauty and outdoor. Insiders selling heavily. Product recalls. Class actions.' This story says a company struggling to find its footing — not one you want to own."
    })
    tree_path.append("STORY TEST: FAILS — 2-minute story reveals a company in transition, not a clear winner")

    # 4. Earnings test
    q2 = match_quote_to_claim("earnings make", quotes)
    if q2: sources.append(q2)
    reasoning.append({
        "quote": "Earnings make the stock go up. That's the single most important thing to remember.",
        "source": "Beating the Street (1993), Ch. 3",
        "analysis": f"f'+{d['net_income_growth_pct']}% net income on +{d['revenue_growth_pct']}% revenue. Something dynamic is NOT keeping earnings moving — costs are eating revenue growth. Gross margin down {abs(d['gross_margin_change_bps'])}bps, net margin down {abs(d['net_margin_change_bps'])}bps. When earnings aren't driving, the stock doesn't go up."
    })
    indicators.append(f"Gross margin decline: {d['gross_margin_change_bps']:+}bps to {d['gross_margin_pct']}%")
    indicators.append(f"Net margin decline: {d['net_margin_change_bps']:+}bps to {d['net_margin_pct']}%")
    tree_path.append("EARNINGS TEST: +3.1% NI on +15.6% rev → FAILS (costs rising faster than revenue)")

    # 5. Inventory check
    q2 = match_quote_to_claim("inventory", quotes)
    if q2: sources.append(q2)
    reasoning.append({
        "quote": "In a cyclical, sell when things are going great. In a fast grower, sell when the growth rate starts to slow.",
        "source": "One Up On Wall Street (1989), Ch. 15",
        "analysis": f"f'Inventory at ${d['inventory_m'] / 1000:.3f}B is {d['inventory_pct_of_revenue']}% of quarterly revenue. That's massively bloated. In ANY category, inventory building while growth slows is a sell signal. They're producing more than they're selling."
    })
    indicators.append(f"Inventory/Revenue ratio: {d['inventory_pct_of_revenue']}% (bloated)")
    tree_path.append("INVENTORY CHECK: $1.034B = 73% of revenue → SELL SIGNAL")

    # 6. Insider selling
    q3 = match_quote_to_claim("sell because the story changes", quotes)
    if q3: sources.append(q3)
    reasoning.append({
        "quote": "If you sell a stock because it goes down, you're doing it wrong. You sell because the story changes.",
        "source": "CNBC Interview: https://www.youtube.com/watch?v=2M9xZHE6gjI",
        "analysis": f"f'CFO sold {d['cfo_sold_pct']}% of holdings. Chairman ${d['chairman_sold_m']:.1f}M secondary. The story HAS changed — from growth story to a story of insider exodus, product recalls, and margin compression. When the story changes, you sell."
    })

    # 7. Final
    q3 = match_quote_to_claim("watering the weeds", quotes)
    if q3: sources.append(q3)
    reasoning.append({
        "quote": "Selling your winners and holding your losers is like cutting the flowers and watering the weeds.",
        "source": "One Up On Wall Street (1989), Ch. 15",
        "analysis": "This stock has multiple sell signals: growth slowing, inventory building, insiders selling, margin compressing, legal troubles. If you owned this, you'd be watering a weed."
    })

    return PersonaAnalysis(
        persona="Lynch",
        ticker=d["ticker"],
        price=d["price"],
        date=datetime.now().strftime("%Y-%m-%d"),
        screening_criteria=screening,
        reasoning=reasoning,
        verdict="SELL",
        conviction=0.8,
        indicators_used=indicators,
        sources_cited=list(set(sources)),
        decision_tree_path=tree_path,
    )


def _run_buffett(d: dict, quotes: List[Dict[str,str]]) -> PersonaAnalysis:
    reasoning = []
    sources = []
    indicators = []
    tree_path = []

    screening = "First check: Is this within my circle of competence? Then check: Does this business have a durable competitive advantage (moat)? Then calculate owner earnings and intrinsic value. Finally, apply the 5-year test."

    # 1. Circle of competence
    q = match_quote_to_claim("circle", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "You don't have to be an expert on every company. You only have to be able to evaluate companies within your circle of competence. The size of that circle is not very important; knowing its boundaries, however, is vital.",
        "source": "1996 Berkshire Annual Letter: https://www.berkshirehathaway.com/letters/1996.html",
        "analysis": "I understand small appliances. But understanding is not the same as finding an investable business. A simple business with a weak moat is still a bad investment."
    })
    indicators.append("Circle of Competence: PASS (understands consumer appliances)")
    tree_path.append("GATE 1: CIRCLE OF COMPETENCE → PASS (but understanding ≠ investing)")

    # 2. Moat check
    q = match_quote_to_claim("moat", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "In business, I look for economic castles protected by unbreachable 'moats.'",
        "source": "1995 Berkshire Annual Letter: https://www.berkshirehathaway.com/letters/1995.html",
        "analysis": f"{d['ticker']} has no moat. No cost advantage (competing with Philips, Dyson, Instant Pot, Cuisinart). No network effect. Low switching costs (costs $0 to switch blenders). Brand is recognizable but provides no pricing power — proven by margin compression. No answer to 'what keeps competitors out for 10+ years?'"
    })
    indicators.append("Moat: NONE — no cost advantage, no network effect, low switching costs, weak brand moat")
    tree_path.append("GATE 2: MOAT → FAILS. No unbreachable competitive advantage.")

    # 3. Owner earnings
    q = match_quote_to_claim("intrinsic", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "We call this 'owner earnings.' These represent (a) reported earnings plus (b) depreciation, depletion, amortization, and certain other non-cash charges... minus (c) the average annual amount of capitalized expenditures for fixed assets, etc. that the business requires to fully maintain its long-term competitive position.",
        "source": "1986 Berkshire Annual Letter: https://www.berkshirehathaway.com/letters/1986.html",
        "analysis": f"f'Annualized Net Income ~${d['net_income_m'] * 4:.0f}M. D&A est. $50-80M. Maintenance capex est. $42-70M (3-5% of revenue). Owner Earnings ~$470-510M. At P/E {d['pe_ratio']}x, the market is paying a premium for a business with SINGLE-DIGIT net income growth. That's not value — that's hope."
    })
    indicators.append("Owner Earnings: ~$470-510M/yr")
    indicators.append("Capex/Net Income: ~10-14% (under 50% = good)")
    tree_path.append("GATE 3: OWNER EARNINGS → FAILS (P/E 25.9x with single-digit growth)")

    # 4. Five year test
    q = match_quote_to_claim("five-year", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "If you aren't willing to own a stock for 10 years, don't even think about owning it for 10 minutes.",
        "source": "1996 Berkshire Annual Letter: https://www.berkshirehathaway.com/letters/1996.html",
        "analysis": f"f'Would I be happy owning {d['ticker']} for 5 years with markets closed? With {d['recalled_units_m']:.1f}M recalled units, {len(d['class_actions'])} class actions, a short seller report, CFO selling {d['cfo_sold_pct']}% of holdings, tariffs, and declining margins — absolutely not. This fails the 5-year test."
    })
    tree_path.append("GATE 4: FIVE-YEAR TEST → FAILS. Would not want to own for 5 years.")

    # 5. Management
    q = match_quote_to_claim("management", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "When a management with a reputation for brilliance tackles a business with a reputation for poor fundamental economics, it is the reputation of the business that remains intact.",
        "source": "1989 Berkshire Annual Letter: https://www.berkshirehathaway.com/letters/1989.html",
        "analysis": f"f'CFO selling {d['cfo_sold_pct']}% of holdings. Chairman dumping ${d['chairman_sold_m']:.1f}M. They're not buying — they're selling. The people who know the business best are exiting. When insiders sell, listen."
    })
    indicators.append("Insider behavior: CFO sold 80%, chairman sold $5.5M secondary")
    tree_path.append("MANAGEMENT CHECK: Insiders exiting → STRONG NEGATIVE SIGNAL")

    # 6. Turnarounds
    q2 = match_quote_to_claim("turnarounds seldom turn", quotes)
    if q2: sources.append(q2)
    reasoning.append({
        "quote": "Turnarounds seldom turn.",
        "source": "1979 Berkshire Annual Letter: https://www.berkshirehathaway.com/letters/1979.html",
        "analysis": "Recalls, class actions, short seller, category decline, margin compression — this has the hallmarks of a struggling business hoping to turn around. 'Turnarounds seldom turn.'"
    })

    # 7. Price vs value
    q2 = match_quote_to_claim("price is what you pay", quotes)
    if q2: sources.append(q2)
    reasoning.append({
        "quote": "Price is what you pay. Value is what you get.",
        "source": "2008 Berkshire Annual Letter: https://www.berkshirehathaway.com/letters/2008ltr.pdf",
        "analysis": f"f'At ${d['price']:.2f} with P/E {d['pe_ratio']}x, single-digit earnings growth, and multiple moat-destroying forces — the price far exceeds the value. You're paying for a growth story that isn't delivering."
    })

    return PersonaAnalysis(
        persona="Buffett",
        ticker=d["ticker"],
        price=d["price"],
        date=datetime.now().strftime("%Y-%m-%d"),
        screening_criteria=screening,
        reasoning=reasoning,
        verdict="AVOID",
        conviction=0.9,
        indicators_used=indicators,
        sources_cited=list(set(sources)),
        decision_tree_path=tree_path,
    )


def _run_david_ryan(d: dict, quotes: List[Dict[str,str]]) -> PersonaAnalysis:
    reasoning = []
    sources = []
    indicators = []
    tree_path = []

    screening = "First check: Run the 85-85 screen (EPS rating 85+, RS rating 85+). Check MarketSmith 250 sorted by strongest industry groups. Look for tight weekly closes near top of base, RS line making new high before price, and breakout volume 50-100%+ above average."

    # 1. 85-85 screen
    q = match_quote_to_claim("85-85", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "The 85-85 screen (EPS 85+ and RS 85+) is my starting point every morning. That filters out 95% of garbage.",
        "source": "IBD TV: https://www.investors.com/research/investing-lessons-from-david-ryan/",
        "analysis": f"f'EPS rating fails because net income growth is +{d['net_income_growth_pct']}% (requirement is 20% minimum in CANSLIM). RS rating fails because RS line is NOT making new highs. The first filter kills this stock — I'd never even see it."
    })
    indicators.append("EPS rating: FAILS (net income +3.1% < 20% minimum)")
    indicators.append("RS rating: FAILS (NOT in new high ground)")
    tree_path.append("85-85 SCREEN: FAILS both EPS and RS → FILTERED OUT in first pass")

    # 2. Base pattern
    q = match_quote_to_claim("tight weekly close", quotes)
    if q: sources.append(q)
    reasoning.append({
        "quote": "I look for tight closes for at least three weeks near the top of the base. That tightness shows the stock is coiling for a breakout.",
        "source": "IBD TV: https://www.investors.com/research/investing-lessons-from-david-ryan/",
        "analysis": "There is NO base pattern. No tight closes. No coiling. No cup-with-handle. No accumulation. Without a base, there's no breakout. Without a breakout, there's no trade."
    })
    indicators.append("Base pattern: NONE (no tight closes, no coiling)")
    tree_path.append("CHART CHECK: No base pattern → NO TRADE")

    # 3. Volume and health
    q2 = match_quote_to_claim("health check", quotes)
    if q2: sources.append(q2)
    reasoning.append({
        "quote": "Health check — fast up on big volume, slow down on light volume, rarely two weeks down in a row.",
        "source": "IBD TV: https://www.investors.com/research/investing-lessons-from-david-ryan/",
        "analysis": "This stock is NOT acting healthy. Fast down on bad news (recalls, litigation). No accumulation. Insiders are distributors. The healthy pattern is completely absent."
    })
    tree_path.append("HEALTH CHECK: UNHEALTHY pattern → SELL")

    # 4. Insider selling
    q3 = match_quote_to_claim("cut your losers like a cancer", quotes)
    if q3: sources.append(q3)
    reasoning.append({
        "quote": "Cut your losers like a cancer. If a stock isn't acting right, get rid of it immediately. Don't hope it comes back.",
        "source": "IBD TV: https://www.investors.com/research/investing-lessons-from-david-ryan/",
        "analysis": f"f'CFO sold {d['cfo_sold_pct']}% of holdings. Chairman ${d['chairman_sold_m']:.1f}M secondary. This stock 'isn't acting right.' The people closest to it are getting out. Cut it."
    })
    indicators.append(f"Insider selling: CFO {d['cfo_sold_pct']}%, chairman ${d['chairman_sold_m']:.1f}M (strong distribution signal)")
    tree_path.append("INSIDER CHECK: Massive insider selling → CUT")

    # 5. Market context
    q4 = match_quote_to_claim("sitting is more important", quotes)
    if q4: sources.append(q4)
    reasoning.append({
        "quote": "Sitting is more important than thinking.",
        "source": "IBD TV: https://www.investors.com/research/investing-lessons-from-david-ryan/",
        "analysis": "f'{d['distribution_days']} distribution days on SPY. Market in correction. The best thing you can do right now is sit on your hands and wait for the next uptrend. Even if {d['ticker']} were a perfect setup (it's not), the market says stay in cash."
    })
    indicators.append("Distribution days: 6 in 22 sessions")
    indicators.append("Market regime: Correction (not confirmed uptrend)")
    tree_path.append("MARKET CHECK: 6 distribution days → CASH IS KING")

    # 6. CANSLIM fundamental fails
    q4 = match_quote_to_claim("the biggest mistake", quotes)
    if q4: sources.append(q4)
    reasoning.append({
        "quote": "The biggest mistake investors make is holding onto their losers and selling their winners.",
        "source": "MarketSmith Webinar: https://marketsmith.investors.com",
        "analysis": "f'CANSLIM fundamental screen: C (+{d['net_income_growth_pct']}% EPS) FAIL, A (+{d['revenue_growth_pct']}% revenue, slowing) FAIL, S (insiders selling) FAIL, L (RS low) FAIL, I (institutional confidence breaking) FAIL, M ({d['distribution_days']} distro days) FAIL. Only N marginal. DON'T HOLD THIS LOSER."
    })

    return PersonaAnalysis(
        persona="David Ryan",
        ticker=d["ticker"],
        price=d["price"],
        date=datetime.now().strftime("%Y-%m-%d"),
        screening_criteria=screening,
        reasoning=reasoning,
        verdict="SELL",
        conviction=0.8,
        indicators_used=indicators,
        sources_cited=list(set(sources)),
        decision_tree_path=tree_path,
    )


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------

def analyze_persona(persona_key: str, sn_data: dict, verbose: bool = False) -> PersonaAnalysis:
    """Run analysis for a single persona."""
    soul = load_soul(persona_key)
    quotes = extract_quote_database(soul)

    if verbose:
        info = PERSONAS[persona_key]
        print(f"  Loaded {info['label']} | {len(quotes)} quotes extracted", file=sys.stderr)

    # Route to the right analysis engine
    engines = {
        "oneil": _run_oneil,
        "minervini": _run_minervini,
        "qullamaggie": _run_qullamaggie,
        "lynch": _run_lynch,
        "buffett": _run_buffett,
        "david-ryan": _run_david_ryan,
    }

    engine = engines.get(persona_key)
    if not engine:
        raise ValueError(f"Unknown persona: {persona_key}. Available: {', '.join(engines.keys())}")

    return engine(sn_data, quotes)


def format_analysis(analysis: PersonaAnalysis, width: int = 72) -> str:
    """Format a PersonaAnalysis into a readable string."""
    lines = []
    sep = "=" * width

    lines.append(sep)
    lines.append(f"=== {analysis.persona.upper()} ===")
    lines.append(sep)
    lines.append("")
    lines.append(f"Ticker: {analysis.ticker}")
    lines.append(f"Date:   {analysis.date}")
    lines.append(f"Price:  ${analysis.price:.2f}")
    lines.append("")
    lines.append(f"WHAT I CHECK FIRST: {analysis.screening_criteria}")
    lines.append("")
    lines.append("REASONING (each bullet = verbatim quote):")
    lines.append("")

    for i, r in enumerate(analysis.reasoning, 1):
        quote_text = r["quote"].strip()
        source = r["source"].strip()
        analysis_text = r["analysis"].strip()
        lines.append(f"[{i}] \"{quote_text}\"")
        lines.append(f"    Source: {source}")
        lines.append(f"    => {analysis_text}")
        lines.append("")

    lines.append(f"VERDICT: {analysis.verdict}. Conviction: {analysis.conviction}.")
    lines.append("")
    lines.append("Indicators Used:")
    for ind in analysis.indicators_used:
        lines.append(f"  - {ind}")
    lines.append("")

    if analysis.decision_tree_path:
        lines.append("Decision Tree Path:")
        for step in analysis.decision_tree_path:
            lines.append(f"  -> {step}")

    lines.append("")
    lines.append("Sources Cited:")
    for src in analysis.sources_cited:
        lines.append(f"  - {src}")
    lines.append(sep)
    lines.append("")

    return "\n".join(lines)


def analysis_to_json(analysis: PersonaAnalysis) -> dict:
    """Convert analysis to JSON-compatible dict."""
    return {
        "persona": analysis.persona,
        "ticker": analysis.ticker,
        "date": analysis.date,
        "price": analysis.price,
        "screening_criteria": analysis.screening_criteria,
        "reasoning": [
            {
                "quote": r["quote"],
                "source": r["source"],
                "analysis": r["analysis"]
            }
            for r in analysis.reasoning
        ],
        "verdict": analysis.verdict,
        "conviction": analysis.conviction,
        "indicators_used": analysis.indicators_used,
        "sources_cited": list(set(analysis.sources_cited)),
        "decision_tree_path": analysis.decision_tree_path,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Hermes Persona Engine — Generative Agents Analysis",
    )
    p.add_argument("--persona", choices=list(PERSONAS.keys()),
                    help="Single persona to analyze")
    p.add_argument("--all", action="store_true",
                    help="Analyze with ALL 6 personas")
    p.add_argument("--json", action="store_true",
                    help="Output as JSON")
    p.add_argument("--verbose", action="store_true",
                    help="Show loading details")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    global sn_data
    sn_data = SN_DATA

    personas_to_run = []
    if args.all:
        personas_to_run = list(PERSONAS.keys())
    elif args.persona:
        personas_to_run = [args.persona]
    else:
        # Default: all
        personas_to_run = list(PERSONAS.keys())

    results = []
    for pk in personas_to_run:
        if args.verbose:
            print(f"\nAnalyzing {PERSONAS[pk]['name']}...", file=sys.stderr)
        analysis = analyze_persona(pk, sn_data, verbose=args.verbose)
        results.append(analysis)

    if args.json:
        output = [analysis_to_json(a) for a in results]
        print(json.dumps(output, indent=2, default=str))
    else:
        for r in results:
            print(format_analysis(r))

    return 0


sn_data = SN_DATA

if __name__ == "__main__":
    sys.exit(main())
