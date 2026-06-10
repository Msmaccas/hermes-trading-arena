#!/usr/bin/env python3
"""
CDP Indicator Reader — connects to TradingView via Chrome DevTools Protocol
Reads REAL indicator values from the running TV Desktop app.

Usage:
  python3 cdp_indicator_reader.py
"""
import json, os, sys, base64, time, urllib.request, subprocess

CDP_PORT = 9222
TV_APP = "/Applications/TradingView.app/Contents/MacOS/TradingView"

# The exact indicators the user has favorited on their chart
TARGET_INDICATORS = [
    "21D EMA STRUCTUREAnts",
    "ATR% multiple from 50-MA",
    "Auto Anchored Volume Profile",
    "Dual Earnings VWAP",
    "EMA 9, 21, 50, 200",
    "EPS & Sales - MarketSmith",
    "IBD Distribution Days",
    "Inside Day",
    "MarketSmith Volumes",
    "Simple Volume with Pocket Pivots",
    "Swing Data",
    "TraderLion's Relative Strength Line",
    "Mark Minervini Trend Template",
]

def json_get(url):
    try:
        return json.loads(urllib.request.urlopen(url, timeout=5).read())
    except:
        return None

def main():
    print("=" * 60)
    print("CDP INDICATOR READER — TradingView Desktop")
    print("=" * 60)
    
    # Step 1: Check if CDP is available
    pages = json_get(f"http://localhost:{CDP_PORT}/json")
    if not pages:
        print(f"[!] CDP not found on port {CDP_PORT}.")
        print("[*] Launching TradingView with --remote-debugging-port...")
        subprocess.Popen([TV_APP, f"--remote-debugging-port={CDP_PORT}"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[*] Waiting 15s for TV to start...")
        time.sleep(15)
        pages = json_get(f"http://localhost:{CDP_PORT}/json")
        if not pages:
            print("[X] Still cannot connect. Please run manually:")
            print(f'    {TV_APP} --remote-debugging-port={CDP_PORT}')
            return 1
    
    # Find the TradingView page
    tv_page = None
    for p in pages:
        if "tradingview" in p.get("title","").lower() or "chart" in p.get("url",""):
            tv_page = p
            break
    if not tv_page:
        tv_page = pages[0]
    
    ws_url = tv_page.get("webSocketDebuggerUrl")
    print(f"[+] Connected: {tv_page.get('title','?')[:60]}")
    print(f"[+] WS: {ws_url[:80]}...")
    
    import websocket
    ws = websocket.create_connection(ws_url, timeout=30)
    
    def cdp(method, params=None, msg_id=1):
        ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        while True:
            r = json.loads(ws.recv())
            if r.get("id") == msg_id:
                return r.get("result", {})
    
    cdp("Page.enable", msg_id=1)
    cdp("Runtime.enable", msg_id=2)
    
    # Read symbol + timeframe
    js = """
    (function() {
        try {
            var w = window;
            return JSON.stringify({
                symbol: (w.tvWidget ? w.tvWidget.activeChart().symbol() : '?'),
                timeframe: (w.tvWidget ? w.tvWidget.activeChart().resolution() : '?'),
            });
        } catch(e) { return JSON.stringify({error: e.message}); }
    })()
    """
    r = cdp("Runtime.evaluate", {"expression": js, "returnByValue": True}, msg_id=3)
    val = r.get("result", {}).get("value", "{}")
    try:
        info = json.loads(val)
        print(f"\n[+] SYMBOL: {info.get('symbol','?')} | TF: {info.get('timeframe','?')}")
    except:
        print(f"[!] Symbol info: {val[:200]}")
    
    # Read ALL indicator values from the data window
    print(f"\n[+] READING {len(TARGET_INDICATORS)} INDICATORS...")
    
    read_js = """
    (function() {
        var r = {};
        // Method 1: Data window table
        try {
            var dw = document.querySelector('[class*="dataWindow"], [class*="data-window"]');
            if (dw) {
                var rows = dw.querySelectorAll('tr');
                rows.forEach(function(row) {
                    var cells = row.querySelectorAll('td');
                    if (cells.length >= 2) {
                        var k = cells[0].textContent.trim();
                        var v = cells[1].textContent.trim();
                        if (k && v) r[k] = v;
                    }
                });
            }
        } catch(e) {}
        // Method 2: Internal chart state
        try {
            if (window.tvWidget) {
                var studies = window.tvWidget.activeChart().getAllStudies();
                for (var i = 0; i < studies.length; i++) {
                    var s = studies[i];
                    var name = s.name ? s.name() : '';
                    if (name) r['study_' + i] = name;
                }
            }
        } catch(e) {}
        return JSON.stringify(r);
    })()
    """
    r = cdp("Runtime.evaluate", {"expression": read_js, "returnByValue": True}, msg_id=4)
    val = r.get("result", {}).get("value", "{}")
    try:
        data = json.loads(val)
        found = []
        for target in TARGET_INDICATORS:
            matched = False
            for k, v in data.items():
                if target.lower() in k.lower() or target.lower() in v.lower():
                    print(f"  [FOUND] {target}: {k} = {v}")
                    found.append(target)
                    matched = True
                    break
            if not matched:
                print(f"  [MISS]  {target}")
        print(f"\n[+] Found {len(found)}/{len(TARGET_INDICATORS)} indicators")
        if len(found) < len(TARGET_INDICATORS):
            print("[*] Raw data window entries:")
            for k, v in sorted(data.items()):
                print(f"    {k}: {v}")
    except:
        print(f"[!] Raw: {val[:500]}")
    
    # Screenshot
    r = cdp("Page.captureScreenshot", {"format": "png"}, msg_id=5)
    img = r.get("data", "")
    if img:
        path = os.path.expanduser("~/tradingview-mcp-jackson/screenshots/cdp_chart.png")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(base64.b64decode(img))
        print(f"\n[+] Screenshot: {path} ({len(img)//1024}KB)")
    
    ws.close()
    print("\n[+] DONE. Real indicator data ready for persona analysis.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
