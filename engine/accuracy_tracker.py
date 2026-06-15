"""
engine/accuracy_tracker.py — Track persona performance via SQLite.
Records picks, scores them against actual prices, ranks personas.
"""

import os
import sqlite3
import datetime
import yfinance as yf

from engine.config import TODAY


DB_PATH = os.path.expanduser("~/.hermes_trading.db")


def _get_db():
    """Get SQLite connection (create schema if needed)."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS accuracy_picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            persona TEXT NOT NULL,
            ticker TEXT NOT NULL,
            entry_price REAL,
            exit_price_7d REAL,
            score_7d REAL
        )
    """)
    conn.commit()
    return conn


def init_db():
    """Initialize the database schema (idempotent)."""
    conn = _get_db()
    conn.close()
    print(f"[Accuracy]  DB ready at {DB_PATH}", flush=True)


def record_pick(persona, ticker, entry_price):
    """Record a pick for a persona at the given entry price."""
    conn = _get_db()
    conn.execute(
        "INSERT INTO accuracy_picks (run_date, persona, ticker, entry_price) VALUES (?, ?, ?, ?)",
        (TODAY.isoformat(), persona, ticker, entry_price),
    )
    conn.commit()
    conn.close()


def score_week_picks():
    """Score picks from last week by comparing to current prices."""
    conn = _get_db()
    week_ago = (TODAY - datetime.timedelta(days=7)).isoformat()
    two_weeks = (TODAY - datetime.timedelta(days=14)).isoformat()

    rows = conn.execute(
        "SELECT id, ticker, entry_price FROM accuracy_picks "
        "WHERE run_date >= ? AND run_date <= ? AND score_7d IS NULL",
        (two_weeks, week_ago),
    ).fetchall()

    scored = 0
    for pick_id, ticker, entry_price in rows:
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="1mo")
            if not hist.empty:
                current = float(hist["Close"].iloc[-1])
                if entry_price and entry_price > 0:
                    score = (current / entry_price - 1) * 100
                    conn.execute(
                        "UPDATE accuracy_picks SET exit_price_7d = ?, score_7d = ? WHERE id = ?",
                        (current, round(score, 2), pick_id),
                    )
                    conn.commit()
                    scored += 1
        except Exception:
            pass

    conn.close()
    print(f"[Accuracy]  Scored {scored}/{len(rows)} picks from last week", flush=True)
    return scored


def compute_rankings():
    """Rank personas by average accuracy score over last 30 days."""
    conn = _get_db()
    month_ago = (TODAY - datetime.timedelta(days=30)).isoformat()

    rows = conn.execute(
        "SELECT persona, AVG(score_7d) as avg_score, COUNT(*) as n "
        "FROM accuracy_picks WHERE run_date >= ? AND score_7d IS NOT NULL "
        "GROUP BY persona ORDER BY avg_score DESC",
        (month_ago,),
    ).fetchall()

    conn.close()

    if not rows:
        print("[Accuracy]  No scored picks yet", flush=True)
        return []

    print(f"[Accuracy]  Rankings (last 30 days):", flush=True)
    ranking = []
    for persona, avg_score, n in rows:
        print(f"  {n:3d} picks  {avg_score:+.2f}%  {persona}", flush=True)
        ranking.append({"persona": persona, "avg_score": avg_score, "n": n})
    return ranking
