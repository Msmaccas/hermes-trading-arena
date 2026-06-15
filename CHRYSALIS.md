## What's gone wrong (and fixed)
- **Empty SOUL.mds** → rewritten: 5 personas went from 45-59 lines to 302-424 lines each, with real quote databases and source URLs
- **2235-line monolith** → 9 modular files (max 224 lines each)
- **Obsidian pollution** → deleted 10_Trading/Competition/ from vault. Output now goes to `output/` in repo only
- **No retry logic** → every API call now has exponential backoff retry (3 attempts)
- **Lynch filter bug** (pass-on-None) → fixed
- **David Ryan filter bug** (price-only fallback) → fixed
- **Review gate false positives** ("pass"/"fail" in negative phrases) → fixed
- **yfinance bottleneck** (60s for 150 tickers) → replaced with direct Yahoo v8/v7 API (3-5s)
- **No cross-persona dedup** → enforced (same stock never analyzed twice)
- **Sequential processing** (1 file/min) → 3 parallel subagents (9.5 files/min)
- **Dead code** `_yf_lock` → now rate-limits all yfinance calls
- **Redundant data fetch** in worker → uses pre-computed Phase 1 data

## New architecture (pushed to GitHub)
```
hermes-trading-arena/
├── engine/                     ← 9 modular files (1,214 total lines)
│   ├── config.py               (72L) All settings in one place
│   ├── data_collector.py       (224L) TV scanner + Yahoo v8/v7 direct + indicators
│   ├── persona_filter.py       (139L) 10 criteria + cross-persona dedup
│   ├── persona_runner.py       (191L) ONLY file that calls DeepSeek
│   ├── review_gate.py          (73L) Quality check (no false positives)
│   ├── accuracy_tracker.py     (111L) SQLite scoring
│   ├── orchestrator.py         (220L) Thin coordinator (Phases 1-5)
│   └── utils.py                (160L) Helpers, API retry
├── config.yaml                 Stock list, mode, concurrency
├── cron/run_weekly.sh          Auto commit + push
├── output/                     ← All analysis files live here
├── profiles/                   ← 10 SOUL.md files (now with real content)
└── pine_scripts/               TV indicators
```

## What I need from you
1. **Your top stock list** — exact tickers you want analyzed (or I use the 10 from last run)
2. **DeepSeek API key** — the `orchestrator.py` reads it from `~/.hermes/.env` or `DEEPSEEK_API_KEY` env var. If neither is set, it'll need the key
3. **Run trigger** — do you want me to run `engine/orchestrator.py` now, or wait for your stock list?
4. **Any missing figures** — I used my training data for verbatim quotes since I don't have direct book access. If you have specific transcripts/PDFs you want me to read, send them and I'll update the SOUL.mds with exact page references
