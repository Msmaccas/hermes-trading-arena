#!/usr/bin/env python3
"""AccuracyDB — own clean SQLite tables for the accuracy engine v2.

Uses a separate table namespace (accuracy_v2_*) to avoid collisions
with the legacy DB tables (predictions, results).
"""

import os
import sqlite3
import datetime
from typing import Dict, List, Optional, Tuple

DB_PATH = os.path.expanduser("~/.hermes_trading.db")

# ─── Schema ──────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
-- Structured predictions extracted from generator output
CREATE TABLE IF NOT EXISTS accuracy_v2_predictions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date    TEXT    NOT NULL,       -- ISO date of the arena run
    persona     TEXT    NOT NULL,
    ticker      TEXT    NOT NULL,
    direction   TEXT    NOT NULL,       -- 'bullish', 'bearish', 'neutral'
    entry_min   REAL,                   -- entry price range low
    entry_max   REAL,                   -- entry price range high
    target      REAL,                   -- target price
    stop        REAL,                   -- stop-loss price
    confidence  REAL    DEFAULT 0.5,   -- expressed confidence (0-1)
    created_at  TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_av2_pred_run  ON accuracy_v2_predictions(run_date);
CREATE INDEX IF NOT EXISTS idx_av2_pred_pt   ON accuracy_v2_predictions(persona, ticker);

-- Outcome results at multiple timeframes
CREATE TABLE IF NOT EXISTS accuracy_v2_outcomes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pred_id     INTEGER NOT NULL REFERENCES accuracy_v2_predictions(id),
    timeframe   TEXT    NOT NULL,       -- '7d', '30d'
    result      TEXT    NOT NULL,       -- 'win', 'loss', 'neutral'
    return_pct  REAL,                   -- realised return percentage
    entry_price REAL,                   -- actual entry price used
    exit_price  REAL,                   -- price at timeframe end
    max_price   REAL,                   -- highest intermediate price
    min_price   REAL,                   -- lowest intermediate price
    check_date  TEXT    NOT NULL,       -- date of the check
    brier_score REAL,                   -- Brier score component (0=perfect, 1=worst)
    created_at  TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_av2_out_pred   ON accuracy_v2_outcomes(pred_id);
CREATE INDEX IF NOT EXISTS idx_av2_out_tf     ON accuracy_v2_outcomes(timeframe);

-- Regime at prediction time (for regime-adjusted scoring)
CREATE TABLE IF NOT EXISTS accuracy_v2_regime_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pred_id     INTEGER NOT NULL REFERENCES accuracy_v2_predictions(id),
    regime      TEXT    NOT NULL,       -- TrendingBull, ChoppyRange, Correction, VBottom
    confidence  REAL,                   -- regime classification confidence
    adx         REAL                    -- ADX value at time of prediction
);

CREATE INDEX IF NOT EXISTS idx_av2_reg_pred ON accuracy_v2_regime_snapshots(pred_id);

-- Persona ranking snapshots
CREATE TABLE IF NOT EXISTS accuracy_v2_rankings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date    TEXT    NOT NULL,
    persona     TEXT    NOT NULL,
    total_picks INTEGER DEFAULT 0,
    wins_7d     INTEGER DEFAULT 0,
    losses_7d   INTEGER DEFAULT 0,
    win_rate_7d REAL,
    avg_return_30d REAL,
    avg_brier   REAL,                   -- lower is better
    bayesian_score REAL,                -- Bayesian-adjusted score
    regime_adj_score REAL,              -- regime-corrected score
    rank        INTEGER,
    eliminated  INTEGER DEFAULT 0,      -- 0=active, 1=eliminated
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_av2_rank_run ON accuracy_v2_rankings(run_date);
"""


class AccuracyDB:
    """Wrapper around the accuracy v2 SQLite tables."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._conn = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def init_schema(self):
        """Create tables if they don't exist."""
        conn = self.connect()
        conn.executescript(SCHEMA_SQL)
        conn.commit()

    # ─── Prediction CRUD ───────────────────────────────────────────────────

    def insert_prediction(
        self,
        run_date: str,
        persona: str,
        ticker: str,
        direction: str,
        entry_min: Optional[float],
        entry_max: Optional[float],
        target: Optional[float],
        stop: Optional[float],
        confidence: float = 0.5,
    ) -> int:
        """Insert a structured prediction. Returns row ID."""
        conn = self.connect()
        cur = conn.execute(
            """INSERT INTO accuracy_v2_predictions
               (run_date, persona, ticker, direction, entry_min, entry_max,
                target, stop, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_date, persona, ticker, direction,
             entry_min, entry_max, target, stop, confidence),
        )
        conn.commit()
        return cur.lastrowid

    def get_predictions_for_persona(
        self, persona: str, limit: int = 100
    ) -> List[sqlite3.Row]:
        conn = self.connect()
        return conn.execute(
            "SELECT * FROM accuracy_v2_predictions WHERE persona = ? ORDER BY run_date DESC LIMIT ?",
            (persona, limit),
        ).fetchall()

    def get_all_predictions(
        self, since_date: Optional[str] = None
    ) -> List[sqlite3.Row]:
        conn = self.connect()
        if since_date:
            return conn.execute(
                "SELECT * FROM accuracy_v2_predictions WHERE run_date >= ? ORDER BY run_date",
                (since_date,),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM accuracy_v2_predictions ORDER BY run_date"
        ).fetchall()

    # ─── Outcome CRUD ──────────────────────────────────────────────────────

    def insert_outcome(
        self,
        pred_id: int,
        timeframe: str,
        result: str,
        return_pct: float,
        entry_price: float,
        exit_price: float,
        max_price: float,
        min_price: float,
        check_date: str,
        brier_score: float,
    ) -> int:
        conn = self.connect()
        cur = conn.execute(
            """INSERT INTO accuracy_v2_outcomes
               (pred_id, timeframe, result, return_pct, entry_price, exit_price,
                max_price, min_price, check_date, brier_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pred_id, timeframe, result, return_pct, entry_price,
             exit_price, max_price, min_price, check_date, brier_score),
        )
        conn.commit()
        return cur.lastrowid

    def get_outcomes(self, timeframe: str = "7d") -> List[sqlite3.Row]:
        conn = self.connect()
        return conn.execute(
            """SELECT p.persona, p.ticker, p.direction, o.*
               FROM accuracy_v2_outcomes o
               JOIN accuracy_v2_predictions p ON o.pred_id = p.id
               WHERE o.timeframe = ?
               ORDER BY o.check_date""",
            (timeframe,),
        ).fetchall()

    # ─── Regime snapshots ──────────────────────────────────────────────────

    def insert_regime_snapshot(
        self, pred_id: int, regime: str, confidence: float, adx: float
    ) -> int:
        conn = self.connect()
        cur = conn.execute(
            """INSERT INTO accuracy_v2_regime_snapshots
               (pred_id, regime, confidence, adx)
               VALUES (?, ?, ?, ?)""",
            (pred_id, regime, confidence, adx),
        )
        conn.commit()
        return cur.lastrowid

    # ─── Rankings ──────────────────────────────────────────────────────────

    def insert_ranking(
        self,
        run_date: str,
        persona: str,
        total_picks: int,
        wins_7d: int,
        losses_7d: int,
        win_rate_7d: float,
        avg_return_30d: float,
        avg_brier: float,
        bayesian_score: float,
        regime_adj_score: float,
        rank: int,
        eliminated: int = 0,
    ) -> int:
        conn = self.connect()
        cur = conn.execute(
            """INSERT INTO accuracy_v2_rankings
               (run_date, persona, total_picks, wins_7d, losses_7d, win_rate_7d,
                avg_return_30d, avg_brier, bayesian_score, regime_adj_score,
                rank, eliminated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_date, persona, total_picks, wins_7d, losses_7d, win_rate_7d,
             avg_return_30d, avg_brier, bayesian_score, regime_adj_score,
             rank, eliminated),
        )
        conn.commit()
        return cur.lastrowid

    def get_latest_rankings(self) -> List[sqlite3.Row]:
        conn = self.connect()
        # Get most recent ranking for each persona
        return conn.execute(
            """SELECT r1.* FROM accuracy_v2_rankings r1
               INNER JOIN (
                   SELECT persona, MAX(run_date) as max_date
                   FROM accuracy_v2_rankings
                   GROUP BY persona
               ) r2 ON r1.persona = r2.persona AND r1.run_date = r2.max_date
               ORDER BY r1.rank"""
        ).fetchall()

    def get_persona_stats(self, persona: str) -> Dict:
        """Aggregate stats for a persona across all timeframes."""
        conn = self.connect()
        row = conn.execute(
            """SELECT
                   COUNT(DISTINCT p.id) as total_picks,
                   SUM(CASE WHEN o.timeframe = '7d' AND o.result = 'win' THEN 1 ELSE 0 END) as wins_7d,
                   SUM(CASE WHEN o.timeframe = '7d' AND o.result = 'loss' THEN 1 ELSE 0 END) as losses_7d,
                   AVG(CASE WHEN o.timeframe = '30d' THEN o.return_pct END) as avg_return_30d,
                   AVG(CASE WHEN o.timeframe = '7d' THEN o.brier_score END) as avg_brier_7d,
                   AVG(CASE WHEN o.timeframe = '30d' THEN o.brier_score END) as avg_brier_30d
               FROM accuracy_v2_predictions p
               LEFT JOIN accuracy_v2_outcomes o ON p.id = o.pred_id
               WHERE p.persona = ?""",
            (persona,),
        ).fetchone()

        return {
            "persona": persona,
            "total_picks": row["total_picks"] or 0,
            "wins_7d": row["wins_7d"] or 0,
            "losses_7d": row["losses_7d"] or 0,
            "avg_return_30d": row["avg_return_30d"] or 0.0,
            "avg_brier_7d": row["avg_brier_7d"] or 0.5,
            "avg_brier_30d": row["avg_brier_30d"] or 0.5,
        }


def init_accuracy_db():
    """Convenience: initialise the accuracy v2 tables."""
    db = AccuracyDB()
    db.init_schema()
    print(f"[AccuracyV2]  DB initialised at {DB_PATH}")
    db.close()
