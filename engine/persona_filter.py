"""
engine/persona_filter.py — Phase 2: Filter stocks by persona methodology criteria.
Each persona has documented rules. A ticker can be assigned to at most ONE persona.
"""

from collections import defaultdict

from engine.config import PERSONAS
from engine.utils import _safe_float


def filter_by_persona_criteria(persona, stock):
    """
    Phase 2b: Filter stock against a persona's documented methodology.
    Returns (passes: bool, reason: str).
    """
    price = _safe_float(stock.get("price"))
    pe = _safe_float(stock.get("pe"))
    eps = _safe_float(stock.get("eps"))
    eps_growth = _safe_float(stock.get("eps_growth"))
    rsi = _safe_float(stock.get("rsi"))
    ma50 = _safe_float(stock.get("ma50"))
    ma200 = _safe_float(stock.get("ma200"))
    mcap = _safe_float(stock.get("mcap"))
    vol_ratio = _safe_float(stock.get("vol_ratio"))
    change_pct = _safe_float(stock.get("change_pct"))
    volume = _safe_float(stock.get("volume") or stock.get("Volume"))

    if persona == "oneil":
        if pe is None or pe <= 0: return False, "PE missing or non-positive"
        if pe < 5 or pe > 50: return False, f"PE {pe:.1f} outside 5-50"
        if eps_growth is None or eps_growth <= 0: return False, "No pos EPS growth"
        if rsi is not None and rsi <= 30: return False, f"RSI {rsi:.0f} <= 30"
        if ma50 is not None and price is not None and price <= ma50:
            return False, f"Price ${price:.2f} <= MA50 ${ma50:.2f}"
        if price is None: return False, "No price"
        return True, "O'Neil: PE 5-50, EPS>0, RSI>30, price>MA50"

    if persona == "buffet":
        if pe is None or pe <= 0: return False, "PE missing/non-positive"
        if pe < 5 or pe > 25: return False, f"PE {pe:.1f} outside 5-25"
        if eps is None or eps <= 0: return False, "EPS <= 0"
        if mcap is None or mcap < 10e9: return False, "mcap < 10B"
        if price is None: return False, "No price"
        return True, "Buffett: PE 5-25, EPS>0, mcap>10B"

    if persona == "lynch":
        if pe is None or pe <= 0: return False, "PE missing/non-positive"
        if eps_growth is None or eps_growth <= 0: return False, "No pos EPS growth"
        peg = pe / (eps_growth * 100) if eps_growth > 0 else 999
        if peg >= 3.0: return False, f"PEG {peg:.2f} >= 3.0"
        if price is None: return False, "No price"
        return True, "Lynch: PE>0, EPS>0, PEG<3.0"

    if persona == "minervini":
        if price is None: return False, "No price"
        if ma50 is not None and price <= ma50: return False, f"Price <= MA50 ${ma50:.2f}"
        if ma200 is not None and ma50 is not None and ma50 <= ma200:
            return False, f"MA50 ${ma50:.2f} <= MA200 ${ma200:.2f}"
        if rsi is not None and (rsi < 30 or rsi > 80): return False, f"RSI {rsi:.0f} outside 30-80"
        if eps_growth is not None and eps_growth <= 0: return False, "Neg EPS growth"
        return True, "Minervini: trend template, RSI 30-80"

    if persona == "qullamaggie":
        if price is None: return False, "No price"
        cond = 0
        if vol_ratio is not None and vol_ratio > 1.0: cond += 1
        if change_pct is not None and change_pct > 2: cond += 1
        if ma50 is not None and price > ma50: cond += 1
        if cond == 0: return False, "No criteria met"
        return True, f"Qullamaggie: {cond}/3 criteria"

    if persona == "david-ryan":
        if eps_growth is not None and eps_growth > 0.1:
            return True, f"David Ryan: EPS growth {eps_growth*100:.1f}% > 10%"
        if eps_growth is None or eps_growth <= 0.1:
            if ma50 is not None and price is not None and price > ma50 and vol_ratio is not None and vol_ratio > 1.2:
                return True, f"David Ryan: price>MA50 + vol_ratio {vol_ratio:.1f} > 1.2"
            return False, "Need EPS>10% OR (price>MA50 AND vol_ratio>1.2)"

    if persona == "matt-caruso":
        if price is None: return False, "No price"
        if price <= 2.0: return False, f"Price ${price:.2f} <= $2"
        if volume is not None and volume <= 50000: return False, f"Vol <= 50000"
        return True, "Caruso: price > 2, vol > 50000"

    if persona == "brian-shannon":
        if price is None: return False, "No price"
        cond = 0
        if ma50 is not None and price > ma50: cond += 1
        if rsi is not None and rsi > 40: cond += 1
        if cond == 0: return False, "Need price>MA50 OR RSI>40"
        return True, "Shannon: uptrend or RSI>40"

    if persona == "dan-zanger":
        if price is None: return False, "No price"
        cond = 0
        if change_pct is not None and change_pct > 1: cond += 1
        if vol_ratio is not None and vol_ratio > 1.2: cond += 1
        if ma50 is not None and price > ma50: cond += 1
        if cond == 0: return False, "Need change>1% OR VR>1.2 OR price>MA50"
        return True, f"Zanger: {cond}/3 criteria"

    if persona == "nick-schmidt":
        if price is None: return False, "No price"
        cond = 0
        if ma50 is not None and price > ma50: cond += 1
        if rsi is not None and rsi > 40: cond += 1
        if cond == 0: return False, "Need price>MA50 OR RSI>40"
        return True, "Schmidt: uptrend or RSI>40"

    return False, f"Unknown persona: {persona}"


def assign_stocks_to_personas(all_stock_data):
    """
    Screen all valid stocks against each persona's methodology criteria.
    Cross-persona dedup: each ticker assigned to at most ONE persona.
    Returns {persona: {ticker: data}}.
    """
    valid = {t: d for t, d in all_stock_data.items()
             if d and "error" not in d and d.get("price")}

    persona_stocks = defaultdict(dict)
    assigned = set()

    for persona in PERSONAS:
        passed = 0
        for ticker, data in list(valid.items()):
            if ticker in assigned:
                continue
            ok, _ = filter_by_persona_criteria(persona, data)
            if ok:
                persona_stocks[persona][ticker] = data
                assigned.add(ticker)
                passed += 1
        print(f"[Filter]  {persona}: {passed} stocks assigned", flush=True)

    return dict(persona_stocks)
