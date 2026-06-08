"""Cross-persona dedup and accuracy tracking for deep_research_engine."""

import os, sqlite3, json
from datetime import date, timedelta

ACCURACY_DB = os.path.expanduser("~/.hermes_trading.db")


def init_accuracy_db():
    """Create accuracy tracking tables if they don't exist."""
    conn = sqlite3.connect(ACCURACY_DB)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS accuracy_picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            persona TEXT NOT NULL,
            ticker TEXT NOT NULL,
            entry_price REAL,
            direction TEXT DEFAULT 'LONG',
            exit_price_7d REAL,
            exit_price_30d REAL,
            score_7d REAL,
            score_30d REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS accuracy_rankings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start TEXT NOT NULL,
            persona TEXT NOT NULL,
            avg_score REAL,
            total_picks INTEGER,
            correct_picks INTEGER,
            rank INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()
    print("[Engine]  acc DB ready at %s" % ACCURACY_DB)


def record_pick(persona, ticker, entry_price, direction="LONG"):
    """Record a pick for future accuracy tracking."""
    try:
        conn = sqlite3.connect(ACCURACY_DB)
        conn.execute(
            "INSERT INTO accuracy_picks (date, persona, ticker, entry_price, direction) VALUES (?, ?, ?, ?, ?)",
            (date.today().isoformat(), persona, ticker, entry_price, direction)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def score_week_picks():
    """Score last week's picks against current prices."""
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    two_weeks_ago = (date.today() - timedelta(days=14)).isoformat()

    try:
        import yfinance as yf
        conn = sqlite3.connect(ACCURACY_DB)
        rows = conn.execute(
            "SELECT id, ticker, entry_price FROM accuracy_picks WHERE date >= ? AND date <= ? AND score_7d IS NULL",
            (two_weeks_ago, week_ago)
        ).fetchall()

        scored = 0
        for pick_id, ticker, entry_price in rows:
            try:
                stock = yf.Ticker(ticker)
                hist = stock.history(period="1mo")
                if not hist.empty:
                    current = float(hist['Close'].iloc[-1])
                    if entry_price and entry_price > 0:
                        score = (current / entry_price - 1) * 100
                        conn.execute(
                            "UPDATE accuracy_picks SET exit_price_7d = ?, score_7d = ? WHERE id = ?",
                            (current, round(score, 2), pick_id)
                        )
                        conn.commit()
                        scored += 1
            except Exception:
                pass

        conn.close()
        print("[Engine]  Scored %d/%d picks from last week" % (scored, len(rows)))
        return scored
    except Exception as e:
        print("[Engine]  Score error: %s" % e)
        return 0


def resolve_watchlist_conflicts(all_watchlists):
    """Find tickers picked by multiple personas, return conflict info."""
    ticker_counts = {}
    for persona, tickers in all_watchlists.items():
        for t in tickers:
            if t not in ticker_counts:
                ticker_counts[t] = []
            ticker_counts[t].append(persona)

    conflicts = {t: ps for t, ps in ticker_counts.items() if len(ps) > 1}
    if conflicts:
        print("[Engine]  WARN: %d overlapping picks across personas:" % len(conflicts))
        for ticker, personas in sorted(conflicts.items(), key=lambda x: -len(x[1])):
            print("[Engine]    %s -> %s (%dx)" % (ticker, ", ".join(personas), len(personas)))
    return conflicts


def compute_rankings():
    """Compute weekly rankings from accuracy scores."""
    conn = sqlite3.connect(ACCURACY_DB)
    week_start = (date.today() - timedelta(days=7)).isoformat()
    
    rows = conn.execute("""
        SELECT persona, AVG(score_7d) as avg_score, COUNT(*) as total, 
               SUM(CASE WHEN score_7d > 0 THEN 1 ELSE 0 END) as correct
        FROM accuracy_picks 
        WHERE score_7d IS NOT NULL AND date >= ?
        GROUP BY persona
        ORDER BY avg_score DESC
    """, (week_start,)).fetchall()
    
    for rank, (persona, avg_score, total, correct) in enumerate(rows, 1):
        conn.execute(
            "INSERT INTO accuracy_rankings (week_start, persona, avg_score, total_picks, correct_picks, rank) VALUES (?, ?, ?, ?, ?, ?)",
            (week_start, persona, round(avg_score, 2), total, correct, rank)
        )
    
    conn.commit()
    conn.close()
    
    print("[Engine]  Rankings computed for %s:" % week_start)
    if rows:
        for rank, (persona, avg_score, total, correct) in enumerate(rows, 1):
            marker = "ELIMINATE" if rank > len(rows) - 2 else ""
            print("[Engine]    #%d %s: avg %.2f%% (%d/%d correct) %s" % (rank, persona, avg_score, correct, total, marker))
    
    return rows


if __name__ == "__main__":
    init_accuracy_db()
    print("Accuracy DB initialized")
