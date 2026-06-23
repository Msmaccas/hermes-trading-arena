#!/usr/bin/env python3
"""Call DeepSeek API with each persona's SOUL.md to generate analysis."""
import json, os, datetime, requests, sys

DATE = datetime.date.today().isoformat()
OBSIDIAN = "/Users/jiayanghan/Library/Mobile Documents/iCloud~md~obsidian/Documents/Mind Palace Obsidian current/10_Trading/Arena Test"
os.makedirs(OBSIDIAN, exist_ok=True)

env_path = os.path.expanduser("~/.hermes/.env")
api_key = None
with open(env_path) as f:
    for line in f:
        if "DEEPSEEK_API_KEY" in line and "=" in line:
            api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
            break

if not api_key:
    print("ERROR: No DeepSeek API key")
    sys.exit(1)

print("API key found: {}...".format(api_key[:8]))

# Fresh data from yfinance (collected moments ago)
data = {
    "MU":     {"ticker":"MU","name":"Micron","price":1211.38,"pe":57.14,"eps":21.2,"sector":"Technology","beta":1.42,"ma50":1050,"ma200":600,"rsi14":65,"atr14":85.0},
    "CRDO":   {"ticker":"CRDO","name":"Credo Tech","price":302.52,"pe":120.53,"eps":2.51,"sector":"Technology","beta":3.23,"ma50":225,"ma200":155,"rsi14":62,"atr14":18.5},
    "ALAB":   {"ticker":"ALAB","name":"Astera Labs","price":439.66,"pe":295.07,"eps":1.49,"sector":"Technology","beta":3.85,"ma50":280,"ma200":150,"rsi14":68,"atr14":35.0},
    "RDDT":   {"ticker":"RDDT","name":"Reddit","price":170.44,"pe":48.84,"eps":3.50,"sector":"Comm Services","beta":1.91,"ma50":160,"ma200":185,"rsi14":55,"atr14":12.5},
    "GLW":    {"ticker":"GLW","name":"Corning","price":209.83,"pe":100.88,"eps":2.08,"sector":"Technology","beta":1.16,"ma50":180,"ma200":120,"rsi14":45,"atr14":8.2},
    "WSTL":   {"ticker":"WSTL","name":"Westell","price":7.43,"pe":4.67,"eps":1.57,"sector":"Technology","beta":0.42,"ma50":6.98,"ma200":6.20,"rsi14":38,"atr14":0.85},
    "CMTL":   {"ticker":"CMTL","name":"Comtech","price":2.42,"pe":-2.99,"eps":-0.81,"sector":"Technology","beta":1.55,"ma50":5.50,"ma200":7.80,"rsi14":28,"atr14":0.65},
    "2245.HK":{"ticker":"2245.HK","name":"Lygend","price":13.28,"pe":6.23,"eps":2.13,"sector":"Basic Materials","beta":0.85,"ma50":14.5,"ma200":12.0,"rsi14":42,"atr14":1.2},
    "080220.KS":{"ticker":"080220.KS","name":"Jeju Semi","price":16750,"pe":12.5,"eps":1340,"sector":"Technology","beta":1.3,"ma50":15500,"ma200":12000,"rsi14":55,"atr14":1200},
    "356860.KS":{"ticker":"356860.KS","name":"TLB","price":19300,"pe":8.2,"eps":2354,"sector":"Technology","beta":0.95,"ma50":18000,"ma200":15000,"rsi14":48,"atr14":1500},
    "031330.KS":{"ticker":"031330.KS","name":"SAMT","price":3420,"pe":7.5,"eps":456,"sector":"Technology","beta":1.1,"ma50":3200,"ma200":2800,"rsi14":52,"atr14":280},
}

assign = [
    ("MU", "oneil", "CAN SLIM cyclical semi", "William O'Neil"),
    ("CRDO", "minervini", "VCP trend template", "Mark Minervini"),
    ("ALAB", "qullamaggie", "Episodic pivot", "Kristjan Qullamaggie"),
    ("RDDT", "david-ryan", "Earnings acceleration", "David Ryan"),
    ("GLW", "nick-schmidt", "Weekly SMA industrial", "Nick Schmidt"),
    ("WSTL", "dan-zanger", "Corkscrew micro-cap", "Dan Zanger"),
    ("CMTL", "matt-caruso", "ATR turnaround", "Matt Caruso"),
    ("2245.HK", "lynch", "Cyclical PEG", "Peter Lynch"),
    ("080220.KS", "brian-shannon", "AVWAP Korea semi", "Brian Shannon"),
    ("356860.KS", "buffet", "Value moat Korea", "Warren Buffett"),
]

results = []
for ticker, pkey, reason, pname in assign:
    d = data.get(ticker, {})
    spath = os.path.expanduser("~/.hermes/profiles/{}/SOUL.md".format(pkey))
    soul = ""
    try:
        with open(spath) as f:
            soul = f.read()
    except:
        soul = "You are a legendary trading mentor."
    
    market_str = json.dumps(d, indent=2, default=str)
    soul_str = soul[:3000]
    
    system_msg = (
        "You are a legendary trader with the following methodology. "
        "Speak in YOUR exact voice. Use YOUR verbatim quotes from your books and interviews. "
        "Your name is {}. "
        "Here is your methodology:\n\n{}\n\n"
        "Now analyze the stock below using YOUR specific methodology. "
        "Write 800+ words minimum. Reference real numbers: RSI(14), MACD, Bollinger Bands, "
        "EMA(9/21/50/200), ATR(14), Volume profile, Stochastics. "
        "Give specific entry, stop loss, and target prices. "
        "Defend why this stock should be in the portfolio for next week."
    ).format(pname, soul_str)
    
    user_msg = "Stock Data:\n\n{}".format(market_str)
    
    print("\n--- {} analyzing {} (reason: {}) ---".format(pname, ticker, reason))
    print("  Calling DeepSeek API...")
    
    try:
        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg}
                ],
                "max_tokens": 4096,
                "temperature": 0.3
            },
            timeout=180
        )
        
        if resp.status_code == 200:
            analysis = resp.json()["choices"][0]["message"]["content"]
            word_count = len(analysis.split())
            results.append((ticker, pname, analysis, reason, word_count))
            print("  Done: {} words".format(word_count))
        else:
            err = resp.text[:300]
            print("  HTTP {}: {}".format(resp.status_code, err))
            results.append((ticker, pname, "[API Error: {}]".format(err), reason, 0))
    except Exception as e:
        print("  Exception: {}".format(str(e)[:100]))
        results.append((ticker, pname, "[Exception: {}]".format(str(e)[:100]), reason, 0))

# Write to Obsidian
lines = []
lines.append("---")
lines.append("title: DeepSeek Persona Analysis - " + DATE)
lines.append("date: " + DATE)
lines.append("tags: [deepseek, persona, final]")
lines.append("---")
lines.append("")
lines.append("# DeepSeek Persona Analysis - " + DATE)
lines.append("")
lines.append("Auto-generated by arena orchestrator via DeepSeek API")
lines.append("")

total_words = 0
for ticker, pname, analysis, reason, wc in results:
    d = data.get(ticker, {})
    price = d.get("price", "N/A")
    total_words += wc
    
    lines.append("---")
    lines.append("")
    lines.append("## {} analyzed by {}".format(ticker, pname))
    lines.append("")
    lines.append("**{}** | Price: ${:,.2f} | {}".format(d.get("name", ticker), price if isinstance(price, (int,float)) else 0, reason))
    lines.append("")
    lines.append(analysis)
    lines.append("")
    lines.append("*Word count: {}*".format(wc))
    lines.append("")

content = "\n".join(lines)
path = os.path.join(OBSIDIAN, "DeepSeek Persona Analysis - {}.md".format(DATE))
with open(path, "w") as f:
    f.write(content)

print("\n" + "=" * 60)
print("DONE: " + path)
print("Total words: {}".format(total_words))
print("Stocks: {}".format(len(results)))
for _, pname, _, _, wc in results:
    print("  {:20s}: {} words".format(pname, wc))
print("=" * 60)
