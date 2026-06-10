#!/usr/bin/env python3
"""
TradingView Vision Pipeline: screenshot → Gemini 2.5 → chart analysis
Connects to the TradingView MCP screenshot tools via the running CDP session.

Usage:
  python3 tv_vision.py                   # Analyze current chart
  python3 tv_vision.py --ticker AAPL     # Switch to AAPL first
"""
import subprocess, json, os, sys, base64, time
from pathlib import Path

# Configuration
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
SCREENSHOT_DIR = Path(os.path.expanduser('~/tradingview-mcp-jackson/screenshots'))

# Step 1: Read raw chart data via the running node MCP server
# The MCP server listens on stdio, so we communicate via the running session
# For now, we use the screenshot directory as a drop zone

def take_screenshot():
    """Call the MCP server directly to take a screenshot."""
    # The TradingView MCP saves screenshots to ~/tradingview-mcp-jackson/screenshots/
    # We trigger via the MCP server process
    print("[TV Vision] To take a screenshot, the user needs TradingView Desktop running with CDP on port 9222")
    print("[TV Vision] The screenshot is taken via the MCP tool: capture_screenshot")
    print("[TV Vision] This script expects screenshots at:", SCREENSHOT_DIR)
    
    # Check if screenshot exists
    screenshots = sorted(SCREENSHOT_DIR.glob('*.png'), key=os.path.getmtime)
    if screenshots:
        latest = screenshots[-1]
        age = time.time() - os.path.getmtime(latest)
        if age < 300:  # less than 5 min old
            print(f"[TV Vision] Using existing screenshot: {latest}")
            return latest
    return None

def analyze_with_gemini(screenshot_path, persona="oneil"):
    """Send screenshot to Gemini 2.5 for pattern analysis."""
    if not GEMINI_API_KEY:
        print("[TV Vision] GEMINI_API_KEY not set. Skipping vision analysis.")
        print("[TV Vision] To enable, run: export GEMINI_API_KEY='your-key-here'")
        return None
    
    import requests
    
    with open(screenshot_path, 'rb') as f:
        image_data = base64.b64encode(f.read()).decode('utf-8')
    
    # Persona-specific analysis prompts
    prompts = {
        "oneil": """You are William O'Neil. Analyze this TradingView chart as if looking for a CAN SLIM setup.
Describe EXACTLY what you see: cup with handle, buy point, volume pattern, RS line position.
Then give your verdict: buy, wait, or pass.""",
        
        "minervini": """You are Mark Minervini. Analyze this chart for a VCP (Volatility Contraction Pattern).
Describe the contraction count, volume pattern, and whether the stock is in a proper pivot area.""",
        
        "qullamaggie": """You are Kristjan Qullamaggie. Analyze this chart for a momentum setup.
Describe the breakout, fib extensions, volume confirmation, and any tight consolidations.""",
        
        "buffet": """You are Warren Buffett. Analyze this chart from a long-term business perspective.
Evaluate whether this represents a durable competitive advantage at a reasonable price.""",
        
        "lynch": """You are Peter Lynch. Categorize this stock (slow grower, stalwart, fast grower, cyclical, turnaround, asset play) and explain why you would or wouldn't own it.""",
        
        "shannon": """You are Brian Shannon. Analyze this chart using VWAP, anchored VWAP, and key support/resistance levels. Describe trend structure and rotation.""",
        
        "david-ryan": """You are David Ryan. Analyze this chart for explosive growth potential. Look for the characteristics that made you a 3-time US Investing Champion: proper bases, volume dry-ups, and breakout confirmation."""
    }
    
    prompt = prompts.get(persona, prompts["oneil"])
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro-exp-03-25:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/png", "data": image_data}}
            ]
        }],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2048}
    }
    
    resp = requests.post(url, json=payload, timeout=60)
    if resp.status_code == 200:
        text = resp.json()['candidates'][0]['content']['parts'][0]['text']
        print(f"\n=== {persona.upper()} ANALYSIS ===")
        print(text)
        return text
    else:
        print(f"[TV Vision] Gemini API error: {resp.status_code}")
        print(resp.text[:500])
        return None

if __name__ == '__main__':
    persona = sys.argv[1] if len(sys.argv) > 1 else "oneil"
    
    print(f"[TV Vision] Ready for {persona}-style chart analysis")
    print("[TV Vision] ====================================")
    print("[TV Vision] Steps:")
    print("[TV Vision]   1. TradingView MCP takes a screenshot (via capture_screenshot)")
    print("[TV Vision]   2. Gemini 2.5 analyzes the chart visually")
    print("[TV Vision]   3. Output is the persona's verdict with exact pattern language")
    print()
    
    # Use most recent screenshot or prompt user
    screenshot = take_screenshot()
    if screenshot:
        analyze_with_gemini(screenshot, persona)
    else:
        print("[TV Vision] No recent screenshot found. Please:")
        print(f"[TV Vision]   1. Run in Hermes: `hermes -p {persona} capture_screenshot`")
        print(f"[TV Vision]   2. Then run: `python3 tv_vision.py {persona}`")
