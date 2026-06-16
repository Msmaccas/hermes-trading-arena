---
description: >
  Run deep Fidelity-report-level analysis on a single stock. Covers US + at least 3 global markets (HK, JP, KR, IN, UK, BR, EU).
  Produces institutional-grade output: business overview, financial analysis (filing-verified), competitive position,
  bear case with 3 specific risks, analyst consensus targets, valuation, and verdict. ALL metrics preserved with SEC
  filing references. Output uses Fidelity-report format: hook title, QUICK TAKE box, narrative arc.
allowed-tools:
  - yfinance
  - mcp_tradingview_*
  - competition_engine
disable-model-invocation: false
help: true
prompt: |
  What stock symbol/market do you want to analyze?
---

# /single-stock-analysis — Deep Institutional-Quality Single Stock Analysis

## When to run
- You need a full Fidelity-report-depth analysis on one stock
- You need to check if a new idea is worth entering the competition pipeline
- You need current fundamentals + technicals + bear case before making an allocation decision

## Workflow

### 1. Set the Symbol and Markets
- Accept the symbol from the user prompt.
- Determine primary exchange (NYSE, NASDAQ, HKEX, TSE, KRX, NSE/BSE, LSE, B3, Xetra).
- Identify at least 3 global markets for cross-reference:
  - **Always include**: US (primary)
  - **Pick at least 3 of**: HK (HKEX), JP (TSE), KR (KRX), IN (NSE/BSE), UK (LSE), BR (B3), EU (Xetra/Euronext/SIX)
- Check ADR/GDR wrapper suffixes and strip them for fundamental comparison.

### 2. Pull Financial Data
Use **yfinance** (or the competition engine at `~/.hermes/scripts/competition_engine.py` if available):
- Income statement (3+ fiscal years + trailing twelve months)
- Balance sheet (current assets, liabilities, debt structure, book value)
- Cash flow statement (owner earnings, free cash flow, capex trends)
- Key metrics: Revenue, Net Income, EPS (diluted), EPS growth, Operating Margin, FCF Yield, ROE, ROIC, Debt/Equity, Current Ratio, BV/Share
- Verify share count changes (dilution/buybacks over 5 years)

### 3. Verify Against Filings (No Face-Value Acceptance)
- Cross-check reported metrics against **actual filings** when possible:
  - SEC EDGAR for US stocks (10-K, 10-Q)
  - HKEX filings, Tokyo TSE filings, CDMX filings per exchange
- Flag discrepancies between press releases and regulatory filings.
- Identify one-time items, revenue recognition changes, unusual non-GAAP adjustments.
- Document share count adjustments (stock splits, buybacks, dilution events).

### 4. Business Overview
- Sector, industry, geographic revenue breakdown
- Products/services contribution to revenue (percentage)
- Business model, moat type (network effects, switching costs, cost advantage, intangible assets, efficient scale)
- Management quality assessment (tenure, insider ownership, capital allocation track record)
- Addressable market size and share

### 5. Financial Health Deep Dive (Filing-Verified)
- **Revenue trajectory**: 3-year CAGR, quarter-over-quarter acceleration/deceleration
- **Profitability**: Gross margin, operating margin, net margin trend (3 years)
- **Cash generation**: Operating CF, FCF, FCF yield, owner earnings (Buffett method: net income + D&A - maintenance capex)
- **Balance sheet strength**: Debt maturity schedule, interest coverage ratio, current ratio, quick ratio
- **Earnings quality**: Accruals ratio, revenue vs cash collection correlation
- **Capital allocation**: Buyback yield, dividend growth, M&A track record

### 6. Competitive Position
- Porter's Five Forces analysis for the market
- Market share trends (expanding or shrinking?)
- Identifiable moat with evidence (patents, pricing power, customer retention rates)
- Competitor benchmarking table (revenue, margin, growth vs 2-3 closest competitors)
- R&D spending as % of revenue vs peers

### 7. Bear Case — 3 Specific Risks (Required)
Every analysis **MUST** include a dedicated bear case with at least 3 quantified risks:

1. **Risk 1**: Specific, quantifiable (e.g., "if revenue decelerates from 15% to 8%, PEG expands to 2.5x implying 30% downside")
2. **Risk 2**: Industry/macro risk (e.g., "regulatory tail risk: FTC scrutiny on vertical integration could force divestiture of X division")
3. **Risk 3**: Company-specific risk (e.g., "CEO owns 60% of voting shares — succession risk with no clear heir apparent")

### 8. Analyst Consensus & Targets
- Bloomberg/Benzinga consensus: mean target, high target, low target
- Number of analysts, upgrades/downgrades (last 3 months)
- Verbatim bull and bear thesis excerpts from sell-side research
- Earnings surprise history (last 8 quarters)

### 9. Valuation
- P/E (trailing, forward), P/S, P/B, EV/EBITDA, EV/Sales
- PEG ratio (trailing + forward growth)
- DCF range (assumptions: WACC, terminal growth, FCF growth phases)
- Comparison to 5-year historical valuation ranges
- Comparison to sector median multiples

### 10. Technical Setup (Price + Volume)
- Current price relative to 50-day and 200-day SMA
- RSI (14), MACD status
- Volume profile: heavy accumulation/distribution days (last 20 sessions)
- Support/resistance levels with specific prices
- Chart pattern identification (cup-and-handle, VCP, flat base, ascending triangle, etc.)
- Average true range (14) for volatility context

### 11. Produce the Fidelity-Format Report

Output must use this structure:

```markdown
# 🎯 [Hook Title]: [Company Name] ([TICKER]) — [$Price]

## ⚡ QUICK TAKE

**Rating**: [Buy / Overweight / Hold / Underweight / Sell] | **Price Target**: [$target]
**Market Cap**: [$cap] | **Sector**: [sector] | **Industry**: [industry]
**S&P Rating**: [if applicable] | **IBD Composite**: [if applicable]

**The Bull Case (One Sentence)**: ...
**The Bear Case (One Sentence)**: ...
**Verdict (One Sentence)**: ...

---

## 1. Business Overview

...

## 2. Financial Health (Filing-Verified)

...

## 3. Competitive Position

...

## 4. Bear Case — 3 Specific Risks

...

## 5. Analyst Consensus

...

## 6. Valuation

...

## 7. Technical Setup

...

## 8. Verdict

**Thesis**: ...
**Catalysts (next 12 months)**: ...
**Position Size Recommendation**: [% of portfolio if applicable]
**Stop-Loss Level**: [$price]
**Reward Target**: [$price]
**Risk/Reward Ratio**: [X:1]

---

*Disclaimer: This is not financial advice. All data sourced from public SEC filings, company reports, and market data. Past performance does not guarantee future results.*
```

### 12. Save and Report
- Save the output to a dated file in the analysis archive or report directly to the user.
- Include explicit SEC filing references for any contested or critical metric.
- Verify that every metric uses the most recent 4 quarters of data.
