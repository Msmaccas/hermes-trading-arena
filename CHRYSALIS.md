# Hermes Trading Arena — Project Chrysalis

## Architecture

```
arena_runner.py    → Self-contained master engine (789 lines, all 6 fixes)
  ├── Fix 1: TV MCP active polling handshake (requests.get/2s, timeout=30)
  ├── Fix 3: DeepSeek persona integration (SOUL.md + API per persona)
  ├── Fix 4: Dynamic ticker list (75 baseline + momentum screen)
  ├── Fix 5: Accuracy tracker (score_week_picks try/except)
  └── Fix 6: Web research per top pick (yfinance news headlines)

Scripts live at: ~/.hermes/scripts/
Profiles (SOUL.mds) at: ~/.hermes/profiles/<name>/
Cron managed via: hermes cron tool (not system crontab)
Output: Obsidian vault (iCloud path with spaces)
```

## Critical Config

- **Delegation provider**: deepseek (v4-flash). NEVER openai-codex.
- **TV Desktop CDP**: port 9223, active polling retry (not hardcoded sleep)
- **DeepSeek API key**: in ~/.hermes/.env as DEEPSEEK_API_KEY
- **GitHub**: Msmaccas/hermes-trading-arena

## Phase Status

| Phase | Status | Details |
|-------|--------|---------|
| 0: Housecleaning | ✅ Complete | dream_cycle.py, watchdog fix, script archive |
| 1: Silent Watchmen | ✅ Complete | disk-watchdog, indicator-scanner, profile-watchdog (all no_agent) |
| 2: Trading Arena | ✅ Complete | arena_runner.py with all 6 fixes, cron Sunday 8AM |
| 3: Medical Pipeline | 🔲 Ready | Uses derm-systematic-review + derm-linkedin-content-workflow skills |
| 4: Business Pipeline | 🔲 Ready | Uses xurl, Gmail skills, 3 mentor profiles |

## Last Test Run

June 13, 2026: 74/75 tickers scanned across 8 markets (yfinance).
Report in Obsidian: 10_Trading/Arena Test/Arena Test Report - 2026-06-13.md
