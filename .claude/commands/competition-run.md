---
description: >
  Run all 10 trading personas on a stock or sector. Each persona loads from .claude/agents/,
  independently analyzes the same target using their own methodology, then results are collected
  into a comparison table. Personas are ranked by accuracy potential, and the bottom 2 are
  flagged for elimination. Requires global market coverage, not just US.
allowed-tools:
  - yfinance
  - mcp_tradingview_*
  - competition_engine
disable-model-invocation: false
help: true
prompt: |
  Stock/sector to run the competition on (e.g., "AAPL", "NVDA", "Semiconductor ETF", "TSLA"):
---

# /competition-run — Full Persona Competition Engine

## When to run
- You need a multi-perspective analysis on a promising idea
- You want to see which persona methodologies converge or diverge on a name
- You're running the monthly/quarterly competition cycle and need to update elimination candidates
- You need to identify which analyst methodologies have been consistently wrong/right

## Workflow

### 1. Load All 10 Personas

Read each persona file from `.claude/agents/`:

| #  | Persona        | Methodology                 | File              |
|----|----------------|-----------------------------|-------------------|
| 1  | William O'Neil | CAN SLIM                    | oneil.md          |
| 2  | Warren Buffett | Value/Moat                  | buffett.md        |
| 3  | Peter Lynch    | PEG/GARP                    | lynch.md          |
| 4  | Mark Minervini | SEPA/VCP                    | minervini.md      |
| 5  | Kristjan Kull  | Momentum/Breakout           | qullamaggie.md    |
| 6  | David Ryan     | Growth at Reasonable Price  | david-ryan.md     |
| 7  | Matt Caruso    | Technical + Fundamental     | matt-caruso.md    |
| 8  | Brian Shannon  | VWAP/Position Sizing        | brian-shannon.md  |
| 9  | Dan Zanger     | Technical Breakout          | dan-zanger.md     |
| 10 | Nick Schmidt   | Quantitative Factor         | nick-schmidt.md   |

### 2. Pull Global Market Data

For the target stock/sector, gather data across **at least** these markets:
- **US** (NYSE/NASDAQ) — primary
- **Hong Kong** (HKEX)
- **Japan** (TSE)
- **South Korea** (KRX)
- **India** (NSE/BSE)
- **UK** (LSE)
- **Brazil** (B3)
- **EU** (Xetra, Euronext, SIX)

For each market, collect:
- Price, volume, market cap (in local currency + USD)
- Sector/industry context in that market
- ADR/GDR premium/discount if applicable
- Local GAAP/IFRS accounting standard flag

### 3. Run Each Persona Analysis

For each persona (in order), execute their methodology on the target stock:

- **O'Neil**: CAN SLIM scoring (C, A, N, S, L, I, M factors); IBD-style chart analysis; look for RS line, Accumulation/Distribution rating
- **Buffett**: Owner earnings calculation; moat durability assessment; competitive advantage period; discount to intrinsic value; management quality score
- **Lynch**: PEG ratio decomposition (PEG vs growth rate); P/E relative to 5-year range; story classification (slow-grower, stalwart, fast-grower, cyclical, turn-around, asset play); balance sheet checklist
- **Minervini**: VCP contraction counting (tightness calculation); relative strength ranking; SEPA criteria checklist; specific pivot point identification with exact price
- **Qullamaggie**: Multi-timeframe momentum analysis; relative strength to SPY/QQQ; breakouts from proper base structures; volume confirmation requirements
- **David Ryan**: EPS growth + sales growth + ROE scoring; chart base pattern recognition (cup, saucer, double bottom); buy zone identification
- **Matt Caruso**: Fundamental screens (EPS, sales, margins, ROE) + technical entry timing; stop-loss placement; position sizing based on volatility
- **Brian Shannon**: VWAP analysis (price relative to VWAP anchors); trend structure (higher highs/lows); position sizing using ATR; risk management first
- **Dan Zanger**: Chart pattern identification (flags, pennants, wedges, double/triple bottoms); price/volume divergence detection; exact entry price levels
- **Nick Schmidt**: Quantitative factor model scoring (momentum, value, quality, low volatility, size); factor z-score aggregation; rank within industry/sector

Each persona must produce a structured output with:
- **Score/Rating**: [Strong Buy / Buy / Hold / Avoid / Sell]
- **Confidence**: [High / Medium / Low]
- **Key Thesis**: 1-3 sentence summary of their rationale
- **Bear Case Flag**: The single biggest risk their methodology identifies
- **Stop-Loss/Entry**: Specific price levels (where applicable)
- **Position Size Guidance**: As % of portfolio (where applicable)

### 4. Build Comparison Table

```
## Competition Results — [TICKER] — [Date]

| Rank | Persona       | Methodology        | Rating      | Confidence | Key Insight | Bear Case Flag         |
|------|---------------|--------------------|-------------|------------|-------------|------------------------|
| 1    | [Name]        | [Method]           | [Rating]    | [Conf]     | [Insight]   | [Risk]                 |
| 2    | [Name]        | [Method]           | [Rating]    | [Conf]     | [Insight]   | [Risk]                 |
| ...  | ...           | ...                | ...         | ...        | ...         | ...                    |
| 9    | 🔴 [Name]     | [Method]           | [Rating]    | [Conf]     | [Insight]   | [Risk]                 |
| 10   | 🔴 [Name]     | [Method]           | [Rating]    | [Conf]     | [Insight]   | [Risk]                 |
```

### 5. Rank by Accuracy Potential

Ranking criteria (in order of importance):
1. **Methodology fit** — does the persona's methodology match the current market regime? (e.g., O'Neil in trending bull, Buffett in value rotation)
2. **Historical accuracy on this sector/type** — persona's track record on similar set-ups
3. **Certainty level** — high conviction, specific entry/exit levels, quantified thesis
4. **Consistency with macro context** — does their thesis align with current interest rate regime, sector rotation, and global liquidity conditions?

### 6. Flag Bottom 2 for Elimination

The bottom 2 persona slots get a `🔴` marker. For each flagged persona, provide:
- **Why they ranked lowest**: Specific methodology mismatch with this stock/sector
- **Pattern of failures**: List past 3 competition rounds where they ranked bottom 3
- **Recommendation**: "Eliminate from next round" or "Give one more chance with [specific stock type]"
- **Suggested replacement methodology**: What type of trader/investor would fill the gap better

Update `.claude/memories/consensus.md` with the elimination data.

### 7. Output the Full Report

The final output must include:
1. Macro context paragraph (current market regime, sector rotation, key rates)
2. Full comparison table (from step 4)
3. For each persona: 2-3 sentence analysis summary
4. Elimination section with specific rationale for bottom 2
5. **Convergence/Divergence Analysis**: Where do personas agree? Where do they disagree most sharply?
6. **Recommended Action**: If a majority of top-5 ranked personas agree on direction, that is the competition's recommendation

### 8. Save to Archive

- Save competition results to `.claude/memories/competitions/YYYY-MM-DD-ticker.md`
- Update cross-session consensus tracker at `.claude/memories/consensus.md`
