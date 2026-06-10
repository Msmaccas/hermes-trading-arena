#!/usr/bin/env python3
"""
ACCURACY TRACKER
================
SQLite-based prediction tracking for Hermes trading personas.
Tracks predictions made by competition engine, backtests them,
and scores persona accuracy over time.

Schema:
  predictions: id, run_date, persona, ticker, direction, entry_price, target, stop
  results:     pred_id, check_date, days_out, price_at_check, max_price, min_price, result, return_pct

CLI:
  python3 accuracy_tracker.py --backtest     Run 7-day backtest on recent analyses
  python3 accuracy_tracker.py --leaderboard  Show persona rankings
  python3 accuracy_tracker.py --import <dir> Import analysis files and extract predictions
"""
import os, sys, re, sqlite3, json, datetime, glob
from pathlib import Path
from datetime import date, timedelta
from collections import defaultdict

# ─── CONFIG ──────────────────────────────────────────────────────────────────

DB_PATH = os.path.expanduser("~/.hermes_trading.db")
LOGS_DIR = os.path.expanduser("~/.hermes/logs/competition")
ANALYSIS_PATTERN = r'(?:^|\n)\s*[-*]\s+[A-Z]{1,5}\b'
TICKER_RE = re.compile(r'\b[A-Z]{2,5}\b')  # 2-5 chars only
SKIP_WORDS = {'US', 'UK', 'BUY', 'SELL', 'LONG', 'SHORT', 'STOP', 'THE', 'FOR', 'ALL', 'NEW',
              'TOP', 'BIG', 'WIN', 'LOW', 'HIGH', 'KEY', 'NOW', 'PER', 'UPS', 'RISK', 'PAID',
              'BEAR', 'BULL', 'GOLD', 'OIL', 'FED', 'CEO', 'EPS', 'PEG', 'PE', 'MA', 'RS',
              'EPS', 'ETF', 'IPO', 'ATM', 'ALL', 'ARE', 'CAN', 'HAS', 'ITS', 'MAY', 'NOT',
              'ONE', 'OUT', 'SMA', 'EMA', 'VWAP', 'ATR', 'RSI', 'AVE', 'TGT', 'VOL',
              'DOW', 'SPX', 'NYSE', 'NASDAQ', 'SPY', 'QQQ', 'IWM', 'DIA', 'TLT', 'XLK',
              'XLF', 'XLV', 'XLI', 'XLE', 'XLP', 'XLY', 'XLB', 'XLU', 'XLRE', 'SMH',
              'IBB', 'ARKK', 'ARKW', 'ARKQ', 'ARKF', 'ARKG'}

def is_valid_ticker(t):
    return t not in SKIP_WORDS and not t.endswith('S')  # plural words often aren't tickers
PRICE_DIR_RE = re.compile(
    r'(?:buy|long|short|entry|target|stop|pivot|breakout|break\s*down|price|zone|at\s+)\s*'
    r'[:\$@]?\s*\$?(\d+\.?\d*)',
    re.IGNORECASE
)

# ─── DB SETUP ────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            persona TEXT NOT NULL,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL CHECK(direction IN ('long', 'short')),
            entry_price REAL,
            target REAL,
            stop REAL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pred_id INTEGER NOT NULL,
            check_date TEXT NOT NULL,
            days_out INTEGER NOT NULL,
            price_at_check REAL NOT NULL,
            max_price REAL,
            min_price REAL,
            result TEXT CHECK(result IN ('win', 'loss', 'open', 'invalid')),
            return_pct REAL,
            FOREIGN KEY (pred_id) REFERENCES predictions(id)
        );
        CREATE INDEX IF NOT EXISTS idx_pred_persona ON predictions(persona);
        CREATE INDEX IF NOT EXISTS idx_pred_run ON predictions(run_date);
        CREATE INDEX IF NOT EXISTS idx_result_pred ON results(pred_id);
    """)
    conn.commit()
    conn.close()


# ─── PREDICTION EXTRACTION ──────────────────────────────────────────────────

def extract_predictions(analysis_text, persona=None, run_date=None):
    """Extract predictions from analysis text using regex.
    
    Returns list of dicts: {ticker, direction, entry_price, target, stop}
    """
    predictions = []
    lines = analysis_text.split("\n")

    # State machine: track current ticker context
    current_ticker = None
    current_direction = "long"

    for line in lines:
        stripped = line.strip()

        # Skip headers, empty lines, non-content
        if not stripped or stripped.startswith("#") or stripped.startswith("---"):
            continue

        # Detect ticker lines (pattern: - TICKER or * TICKER or ## TICKER)
        ticker_match = re.match(r'^[-*\s]*([A-Z]{2,5})\b', stripped)
        price_matches = list(re.finditer(r'\$?(\d+\.?\d*)', stripped))

        if ticker_match:
            candidate = ticker_match.group(1)
            if not is_valid_ticker(candidate):
                current_ticker = None
                continue
            skip_words = {"I", "A", "AN", "THE", "IN", "ON", "AT", "TO", "FOR", "OF",
                          "AND", "OR", "IS", "ARE", "WAS", "WERE", "BE", "BEEN",
                          "HAS", "HAVE", "HAD", "DO", "DOES", "DID",
                          "BUT", "AS", "WITH", "BY", "FROM", "NOT", "NO",
                          "IT", "ITS", "WE", "YOU", "OUR", "YOUR", "THIS", "THAT",
                          "ALL", "CAN", "WILL", "WOULD", "COULD", "SHOULD",
                          "MAY", "MIGHT", "MUST", "THAN", "THEN", "THEM",
                          "SOME", "ANY", "EACH", "EVERY", "BOTH", "FEW", "MORE",
                          "RSI", "MACD", "VWAP", "EPS", "PEG", "ROE", "PE",
                          "HIGH", "LOW", "NEW", "OLD", "BIG", "TOP", "KEY",
                          "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN",
                          "EIGHT", "NINE", "TEN", "NOW", "HOW", "WHY", "WHAT",
                          "WHEN", "WHERE", "WHO", "WHICH",
                          "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
                          "INC", "LLC", "LTD", "CORP", "CO",
                          "NASDAQ", "NYSE", "AMEX", "OTC", "IPO", "ATH",
                          "YTD", "YOY", "QTY",
                          "ABC", "XYZ", "SOS", "DNA", "RNA", "CEO", "CFO",
                          "CTA", "ROI", "KPI", "OKR",
                          "SMA", "EMA", "ATR", "VCP", "RS",
                          "SPY", "QQQ", "DIA", "IWM", "GLD", "TLT",
                          "XLK", "XLF", "XLV", "XLI", "XLE", "XLP", "XLY",
                          "XLB", "XLU", "XLRE", "SMH", "IBB", "ARKK"}
            if candidate not in skip_words:
                current_ticker = candidate
                # Check for direction
                line_upper = stripped.upper()
                if any(w in line_upper for w in ["SHORT", "SELL", "BEARISH", "PUT"]):
                    current_direction = "short"
                else:
                    current_direction = "long"

        # If we have a ticker context, extract prices
        if current_ticker and len(price_matches) >= 1:
            prices = [float(m.group(1)) for m in price_matches]
            entry = prices[0] if prices else None
            target = prices[1] if len(prices) > 1 else None
            stop = prices[2] if len(prices) > 2 else None

            pred = {
                "ticker": current_ticker,
                "direction": current_direction,
                "entry_price": entry,
                "target": target,
                "stop": stop,
            }
            predictions.append(pred)

    # De-duplicate by ticker (keep first occurrence)
    seen = set()
    unique = []
    for p in predictions:
        if p["ticker"] not in seen:
            seen.add(p["ticker"])
            unique.append(p)

    return unique


def save_predictions(predictions, persona, run_date=None):
    """Save extracted predictions to database."""
    if not run_date:
        run_date = date.today().isoformat()

    conn = get_db()
    saved = 0
    for p in predictions:
        conn.execute(
            "INSERT INTO predictions (run_date, persona, ticker, direction, entry_price, target, stop) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_date, persona, p["ticker"], p["direction"],
             p.get("entry_price"), p.get("target"), p.get("stop"))
        )
        saved += 1
    conn.commit()
    conn.close()
    return saved


# ─── BACKTESTING ──────────────────────────────────────────────────────────

def get_current_price(ticker):
    """Fetch current price for a ticker using yfinance or fallback."""
    # First try yfinance
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        hist = stock.history(period="5d")
        if not hist.empty:
            latest = hist.iloc[-1]
            return {
                "price": float(round(latest["Close"], 2)),
                "high": float(round(latest["High"], 2)),
                "low": float(round(latest["Low"], 2)),
            }
    except Exception:
        pass

    # Fallback: try Yahoo Finance CSV download
    try:
        import urllib.request
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5d&interval=1d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            result = data["chart"]["result"][0]
            meta = result["meta"]
            quotes = result["indicators"]["quote"][0]
            closes = [c for c in quotes["close"] if c is not None]
            highs = [h for h in quotes["high"] if h is not None]
            lows = [l for l in quotes["low"] if l is not None]
            if closes:
                return {
                    "price": round(closes[-1], 2),
                    "high": round(max(highs[-5:]), 2),
                    "low": round(min(lows[-5:]), 2),
                }
    except Exception:
        pass

    return None


def backtest_prediction(pred_id, check_date=None):
    """Backtest a single prediction: check current price vs predicted levels.
    
    Returns (result_dict) or None if price unavailable.
    """
    conn = get_db()
    row = conn.execute("SELECT * FROM predictions WHERE id = ?", (pred_id,)).fetchone()
    conn.close()

    if not row:
        return None

    if not check_date:
        check_date = date.today().isoformat()

    ticker = row["ticker"]
    entry_price = row["entry_price"]
    target = row["target"]
    stop = row["stop"]
    direction = row["direction"]
    run_date = row["run_date"]

    # Calculate days out
    try:
        run_dt = datetime.datetime.strptime(run_date, "%Y-%m-%d").date()
        check_dt = datetime.datetime.strptime(check_date, "%Y-%m-%d").date()
        days_out = (check_dt - run_dt).days
    except ValueError:
        days_out = 0

    price_data = get_current_price(ticker)
    if not price_data:
        return None

    price = price_data["price"]
    high = price_data["high"]
    low = price_data["low"]

    # Determine result
    result = "open"
    return_pct = 0.0

    if entry_price and entry_price > 0:
        if direction == "long":
            return_pct = round(((price - entry_price) / entry_price) * 100, 2)
            if target and price >= target:
                result = "win"
            elif stop and price <= stop:
                result = "loss"
            elif entry_price and price > entry_price:
                result = "win"  # in profit
            else:
                result = "open"  # still open
        else:  # short
            return_pct = round(((entry_price - price) / entry_price) * 100, 2)
            if target and price <= target:
                result = "win"
            elif stop and price >= stop:
                result = "loss"
            elif entry_price and price < entry_price:
                result = "win"
            else:
                result = "open"

    # Save result
    conn = get_db()
    conn.execute(
        "INSERT INTO results (pred_id, check_date, days_out, price_at_check, max_price, min_price, result, return_pct) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (pred_id, check_date, days_out, price, high, low, result, return_pct)
    )
    conn.commit()
    conn.close()

    return {
        "pred_id": pred_id,
        "ticker": ticker,
        "direction": direction,
        "entry_price": entry_price,
        "target": target,
        "stop": stop,
        "current_price": price,
        "high": high,
        "low": low,
        "return_pct": return_pct,
        "result": result,
        "days_out": days_out,
    }


def run_backtest(days_back=30):
    """Run backtest on predictions from the last N days."""
    init_db()
    cutoff = (date.today() - timedelta(days=days_back)).isoformat()
    today_str = date.today().isoformat()

    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM predictions WHERE run_date >= ? "
        "AND id NOT IN (SELECT pred_id FROM results WHERE check_date = ?)",
        (cutoff, today_str)
    ).fetchall()
    conn.close()

    if not rows:
        print(f"[Backtest]  No untested predictions found since {cutoff}")
        return

    print(f"[Backtest]  Testing {len(rows)} predictions...")
    results = []
    for row in rows:
        result = backtest_prediction(row["id"], today_str)
        if result:
            results.append(result)

    # Summary
    wins = sum(1 for r in results if r["result"] == "win")
    losses = sum(1 for r in results if r["result"] == "loss")
    open_p = sum(1 for r in results if r["result"] == "open")
    avg_return = sum(r["return_pct"] for r in results) / len(results) if results else 0

    print(f"\n[Backtest]  RESULTS ({today_str})")
    print(f"  Tested:    {len(results)}")
    print(f"  Wins:      {wins}")
    print(f"  Losses:    {losses}")
    print(f"  Open:      {open_p}")
    print(f"  Win Rate:  {(wins/(wins+losses)*100) if (wins+losses) > 0 else 0:.1f}%")
    print(f"  Avg Ret:   {avg_return:+.2f}%")
    print()

    for r in sorted(results, key=lambda x: x["return_pct"], reverse=True):
        icon = "+" if r["return_pct"] >= 0 else "-"
        print(f"  [{r['result']:5s}] {icon}{r['ticker']:6s} entry:${r['entry_price']:<8} "
              f"now:${r['current_price']:<8} ({r['return_pct']:+.2f}%) [{r['days_out']}d]")

    return results


# ─── SCORING & LEADERBOARD ───────────────────────────────────────────────

def score_persona(persona):
    """Calculate win rate, avg return, and total picks for a persona.
    
    Returns dict with stats.
    """
    conn = get_db()
    rows = conn.execute(
        """SELECT p.persona, p.ticker, p.direction, p.entry_price, p.target, p.stop,
                  r.result, r.return_pct, r.days_out
           FROM predictions p
           LEFT JOIN results r ON r.pred_id = p.id
           WHERE p.persona = ? AND r.result IN ('win', 'loss')""",
        (persona,)
    ).fetchall()

    total = conn.execute(
        "SELECT COUNT(*) FROM predictions WHERE persona = ?", (persona,)
    ).fetchone()[0]

    open_count = conn.execute(
        """SELECT COUNT(*) FROM predictions p
           LEFT JOIN results r ON r.pred_id = p.id
           WHERE p.persona = ? AND r.result IS NULL""",
        (persona,)
    ).fetchone()[0]
    conn.close()

    wins = [r for r in rows if r["result"] == "win"]
    losses = [r for r in rows if r["result"] == "loss"]
    total_decided = len(wins) + len(losses)

    win_rate = (len(wins) / total_decided * 100) if total_decided > 0 else 0
    avg_return = sum(r["return_pct"] for r in rows) / len(rows) if rows else 0
    best_pick = max(rows, key=lambda r: r["return_pct"]) if rows else None
    worst_pick = min(rows, key=lambda r: r["return_pct"]) if rows else None

    return {
        "persona": persona,
        "total_picks": total,
        "decided": total_decided,
        "open": open_count,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "avg_return": round(avg_return, 2),
        "best_pick": {
            "ticker": best_pick["ticker"],
            "return_pct": best_pick["return_pct"],
        } if best_pick else None,
        "worst_pick": {
            "ticker": worst_pick["ticker"],
            "return_pct": worst_pick["return_pct"],
        } if worst_pick else None,
    }


def show_leaderboard():
    """Display persona rankings sorted by win rate."""
    init_db()
    conn = get_db()
    personas = sorted(set(
        r[0] for r in conn.execute("SELECT DISTINCT persona FROM predictions").fetchall()
    ))
    conn.close()

    if not personas:
        print("[Leaderboard]  No predictions in database yet.")
        print("[Leaderboard]  Run with --import <dir> or --backtest first.")
        return

    scores = [score_persona(p) for p in personas]
    scores.sort(key=lambda s: (s["win_rate"], s["avg_return"]), reverse=True)

    print(f"\n{'='*75}")
    print(f"  ACCURACY LEADERBOARD ({date.today().isoformat()})")
    print(f"{'='*75}")
    print(f"  {'Rank':<5} {'Persona':<20} {'Picks':<5} {'Wins':<5} {'Loss':<5} "
          f"{'WR%':<7} {'Avg Ret%':<10} {'Best':<15}")
    print(f"  {'-'*72}")

    for i, s in enumerate(scores, 1):
        best = f"{s['best_pick']['ticker']}({s['best_pick']['return_pct']:+.1f}%)" if s["best_pick"] else "-"
        print(f"  {i:<5} {s['persona']:<20} {s['total_picks']:<5} {s['wins']:<5} {s['losses']:<5} "
              f"{s['win_rate']:<7.1f} {s['avg_return']:<+10.2f} {best:<15}")

    print(f"{'='*75}")
    print()


def import_analyses(import_dir=None):
    """Import analysis files and extract predictions."""
    if not import_dir:
        import_dir = LOGS_DIR

    path = Path(import_dir)
    if not path.exists():
        print(f"[Import]  Directory not found: {import_dir}")
        return

    # Find all .md files recursively
    files = list(path.rglob("*.md"))
    print(f"[Import]  Scanning {len(files)} analysis files in {import_dir}...")

    total_preds = 0
    for fpath in files:
        # Extract persona and date from filename
        filename = fpath.stem
        parts = filename.split(" - ")
        persona = parts[0].strip().lower() if len(parts) >= 1 else "unknown"

        # Try to get date from filename or dir structure
        run_date = None
        if len(parts) >= 2:
            date_candidate = parts[1].strip()
            if re.match(r'^\d{4}-\d{2}-\d{2}$', date_candidate):
                run_date = date_candidate
        if not run_date:
            # Try parent directory name as date
            parent = str(fpath.parent.name)
            if re.match(r'^\d{4}-\d{2}-\d{2}$', parent):
                run_date = parent

        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            continue

        # Skip frontmatter
        content_clean = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL)

        preds = extract_predictions(content_clean, persona, run_date)
        if preds:
            saved = save_predictions(preds, persona, run_date)
            total_preds += saved
            print(f"[Import]  {saved} predictions from {filename}")

    print(f"\n[Import]  Total: {total_preds} predictions imported")


# ─── CLI ──────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    init_db()
    cmd = sys.argv[1]

    if cmd == "--backtest":
        days_back = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        run_backtest(days_back)

    elif cmd == "--leaderboard":
        show_leaderboard()

    elif cmd == "--import":
        import_dir = sys.argv[2] if len(sys.argv) > 2 else LOGS_DIR
        import_analyses(import_dir)

    elif cmd == "--score":
        if len(sys.argv) < 3:
            print("Usage: accuracy_tracker.py --score <persona>")
            return
        stats = score_persona(sys.argv[2])
        print(f"\n{stats['persona'].title()} Stats:")
        print(f"  Total Picks:  {stats['total_picks']}")
        print(f"  Decided:      {stats['decided']} (W: {stats['wins']}, L: {stats['losses']})")
        print(f"  Open:         {stats['open']}")
        print(f"  Win Rate:     {stats['win_rate']}%")
        print(f"  Avg Return:   {stats['avg_return']:+.2f}%")
        if stats['best_pick']:
            print(f"  Best Pick:    {stats['best_pick']['ticker']} ({stats['best_pick']['return_pct']:+.2f}%)")
        if stats['worst_pick']:
            print(f"  Worst Pick:   {stats['worst_pick']['ticker']} ({stats['worst_pick']['return_pct']:+.2f}%)")

    elif cmd == "--db-path":
        print(DB_PATH)

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
