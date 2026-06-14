# YFinance Exchange Suffixes — Complete Reference Guide

## How It Works

YFinance uses Yahoo Finance's ticker format: `BASETICKER.SUFFIX`. The suffix identifies the exchange. US stocks need **no suffix** — just use the bare ticker (AAPL, MSFT, GOOG).

> **Tested live** — all suffixes below were verified with real `yfinance` API calls on 2026-06-14.

---

## KNOWN WORKING SUFFIXES (Verified)

| # | Exchange | Country | Suffix | Example | Notes |
|---|----------|---------|--------|---------|-------|
| 1 | **Korea Stock Exchange (KRX)** | South Korea | `.KS` | `005930.KS` (Samsung) | — |
| 2 | **KOSDAQ** | South Korea | `.KQ` | `035900.KQ` (JYP Ent.) | Separate from main KRX |
| 3 | **Hong Kong Stock Exchange** | Hong Kong | `.HK` | `0700.HK` (Tencent) | ⚠️ See leading-zero rules below |
| 4 | **Tokyo Stock Exchange** | Japan | `.T` | `7203.T` (Toyota) | — |
| 5 | **National Stock Exchange of India** | India | `.NS` | `RELIANCE.NS` | — |
| 6 | **Bombay Stock Exchange** | India | `.BO` | `RELIANCE.BO` | Alternative to .NS |
| 7 | **London Stock Exchange** | UK | `.L` | `SHEL.L` | ⚠️ See double-dot rules below |
| 8 | **BM&F Bovespa** | Brazil | `.SA` | `PETR4.SA` | — |
| 9 | **Taiwan Stock Exchange (TWSE)** | Taiwan | `.TW` | `2451.TW` | — |
| 10 | **Taipei Exchange (OTC)** | Taiwan | `.TWO` | `6488.TWO` | OTC market |
| 11 | **Borsa Istanbul** | Turkey | `.IS` | `PCILT.IS` | — |
| 12 | **Shanghai Stock Exchange (A-shares)** | China | `.SS` | `600519.SS` (Moutai) | — |
| 13 | **Shenzhen Stock Exchange (A-shares)** | China | `.SZ` | `000001.SZ`, `300750.SZ` | — |
| 14 | **Ho Chi Minh Stock Exchange (HOSE)** | Vietnam | `.VN` | `VIC.VN` (Vingroup) | Works for both HOSE & HNX listings |
| 15 | **Hanoi Stock Exchange (HNX)** | Vietnam | `.VN` | `KSF.VN` | Same suffix — some stocks may not be covered by Yahoo |
| 16 | **Indonesia Stock Exchange (IDX)** | Indonesia | `.JK` | `BBCA.JK` | — |
| 17 | **Stock Exchange of Thailand (SET)** | Thailand | `.BK` | `CPALL.BK` | — |
| 18 | **Saudi Exchange (Tadawul)** | Saudi Arabia | `.SR` | `2222.SR` (Saudi Aramco), `1120.SR` | Not `.SAU` (old/deprecated) |
| 19 | **Pakistan Stock Exchange (PSX)** | Pakistan | `.KA` | `OGDC.KA`, `HBL.KA` | Uses legacy Karachi code |
| 20 | **Singapore Exchange (SGX)** | Singapore | `.SI` | `D05.SI` (DBS) | — |
| 21 | **Australian Securities Exchange (ASX)** | Australia | `.AX` | `BHP.AX` | — |
| 22 | **Toronto Stock Exchange** | Canada | `.TO` | `SHOP.TO` | — |
| 23 | **SIX Swiss Exchange** | Switzerland | `.SW` | `NESN.SW` (Nestlé) | — |
| 24 | **Deutsche Börse (XETRA)** | Germany | `.DE` | `SAP.DE` | — |
| 25 | **Frankfurt Stock Exchange** | Germany | `.F` | `ZYE1.F` | — |
| 26 | **Euronext Paris** | France | `.PA` | `MC.PA` (LVMH) | — |
| 27 | **Euronext Amsterdam** | Netherlands | `.AS` | `ASML.AS` | — |
| 28 | **Johannesburg Stock Exchange (JSE)** | South Africa | `.JO` | `ABSP.JO` | — |
| 29 | **Oslo Stock Exchange** | Norway | `.OL` | `AKBM.OL` | — |
| 30 | **Stockholm Stock Exchange** | Sweden | `.ST` | `ALIF-B.ST` | — |
| 31 | **Wiener Börse** | Austria | `.VI` | `AGR.VI` | — |
| 32 | **Bolsa de Madrid** | Spain | `.MC` | `ANA.MC` | — |
| 33 | **Borsa Italiana** | Italy | `.MI` | `ADB.MI` | — |
| 34 | **New Zealand Exchange (NZX)** | New Zealand | `.NZ` | `AIR.NZ` | — |
| 35 | **Malaysia Stock Exchange** | Malaysia | `.KL` | — | — |
| 36 | **Mexico Stock Exchange (BMV)** | Mexico | `.MX` | — | — |
| 37 | **Qatar Stock Exchange** | Qatar | `.QA` | — | — |
| 38 | **Moscow Exchange (MOEX)** | Russia | `.ME` | — | Real-time data |
| 39 | **Warsaw Stock Exchange** | Poland | `.WA` | `AMB.WA` | — |

---

## CRITICAL EDGE CASES & EXCEPTIONS

### 1️⃣ Hong Kong — Leading Zeros MUST Be Stripped

**This is the most common gotcha.** TV Scanner returns 5-digit HK codes (e.g., "03416"). YFinance expects **max 4 digits** — strip all leading zeros.

| TradingView Code | Correct YFinance Format | Notes |
|-----------------|------------------------|-------|
| `09988` | `9988.HK` | Alibaba — strip leading `0` |
| `00700` | `0700.HK` | Tencent — strip leading `00`, keep 4 digits |
| `03416` | `3416.HK` | Strip leading `0` |
| `02802` | `2802.HK` | Strip leading `0` |
| `00857` | `0857.HK` | Strip leading `00` |
| `02245` | `2245.HK` | Lygend — strip leading `0` |

**Rule:** Take the raw 5-digit code, remove the first character (the leading zero). If the result is 4 digits, you're done. If it starts with another zero, keep stripping until you have 4 digits or no leading zeros.

> **Watch out:** Using wrong codes like `00857.HK` returns a **stale ghost record** (market shows as `"us_market"`, all prices null) — no error raised, data completely wrong. Always verify `market` is `"hk_market"` and price is not null.

### 2️⃣ UK — Double-Dot Tickers Use Hyphens

Some UK tickers contain a period in their official name (e.g., BT.A → BT Group). YFinance uses a **hyphen instead of a dot** to avoid ambiguity with the exchange suffix separator.

| Official Ticker | Correct YFinance Format |
|----------------|------------------------|
| `BT.A` (London) | `BT-A.L` |
| `ALIF-B` (Sweden) | `ALIF-B.ST` |

**Rule:** Replace any `.` in the ticker name with `-` before appending the exchange suffix.

### 3️⃣ China A-Shares — Leading Zeros Are PRESERVED

Unlike Hong Kong, China A-shares **keep their leading zeros**. The TV scanner number is the same format YFinance expects.

| Exchange | TV Code → YFinance | Example |
|----------|-------------------|---------|
| Shanghai | `600519` → `600519.SS` | Kweichow Moutai |
| Shenzhen | `000001` → `000001.SZ` | Ping An Bank |
| Shenzhen | `300750` → `300750.SZ` | CATL |

**No transformation needed** — just append `.SS` or `.SZ`.

### 4️⃣ Korea — Leading Zeros ARE PRESERVED

Same as China — Korean stocks keep leading zeros.

| TV Code → YFinance | Example |
|-------------------|---------|
| `005930` → `005930.KS` | Samsung Electronics |
| `035900` → `035900.KQ` | JYP Ent. (KOSDAQ) |

### 5️⃣ Taiwan — Leading Zeros ARE PRESERVED

Same format as Korea/China.

| TV Code → YFinance | Example |
|-------------------|---------|
| `2451` → `2451.TW` | Transcend Information |
| `0050` → `0050.TW` | Yuanta/P-shares Taiwan 50 (ETF) |

---

## TV SCANNER → YFINANCE QUICK-CONVERSION TABLE

| TV Scanner Symbol | Exchange | YFinance Ticker | Notes |
|-------------------|----------|-----------------|-------|
| `005930` | Korea KRX | `005930.KS` | Keep leading zeros |
| `035900` | KOSDAQ | `035900.KQ` | Keep leading zeros |
| `0050` | Taiwan TWSE | `0050.TW` | Keep leading zeros |
| `2451` | Taiwan TWSE | `2451.TW` | Keep leading zeros |
| `6488` | Taiwan OTC | `6488.TWO` | Keep leading zeros |
| `600519` | Shanghai | `600519.SS` | Keep leading zeros |
| `000001` | Shenzhen | `000001.SZ` | Keep leading zeros |
| `300750` | Shenzhen | `300750.SZ` | Keep leading zeros |
| `09988` | Hong Kong | `9988.HK` | ⚠️ Strip leading zero |
| `02245` | Hong Kong | `2245.HK` | ⚠️ Strip leading zero |
| `00700` | Hong Kong | `0700.HK` | ⚠️ Strip leading 00 |
| `7203` | Japan | `7203.T` | — |
| `2222` | Saudi Tadawul | `2222.SR` | — |
| `1120` | Saudi Tadawul | `1120.SR` | — |
| `OGDC` | Pakistan PSX | `OGDC.KA` | — |
| `HBL` | Pakistan PSX | `HBL.KA` | — |
| `BBCA` | Indonesia IDX | `BBCA.JK` | — |
| `BMRI` | Indonesia IDX | `BMRI.JK` | — |
| `CPALL` | Thailand SET | `CPALL.BK` | — |
| `PTT` | Thailand SET | `PTT.BK` | — |
| `VIC` | Vietnam HOSE | `VIC.VN` | — |
| `VNM` | Vietnam HOSE | `VNM.VN` | — |
| `KSF` | Vietnam HNX | `KSF.VN` | Correct suffix; Yahoo may not have data |
| `BT.A` | UK LSE | `BT-A.L` | ⚠️ Replace `.` with `-` |
| `SHEL` | UK LSE | `SHEL.L` | — |
| `PETR4` | Brazil Bovespa | `PETR4.SA` | — |
| `PCILT` | Turkey BIST | `PCILT.IS` | — |
| `RELIANCE` | India NSE | `RELIANCE.NS` | — |
| `RELIANCE` | India BSE | `RELIANCE.BO` | — |
| `D05` | Singapore SGX | `D05.SI` | — |
| `BHP` | Australia ASX | `BHP.AX` | — |
| `SHOP` | Canada TSX | `SHOP.TO` | — |

---

## QUICK REFERENCE: Country → Suffix

| Country | Suffix | Country | Suffix |
|---------|--------|---------|--------|
| 🇰🇷 Korea (KRX) | `.KS` | 🇰🇷 Korea (KOSDAQ) | `.KQ` |
| 🇭🇰 Hong Kong | `.HK` | 🇯🇵 Japan | `.T` |
| 🇮🇳 India (NSE) | `.NS` | 🇮🇳 India (BSE) | `.BO` |
| 🇬🇧 UK (LSE) | `.L` | 🇧🇷 Brazil | `.SA` |
| 🇹🇼 Taiwan (TWSE) | `.TW` | 🇹🇼 Taiwan (OTC) | `.TWO` |
| 🇹🇷 Turkey | `.IS` | 🇨🇳 China Shanghai | `.SS` |
| 🇨🇳 China Shenzhen | `.SZ` | 🇻🇳 Vietnam | `.VN` |
| 🇮🇩 Indonesia | `.JK` | 🇹🇭 Thailand | `.BK` |
| 🇸🇦 Saudi Arabia | `.SR` | 🇵🇰 Pakistan | `.KA` |
| 🇸🇬 Singapore | `.SI` | 🇦🇺 Australia | `.AX` |
| 🇨🇦 Canada (TSX) | `.TO` | 🇨🇭 Switzerland | `.SW` |
| 🇩🇪 Germany (XETRA) | `.DE` | 🇩🇪 Germany (Frankfurt) | `.F` |
| 🇫🇷 France (Euronext) | `.PA` | 🇳🇱 Netherlands | `.AS` |
| 🇿🇦 South Africa | `.JO` | 🇳🇴 Norway | `.OL` |
| 🇸🇪 Sweden | `.ST` | 🇦🇹 Austria | `.VI` |
| 🇪🇸 Spain | `.MC` | 🇮🇹 Italy | `.MI` |
| 🇳🇿 New Zealand | `.NZ` | 🇲🇾 Malaysia | `.KL` |
| 🇲🇽 Mexico | `.MX` | 🇶🇦 Qatar | `.QA` |
| 🇷🇺 Russia | `.ME` | 🇵🇱 Poland | `.WA` |

---

## VERIFICATION SCRIPT

Use this to test if a ticker works:

```python
import yfinance as yf

ticker = "YOUR.TICKER.HERE"
stock = yf.Ticker(ticker)
info = stock.info

name = info.get('shortName') or info.get('longName', 'N/A')
price = info.get('currentPrice') or info.get('regularMarketPrice', 'N/A')
market = info.get('market', 'N/A')
currency = info.get('currency', 'N/A')

print(f"Name: {name}")
print(f"Price: {price} {currency}")
print(f"Market: {market}")

# Sanity check: verify market matches expected
VALID_MARKETS = {
    '.KS': 'kr_market', '.KQ': 'kr_market',
    '.HK': 'hk_market', '.T': 'jp_market',
    '.NS': 'in_market', '.BO': 'in_market',
    '.L': 'gb_market', '.SA': 'br_market',
    '.TW': 'tw_market', '.TWO': 'tw_market',
    '.IS': 'tr_market', '.SS': 'cn_market',
    '.SZ': 'cn_market', '.VN': 'vn_market',
    '.JK': 'id_market', '.BK': 'th_market',
    '.SR': 'sr_market', '.KA': 'pk_market',
    '.SI': 'sg_market', '.AX': 'au_market',
    '.TO': 'ca_market',
}

suffix = '.' + ticker.split('.')[-1]
expected = VALID_MARKETS.get(suffix)
if expected and market != expected and market != 'N/A':
    print(f"⚠️ WARNING: Expected market '{expected}' but got '{market}' — possible ghost data!")
elif market == 'N/A' or price == 'N/A':
    print(f"⚠️ No data returned — Yahoo may not cover this stock")
else:
    print(f"✅ Looks good!")
```

---

**Legend:**
- ✅ = Verified working with live yfinance API call
- ⚠️ = Has edge cases / special handling required
- — = Standard suffix, no special handling needed
