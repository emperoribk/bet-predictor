# Sporty Betting Engine

## Memory
Project memory lives in `.claude/memory/`. Read `MEMORY.md` there for the full index, then load relevant memory files at the start of each session.

## Setup
- Run commands from project root: `python backend/manage.py run_accumulator --date YYYY-MM-DD`
- Python env: activate `venv/` before running
- API keys in `backend/.env`

## Key Commands
- Daily prediction: `python backend/manage.py run_accumulator --date YYYY-MM-DD`
- Backtest: `python backend/scorecard.py` (Fri/Sat/Sun, Oct 2025–Apr 2026)
- Backfill DB: `python backend/manage.py backfill_history --season 2025`
