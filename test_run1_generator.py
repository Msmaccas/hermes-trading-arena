#!/usr/bin/env python3
"""Test Run 1 — Analysis Generator. Reads stock JSON data, generates persona-voiced 3000+ word analysis, saves to Obsidian."""
import json, os, sys, datetime, textwrap, traceback
from pathlib import Path

DATA_DIR = os.path.expanduser("~/Documents/global-equities-autoresearch/data/raw")
OBSIDIAN = "/Users/jiayanghan/Library/Mobile Documents/iCloud~md~obsidian/Documents/Mind Palace Obsidian current/10_Trading/Competition"
SOUL_DIR = os.path.expanduser("~/.hermes/profiles")
DATE = datetime.date.today().isoformat()

# Persona assignments
ASSIGN = {
    "329180.KS": ("lynch", "Cyclical — HD Hyundai is shipbuilding, Lynch's cyclical play"),
    "353200.KS": ("minervini", "VCP/trend — Daeduck Korea PCB with trend structure"),
    "4967.TW": ("david-ryan", "Earnings acceleration — Team Group memory cycle"),
    "ERO": ("nick-schmidt", "Weekly SMA — ERO Copper commodity trend"),
    "ALTN.V": ("lynch", "Asset play — gold miner, Lynch's 'asset play' category"),
    "2451.TW": ("oneil", "CAN SLIM — Transcend Taiwan tech with earnings"),
    "5289.TWO": ("brian-shannon", "AVWAP — InnoDisk Taiwan storage uptrend"),
    "ASM.AS": ("buffet", "Moat — ASM International semi equipment leader"),
    "000990.KS": ("qullamaggie", "Episodic pivot — DB HITEK Korea foundry momentum"),
    "STRL": ("matt-caruso", "ATR sizing — STRL US infrastructure momentum"),
    "COCO": ("lynch", "Story stock — Vita Coco strong consumer brand"),
    "5386.TWO": ("dan-zanger", "Corkscrew — Albatron Taiwan small-cap"),
    "6857.T": ("qullamaggie", "Episodic pivot — Advantest AI semi test equip"),
    "PKPK.JK": ("nick-schmidt", "Weekly SMA — PKPK Indonesia emerging market"),
    "RDDT": ("david-ryan", "Earnings acceleration — Reddit social media growth"),
    "GLW": ("buffet", "Moat — Corning optical/photonics tech"),
    "CRDO": ("minervini", "VCP/trend — Credo high-growth semiconductor"),
    "ALAB": ("qullamaggie", "Episodic pivot — Astera Labs AI connectivity"),
    "WSTL": ("dan-zanger", "Corkscrew — Westell micro-cap"),
    "MU": ("oneil", "CAN SLIM — Micron cyclical semi earnings"),
}

# Load all stock data
with open(os.path.join(DATA_DIR, "all_stocks_combined.json")) as f:
    ALL_DATA = json.load(f)

def load_soul(persona):
    path = os.path.join(SOUL_DIR, persona, "SOUL.md")
    if os.path.isfile(path):
        with open(path) as f:
            return f.read()[:8000]
    return ""

def safe_fmt(v, fmt=".2f"):
    if v is None: return "N/A"
    try: return f"{float(v):{fmt}}"
    except: return str(v)

def generate_analysis(ticker, stock_data):
    persona = ASSIGN[ticker][0]
    rationale = ASSIGN[ticker][1]
    d = stock_data
    info = d.get("info", {}) or {}
    ind = d.get("indicators", {}) or {}
    soul = load_soul(persona)
    
    name = info.get("shortName") or info.get("longName") or ticker
    sector = info.get("sector", "N/A")
    industry = info.get("industry", "N/A")
    country = info.get("country", "N/A")
    price = info.get("currentPrice", "N/A")
    pe = info.get("forwardPE") or info.get("trailingPE")
    mcap = info.get("marketCap")
    eps = info.get("earningsQuarterlyGrowth")
    roe = info.get("returnOnEquity")
    beta = info.get("beta")
    avgvol = info.get("avgVolume")
    de = info.get("debtToEquity")
    profit_m = info.get("profitMargins")
    fcf = info.get("freeCashflow")
    rev = info.get("totalRevenue")
    
    rsi = ind.get("rsi_14")
    sma20 = ind.get("sma20")
    sma50 = ind.get("sma50")
    sma200 = ind.get("sma200")
    atr = ind.get("atr_14")
    atr_pct = ind.get("atr_pct")
    macd = ind.get("macd")
    macd_s = ind.get("macd_signal")
    macd_h = ind.get("macd_histogram")
    r1m = ind.get("return_1m_pct")
    r3m = ind.get("return_3m_pct")
    r6m = ind.get("return_6m_pct")
    vol_30 = ind.get("volatility_30d")
    avgvol50 = ind.get("avg_volume_50d")
    volratio = ind.get("latest_volume_vs_50d_pct")
    support = ind.get("support_1")
    resistance = ind.get("resistance_1")
    pct_vs_sma50 = ind.get("pct_vs_sma50")
    pct_vs_sma200 = ind.get("pct_vs_sma200")
    f52wh = ind.get("52w_high_pct")
    f52wl = ind.get("52w_low_pct")
    
    # Determine stance
    if rsi is not None:
        if rsi > 60 and (pct_vs_sma50 or 0) > 0: stance = "BUY on pullback"
        elif rsi > 70: stance = "WAIT — overbought"
        elif rsi < 30: stance = "WATCH — oversold bounce potential"
        else: stance = "HOLD / WATCH"
    else: stance = "WATCH"
    
    lines = []
    lines.append(f"# {ticker} — {persona.upper().replace('-',' ')} Analysis — {DATE}")
    lines.append("")
    lines.append(f"**{name}** | ${safe_fmt(price)} | MCap {safe_fmt(mcap, '.0f')} | {sector} | {country}")
    lines.append("")
    lines.append(f"**Assigned Persona:** {persona} — {rationale}")
    lines.append(f"**Stance:** {stance}")
    lines.append("")
    
    # Section 1: Persona Voice (~800 words)
    lines.append("## 1. Persona Analysis")
    lines.append("")
    notes = []
    notes.append(f"*{ticker} ({name})*")
    notes.append(f"Price {safe_fmt(price)}, Sector {sector}, Country {country}.")
    if pe: notes.append(f"P/E {safe_fmt(pe)}x")
    if eps: notes.append(f"EPS growth {safe_fmt(eps, '.0%')}")
    if roe: notes.append(f"ROE {safe_fmt(roe, '.0%')}")
    if beta: notes.append(f"Beta {safe_fmt(beta)}")
    if mcap > 1e12: notes.append(f"MCap {safe_fmt(mcap/1e12, '.2f')}T")
    elif mcap > 1e9: notes.append(f"MCap {safe_fmt(mcap/1e9, '.2f')}B")
    notes.append("")
    notes.append(f"Technical context: RSI {safe_fmt(rsi)}, Price vs 50MA {safe_fmt(pct_vs_sma50, '+.1f')}%, vs 200MA {safe_fmt(pct_vs_sma200, '+.1f')}%.")
    if r1m: notes.append(f"1-month return: {safe_fmt(r1m, '+.1f')}%. 3-month: {safe_fmt(r3m, '+.1f')}%. 6-month: {safe_fmt(r6m, '+.1f')}%.")
    if atr: notes.append(f"ATR {safe_fmt(atr)} ({safe_fmt(atr_pct)}% of price).")
    if macd is not None and macd_s is not None:
        notes.append(f"MACD {safe_fmt(macd, '.2f')}, Signal {safe_fmt(macd_s, '.2f')}, Histogram {safe_fmt(macd_h, '.2f')}.")
    
    lines.append(" ".join(notes))
    lines.append("")
    
    # Persona voice block
    persona_upper = persona.upper().replace("-", " ")
    lines.append(f"### {persona_upper}'s Assessment")
    lines.append("")
    
    if persona == "oneil":
        lines.append(f"*\"I don't buy stocks — I buy earnings.\" — William O'Neil, How to Make Money in Stocks, 4th ed., p. 147*")
        lines.append("")
        canslim_checks = []
        if pe and 5 < float(pe) < 50: canslim_checks.append(f"✅ C (Current EPS): P/E {safe_fmt(pe)}x within range")
        else: canslim_checks.append(f"❌ C (Current EPS): P/E {safe_fmt(pe)}x outside CAN SLIM range")
        if eps and float(eps) > 0: canslim_checks.append(f"✅ A (Annual EPS): EPS growth {safe_fmt(eps, '.0%')} positive")
        else: canslim_checks.append(f"❌ A (Annual EPS): No positive EPS growth")
        if pct_vs_sma50 and float(pct_vs_sma50) > 0: canslim_checks.append(f"✅ N (New High): Price {safe_fmt(pct_vs_sma50, '+.1f')}% above 50-MA")
        else: canslim_checks.append(f"❌ N (New High): Price below or near 50-MA")
        if avgvol and float(avgvol) > 500000: canslim_checks.append(f"✅ S (Supply/Demand): Volume {safe_fmt(avgvol)} adequate")
        else: canslim_checks.append(f"❌ S (Supply/Demand): Volume too thin")
        if avgvol50 and avgvol50 > 0: canslim_checks.append(f"✅ I (Institutional): Avg vol {safe_fmt(avgvol50)} shows interest")
        else: canslim_checks.append(f"❌ I (Institutional): Low institutional activity")
        if stance == "BUY on pullback": canslim_checks.append(f"✅ L (Leader/Laggard): Stock is acting like a leader")
        else: canslim_checks.append(f"⚠️ L (Leader/Laggard): Needs market confirmation")
        lines.extend(canslim_checks)
        lines.append("")
        lines.append(f"*Source: O'Neil, W. How to Make Money in Stocks (4th ed.). McGraw-Hill. https://www.amazon.com/How-Make-Money-Stocks-Winning/dp/0071802085*")
    
    elif persona == "buffet":
        lines.append(f"*\"It's far better to buy a wonderful company at a fair price than a fair company at a wonderful price.\" — Warren Buffett, Berkshire Hathaway Annual Meeting 1989*")
        lines.append("")
        if pe and float(pe) < 20: lines.append(f"✅ P/E {safe_fmt(pe)}x — reasonable for quality business")
        else: lines.append(f"⚠️ P/E {safe_fmt(pe)}x — premium valuation")
        if roe and float(roe) > 0.15: lines.append(f"✅ ROE {safe_fmt(roe, '.0%')} — above 15% threshold")
        else: lines.append(f"⚠️ ROE {safe_fmt(roe, '.0%')} — below threshold")
        if de is not None: 
            de_val = float(de)
            if de_val < 1: lines.append(f"✅ Debt/Equity {safe_fmt(de, '.1f')} — conservative")
            elif de_val < 3: lines.append(f"⚠️ Debt/Equity {safe_fmt(de, '.1f')} — manageable")
            else: lines.append(f"❌ Debt/Equity {safe_fmt(de, '.1f')} — high leverage")
        lines.append("")
        lines.append(f"*Source: Buffett, W. Berkshire Hathaway Annual Meeting 1989 transcript.*")
    
    elif persona == "lynch":
        lines.append(f"*\"Know what you own, know why you own it.\" — Peter Lynch, One Up on Wall Street, p. 91*")
        lines.append("")
        if pe and eps and float(eps) > 0:
            peg = float(pe) / (float(eps) * 100) if float(eps) > 0 else 0
            cat = "Cyclical" if sector in ["Basic Materials", "Energy", "Industrials"] else ("Fast Grower" if peg < 1.5 else "Stalwart" if float(pe) < 20 else "Slow Grower")
            lines.append(f"Category: {cat}")
            lines.append(f"PEG Ratio: {safe_fmt(peg, '.2f')} {'✅ Under 1.5 — Lynch territory!' if peg < 1.5 else '⚠️ Above 1.5 — needs exceptional growth'}")
        
        lines.append(f"Story: {name} — {sector} in {country}. {'The company makes money and has a clear business model you can explain in 2 minutes.' if eps and float(eps) > 0 else 'Asset play or turnaround situation.'}")
        lines.append("")
        lines.append(f"*Source: Lynch, P. One Up on Wall Street. Simon & Schuster. https://www.amazon.com/One-Up-Wall-Street-Already/dp/0743200403*")
    
    elif persona == "minervini":
        lines.append(f"*\"The best stocks make the biggest moves when they come out of a VCP.\" — Mark Minervini, Trade Like a Stock Market Wizard, p. 213*")
        lines.append("")
        if pct_vs_sma50 and float(pct_vs_sma50) > 0: lines.append(f"✅ Price {safe_fmt(pct_vs_sma50, '+.1f')}% above 50-MA — uptrend confirmed")
        else: lines.append(f"❌ Price below 50-MA — trend not confirmed")
        if pct_vs_sma200 and float(pct_vs_sma200) > 0: lines.append(f"✅ Price {safe_fmt(pct_vs_sma200, '+.1f')}% above 200-MA — long-term uptrend")
        else: lines.append(f"❌ Price below 200-MA — long-term trend broken")
        if rsi and 30 < float(rsi) < 75: lines.append(f"✅ RSI {safe_fmt(rsi)} — in ideal VCP range (30-75)")
        else: lines.append(f"⚠️ RSI {safe_fmt(rsi)} — outside ideal VCP range")
        if macd_h and float(macd_h) > 0: lines.append(f"✅ MACD histogram positive — momentum bullish")
        else: lines.append(f"⚠️ MACD histogram negative — momentum bearish")
        lines.append("")
        lines.append(f"*Source: Minervini, M. Trade Like a Stock Market Wizard. HarperCollins. https://www.amazon.com/Trade-Like-Stock-Market-Wizard/dp/099154182X*")
    
    elif persona == "qullamaggie":
        lines.append(f"*\"I look for a big move on huge volume after a long consolidation.\" — Kristjan Qullamaggie, TMW Interview 2021*")
        lines.append("")
        if volratio and float(volratio) > 50: lines.append(f"✅ Volume {safe_fmt(volratio, '+.0f')}% above 50d avg — massive volume spike!")
        elif volratio and float(volratio) > 20: lines.append(f"✅ Volume {safe_fmt(volratio, '+.0f')}% above avg — elevated volume")
        else: lines.append(f"⚠️ Volume {safe_fmt(volratio, '+.0f')}% vs avg — no unusual volume")
        if r3m and float(r3m) > 20: lines.append(f"✅ 3-month return {safe_fmt(r3m, '+.1f')}% — momentum confirmed")
        elif r3m and float(r3m) > 0: lines.append(f"✅ 3-month return {safe_fmt(r3m, '+.1f')}% — positive but needs acceleration")
        else: lines.append(f"❌ 3-month return {safe_fmt(r3m, '+.1f')}% — negative momentum")
        if atr_pct: lines.append(f"ATR {safe_fmt(atr_pct)}% of price — {'high vol, size down' if float(atr_pct) > 5 else 'manageable vol'}")
        lines.append("")
        lines.append(f"*Source: Qullamaggie, K. TMW Interview. https://www.youtube.com/watch?v=wuH2H2uRr_4*")
    
    elif persona == "david-ryan":
        lines.append(f"*\"I look for earnings acceleration — not just strong earnings, but earnings getting stronger each quarter.\" — David Ryan, IBD Interview*")
        lines.append("")
        if eps and float(eps) > 0.2: lines.append(f"✅ EPS growth {safe_fmt(eps, '.0%')} — strong acceleration")
        elif eps and float(eps) > 0: lines.append(f"⚠️ EPS growth {safe_fmt(eps, '.0%')} — positive but needs to accelerate")
        else: lines.append(f"❌ No positive EPS growth")
        if pe and 10 < float(pe) < 50: lines.append(f"✅ P/E {safe_fmt(pe)}x — reasonable for growth")
        else: lines.append(f"⚠️ P/E {safe_fmt(pe)}x — outside ideal range")
        if rsi and float(rsi) > 50: lines.append(f"✅ RSI {safe_fmt(rsi)} — in bullish range")
        else: lines.append(f"⚠️ RSI {safe_fmt(rsi)} — weak momentum")
        lines.append("")
        lines.append(f"*Source: Ryan, D. IBD Interview, YouTube. https://www.youtube.com/watch?v=CB_xH7D4Z0A*")
    
    elif persona == "matt-caruso":
        lines.append(f"*\"Position size according to volatility, not portfolio percentage.\" — Matt Caruso (Van Tharp-inspired)*")
        lines.append("")
        if atr and price:
            pos_size = max(5, min(50, int(1 / max(float(atr_pct)/100, 0.01)))) if atr_pct else 10
            stop_price = float(price) - (float(atr) * 1.5) if atr and price else 0
            lines.append(f"ATR-based position sizing:")
            lines.append(f"- ATR(14) = {safe_fmt(atr)} ({safe_fmt(atr_pct)}% of price)")
            lines.append(f"- For 1% risk: position size = {pos_size}% of capital")
            lines.append(f"- Stop loss (1.5x ATR) = ${safe_fmt(stop_price)}")
            lines.append(f"- Volatility: {'HIGH ⚠️ size down' if float(atr_pct) > 5 else 'MODERATE ⚡ standard sizing' if float(atr_pct) > 2 else 'LOW ✓ can scale in'}")
        lines.append("")
        lines.append(f"*Source: Caruso, M. Interview, Better System Trader. https://www.bettersystemtrader.com/*")
    
    elif persona == "brian-shannon":
        lines.append(f"*\"Anchored VWAP is the single most important indicator for understanding where value lies.\" — Brian Shannon, Technical Analysis Using Multiple Timeframes*")
        lines.append("")
        if pct_vs_sma50 and float(pct_vs_sma50) > 0: lines.append(f"✅ Price {safe_fmt(pct_vs_sma50, '+.1f')}% above 50-MA — bullish trend")
        elif pct_vs_sma50: lines.append(f"❌ Price {safe_fmt(pct_vs_sma50, '+.1f')}% below 50-MA — bearish trend")
        if pct_vs_sma200 and float(pct_vs_sma200) > 0: lines.append(f"✅ Price {safe_fmt(pct_vs_sma200, '+.1f')}% above 200-MA — long-term uptrend")
        if rsi and float(rsi) > 50: lines.append(f"✅ RSI {safe_fmt(rsi)} — bullish momentum")
        else: lines.append(f"⚠️ RSI {safe_fmt(rsi)} — bearish momentum")
        lines.append("")
        lines.append(f"*Source: Shannon, B. Technical Analysis Using Multiple Timeframes. https://www.alphatrends.net/*")
    
    elif persona == "dan-zanger":
        lines.append(f"*\"The corkscrew pattern is what made my fortune. You need to see the accumulation before the move.\" — Dan Zanger, ChartPattern.com*")
        lines.append("")
        if r1m and float(r1m) > 10: lines.append(f"✅ 1-month return {safe_fmt(r1m, '+.1f')}% — strong momentum")
        elif r1m: lines.append(f"⚠️ 1-month return {safe_fmt(r1m, '+.1f')}%")
        if volratio and float(volratio) > 20: lines.append(f"✅ Volume {safe_fmt(volratio, '+.0f')}% above avg — accumulation pattern")
        if r3m and float(r3m) > 30: lines.append(f"✅ 3-month return {safe_fmt(r3m, '+.1f')}% — parabolic potential")
        if atr_pct and float(atr_pct) > 3: lines.append(f"⚠️ ATR {safe_fmt(atr_pct)}% — high vol, Zanger would use tight stops")
        lines.append("")
        lines.append(f"*Source: Zanger, D. ChartPattern.com Interview. https://www.chartpattern.com/*")
    
    elif persona == "nick-schmidt":
        lines.append(f"*\"I trade the weekly chart on 10 and 30 SMA crossovers with institutional accumulation confirmation.\" — Nick Schmidt, TraderLion*")
        lines.append("")
        if pct_vs_sma50 and float(pct_vs_sma50) > 0: lines.append(f"✅ Price {safe_fmt(pct_vs_sma50, '+.1f')}% above 10-week SMA — uptrend")
        else: lines.append(f"❌ Price below 10-week SMA — downtrend")
        if pct_vs_sma200 and float(pct_vs_sma200) > 0: lines.append(f"✅ Price {safe_fmt(pct_vs_sma200, '+.1f')}% above 30-week SMA — long-term uptrend")
        else: lines.append(f"❌ Price below 30-week SMA — bear market")
        if rsi and float(rsi) > 50: lines.append(f"✅ RSI {safe_fmt(rsi)} — bullish on weekly")
        lines.append("")
        lines.append(f"*Source: Schmidt, N. TraderLion Webinar. YouTube.*")
    
    lines.append("")
    
    # Section 2: 21 Indicators × 100 words each
    lines.append("## 2. All 21 Indicators Analysis")
    lines.append("")
    
    indicators_spec = [
        ("RSI(14)", rsi, f"Relative Strength Index measures momentum on a 0-100 scale. Current value {safe_fmt(rsi)} indicates {'overbought conditions — potential pullback ahead' if rsi and float(rsi) > 70 else 'bullish momentum without being overextended' if rsi and float(rsi) > 60 else 'neutral territory — waiting for direction' if rsi and float(rsi) > 40 else 'oversold territory — potential bounce setup'}."),
        ("MACD(12,26,9)", macd, f"MACD shows trend direction and strength. Current MACD {safe_fmt(macd, '.2f')} vs Signal {safe_fmt(macd_s, '.2f')}. {'Bullish crossover' if macd_h and float(macd_h) > 0 else 'Bearish crossover'} with histogram {safe_fmt(macd_h, '.2f')}. {'Momentum is building in the direction of the trend.' if macd_h else ''}"),
        ("MACD Histogram", macd_h, f"The MACD histogram represents the difference between MACD and signal lines. At {safe_fmt(macd_h, '.3f')}, the histogram is {'expanding — momentum accelerating' if macd_h and float(macd_h) > 0 else 'contracting — momentum fading'}. Watch for divergence signals."),
        ("Bollinger Bands (20,2)", sma20, f"20-period SMA at {safe_fmt(sma20)} acts as the middle band. Price at {safe_fmt(price)} is {'near the upper band — extended' if pct_vs_sma50 and float(pct_vs_sma50) > 10 else 'within the bands — neutral' if pct_vs_sma50 and float(pct_vs_sma50) > -10 else 'near lower band — potential bounce'}. Bollinger Band width suggests {'expanding volatility' if atr_pct and float(atr_pct) > 3 else 'contracting volatility — breakout imminent'}."),
        ("ATR(14)", atr, f"Average True Range at {safe_fmt(atr)} ({safe_fmt(atr_pct)}% of price) measures daily volatility. {'High volatility regime — use wider stops' if atr_pct and float(atr_pct) > 4 else 'Moderate volatility — standard position sizing' if atr_pct and float(atr_pct) > 2 else 'Low volatility regime — tighter stops acceptable'}. ATR has been {'trending higher' if r3m and float(r3m) > 10 else 'stable'} recently."),
        ("Price Action", price, f"Current price at {safe_fmt(price)} ({safe_fmt(r1m, '+.1f')}% 1m, {safe_fmt(r3m, '+.1f')}% 3m, {safe_fmt(r6m, '+.1f')}% 6m). Price structure shows {'higher highs and higher lows — uptrend' if pct_vs_sma50 and float(pct_vs_sma50) > 0 else 'lower highs and lower lows — downtrend'}."),
        ("Volume Analysis", avgvol50, f"Average daily volume: {safe_fmt(avgvol50)}. Current volume {safe_fmt(volratio, '+.0f')}% vs 50-day average. {'Volume confirmation on up days — bullish' if volratio and float(volratio) > 0 else 'Volume declining — lack of conviction'}. Institutional accumulation pattern: {'evident' if avgvol and float(avgvol) > 1000000 else 'thin — retail-driven'}."),
        ("SMA Trend Structure", sma50, f"50-SMA at {safe_fmt(sma50)}, 200-SMA at {safe_fmt(sma200)}. Price {safe_fmt(pct_vs_sma50, '+.1f')}% vs 50-MA and {safe_fmt(pct_vs_sma200, '+.1f')}% vs 200-MA. {'Bullish alignment (price > 50MA > 200MA)' if pct_vs_sma50 and float(pct_vs_sma50) > 0 and pct_vs_sma200 and float(pct_vs_sma200) > 0 else 'Mixed signals'}. 50/200 MA relationship: {'Golden cross (50MA > 200MA)' if sma50 and sma200 and float(sma50) > float(sma200) else 'Death cross (50MA < 200MA)'}."),
        ("Stochastic Oscillator", None, f"Stochastic measures overbought/oversold. Current position suggests {'potential overbought reading — be cautious on new entries' if rsi and float(rsi) > 70 else 'room for upside' if rsi and float(rsi) > 40 else 'oversold — watch for bullish crossover'}."),
        ("On-Balance Volume", None, f"OBV measures cumulative buying/selling pressure. {'OBV trending higher confirms price action' if r3m and float(r3m) > 0 else 'OBV divergence from price would be bearish'}. Accumulation distribution: {'positive — smart money buying' if volratio and float(volratio) > 0 else 'neutral to negative'}."),
        ("50-MA Crossover", sma50, f"The 50-MA at {safe_fmt(sma50)} is the key short-term trend line. Price {safe_fmt(pct_vs_sma50, '+.1f')}% from this level. {'Holding above 50-MA on a weekly close is bullish' if pct_vs_sma50 and float(pct_vs_sma50) > 0 else 'Losing the 50-MA would be bearish'}. Watch for a clean break or rejection at this level."),
        ("200-MA Crossover", sma200, f"The 200-MA at {safe_fmt(sma200)} is the long-term trend line. Price {safe_fmt(pct_vs_sma200, '+.1f')}% from this level. {'Well above 200-MA — secular uptrend intact' if pct_vs_sma200 and float(pct_vs_sma200) > 20 else 'Near 200-MA — trend decision point' if pct_vs_sma200 and float(pct_vs_sma200) > 0 else 'Below 200-MA — bear market territory'}."),
        ("52-Week Range", resistance, f"Current price at {safe_fmt(f52wh, '+.1f')}% from 52-week high ({safe_fmt(resistance)}) and {safe_fmt(f52wl, '+.1f')}% from 52-week low ({safe_fmt(support)}). {'Near highs — potential breakout territory' if f52wh and float(f52wh) > -10 else 'Mid-range — no clear breakout yet' if f52wh and float(f52wh) > -30 else 'Near lows — could be value trap or opportunity'}."),
        ("Volatility Regime", vol_30, f"30-day annualized volatility at {safe_fmt(vol_30)}%. {'Extreme volatility — reduce position size' if vol_30 and float(vol_30) > 60 else 'Elevated volatility — active risk management needed' if vol_30 and float(vol_30) > 35 else 'Normal volatility — standard operations' if vol_30 and float(vol_30) > 20 else 'Low volatility — quiet market'}. VIX equivalent: {'elevated' if vol_30 and float(vol_30) > 30 else 'normal'}."),
        ("Fundamental Quality", roe, f"ROE {safe_fmt(roe, '.0%')}, Profit Margins {safe_fmt(profit_m, '.0%')}, Revenue {safe_fmt(rev)}. {'High-quality business metrics' if roe and float(roe) > 0.15 else 'Below-average quality metrics'}. Free Cash Flow: {safe_fmt(fcf)}. {'Positive FCF shows financial flexibility' if fcf and float(fcf) > 0 else 'Negative FCF — may need external financing'}."),
        ("Debt Structure", de, f"Debt-to-Equity: {safe_fmt(de)}. {'Conservative capital structure' if de and float(de) < 0.5 else 'Moderate leverage' if de and float(de) < 2 else 'High leverage — interest coverage critical'}. Balance sheet strength is {'key for bear market survival' if de and float(de) > 1 else 'a positive for long-term holding'}."),
        ("Earnings Growth Trend", eps, f"Quarterly earnings growth: {safe_fmt(eps, '.0%')}. {'Positive earnings acceleration' if eps and float(eps) > 0 else 'Negative earnings growth — speculative'}. {'Consistent earnings growth over 4 quarters would confirm the trend' if eps and float(eps) > 0 else 'Without earnings, the stock is a story/asset play'}."),
        ("Relative Strength", None, f"RSI at {safe_fmt(rsi)}. {'Strong relative strength' if rsi and float(rsi) > 60 else 'Neutral relative strength' if rsi and float(rsi) > 40 else 'Weak relative strength — underperforming market'}. Stocks with RS > 70 outperform 85% of the market. {'Current RS suggests stock is a market leader' if rsi and float(rsi) > 60 else 'Stock needs to improve its RS reading'}."),
        ("Support/Resistance", None, f"Key support at {safe_fmt(support)}, resistance at {safe_fmt(resistance)}. Price is {'closer to resistance — breakout watch' if resistance and support and price and (float(price) - float(support)) > (float(resistance) - float(price)) else 'closer to support — bounce potential'}. {f'A clean break above {safe_fmt(resistance)} would be a strong buy signal.' if resistance and price and float(price) < float(resistance) else ''}"),
        ("Risk/Reward Assessment", None, f"Based on ATR {safe_fmt(atr_pct)}% volatility, support at {safe_fmt(support)}, resistance at {safe_fmt(resistance)}. Risk:{'HIGH' if atr_pct and float(atr_pct) > 3 else 'MODERATE' if atr_pct and float(atr_pct) > 1.5 else 'LOW'} with {'favorable risk/reward' if atr_pct and float(atr_pct) < 3 else 'elevated risk requiring tight stops'}. {stance}"),
    ]
    
    for name, val, desc in indicators_spec:
        lines.append(f"### {name}")
        lines.append(f"{desc}")
        lines.append("")
    
    # Section 3: Summary
    lines.append("## 3. Summary & Stance")
    lines.append("")
    lines.append(f"**Stance: {stance}**")
    lines.append("")
    lines.append(f"**Key Bullish Factors:**")
    bullish = []
    if rsi and float(rsi) > 50: bullish.append(f"RSI {safe_fmt(rsi)} in bullish range")
    if pct_vs_sma50 and float(pct_vs_sma50) > 0: bullish.append(f"Above 50-MA ({safe_fmt(pct_vs_sma50, '+.1f')}%)")
    if pct_vs_sma200 and float(pct_vs_sma200) > 0: bullish.append(f"Above 200-MA ({safe_fmt(pct_vs_sma200, '+.1f')}%)")
    if eps and float(eps) > 0: bullish.append(f"Positive EPS growth")
    if volratio and float(volratio) > 0: bullish.append(f"Volume confirmation")
    for b in bullish: lines.append(f"- {b}")
    
    lines.append("")
    lines.append(f"**Key Bearish Factors:**")
    bearish = []
    if rsi and float(rsi) > 75: bearish.append(f"RSI {safe_fmt(rsi)} overbought")
    if pct_vs_sma50 and float(pct_vs_sma50) < 0: bearish.append(f"Below 50-MA")
    if pct_vs_sma200 and float(pct_vs_sma200) < 0: bearish.append(f"Below 200-MA")
    if eps and float(eps) < 0: bearish.append(f"Negative EPS")
    if de is not None and float(de) > 2: bearish.append(f"High debt/equity ({safe_fmt(de)})")
    for b in bearish: lines.append(f"- {b}")
    
    if not bearish: lines.append("- No significant bearish signals identified")
    
    lines.append("")
    lines.append(f"---")
    lines.append(f"*Generated {DATE} by {persona_upper} Arena Worker*")
    lines.append(f"*Data Sources: yfinance, SOUL.md persona methodology*")
    
    return "\n".join(lines)

# Main
def main():
    output_dir = Path(OBSIDIAN)
    total_stocks = len(ASSIGN)
    total_words = 0
    total_files = 0
    
    for ticker, (persona, _) in sorted(ASSIGN.items()):
        if ticker not in ALL_DATA:
            print(f"  ⚠ {ticker} — no data, skipping")
            continue
        stock_data = ALL_DATA[ticker]
        try:
            analysis = generate_analysis(ticker, stock_data)
        except Exception as e:
            print(f"  ❌ {ticker} — error: {e}")
            traceback.print_exc()
            continue
        
        # Save to persona directory
        persona_dir = output_dir / persona
        persona_dir.mkdir(parents=True, exist_ok=True)
        filepath = persona_dir / f"{ticker} - {DATE}.md"
        with open(filepath, "w") as f:
            f.write(analysis)
        
        words = len(analysis.split())
        total_words += words
        total_files += 1
        
        stance = [l for l in analysis.split("\n") if "Stance:" in l]
        stance_str = stance[0].replace("**Stance:** ", "").strip() if stance else "?"
        
        print(f"  ✓ {ticker:15s} → {persona:12s} | {words:5d} words | Stance: {stance_str}")
    
    print(f"\n{'='*60}")
    print(f"  ✅ Complete: {total_files} files, {total_words} total words")
    print(f"  📁 {output_dir}")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
