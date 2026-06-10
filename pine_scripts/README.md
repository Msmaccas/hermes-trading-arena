# Per-Persona Pine Script Indicator Packs

6 standalone Pine Script v5 indicators, one per trading persona.  
Each pack adds that persona's specific indicator set + a data table to the chart.

## Loading via MCP

Use `chart_manage_indicator("add", "<Full Name>")` from the Jackson MCP:

| File | Full Name (load via MCP) | Description |
|------|--------------------------|-------------|
| `oneil.pine` | `ONeil CANSLIM Pack` | EMA20/SMA50/SMA200, RSI(14), RS Line, volume%, dist days, pivot level, CANSLIM score table |
| `minervini.pine` | `Minervini VCP/SEPA Pack` | EMA10/20/SMA50, RSI(14), ATR with VCP tightening ratio, shakeout detection, RS Line |
| `qullamaggie.pine` | `Qullamaggie Momentum/EP Pack` | EMA10/20, RSI(14), ADR%, gap detection, fib extension levels (1.618/2.618/4.236) |
| `lynch.pine` | `Lynch GARP Pack` | SMA50/200, P/E & PEG table, EPS growth, Lynch category classification |
| `buffet.pine` | `Buffett Value Pack` | SMA50/200, P/E/P/B/ROE/D/E/div yield, Moat Score composite |
| `david-ryan.pine` | `David Ryan CANSLIM+ Pack` | O'Neil features + 85-85 score, tight close detection, power from pivot, pyramiding levels |

## Loading via TradingView UI

1. Open TradingView → Pine Editor (bottom panel)
2. Open the file from `~/hermes_home/pine_scripts/<name>.pine`
3. Click "Add to Chart"

Or use the MCP:
```
chart_manage_indicator("add", "ONeil CANSLIM Pack")
```

## How MCP Reads the Table Data

After adding an indicator, call:
```
data_get_study_values()          → All plot values (RSI, EMAs, etc.)
data_get_pine_tables()           → Table cell contents from table.new()
data_get_pine_labels()           → Any label.new() annotations on chart
```

The tables use `table.new()` with `position.bottom_right` location so they appear on the chart and are readable by MCP.

## Global Market Support

All indicators work on any symbol TradingView supports:
- `NASDAQ:AAPL`, `NYSE:BRK.B`
- `HKEX:00700`, `SSE:600519`, `TSE:9984`
- `NSE:RELIANCE`, `LSE:BP`, `BMFBOVESPA:PETR4`

Just switch the chart symbol via MCP and the indicators auto-adjust.
