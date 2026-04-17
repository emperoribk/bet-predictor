# Sporty Betting Engine

## Memory
Project memory lives in `.claude/memory/`. Read `MEMORY.md` there for the full index, then load relevant memory files at the start of each session.

## Setup (any machine)
```bash
git pull origin explore/higher-odds-combo
cd backend
pip install -r requirements.txt
python manage.py migrate
python manage.py loaddata bets/fixtures/historical_fixtures.json
```
- API keys go in `backend/.env` (see `.env.example`)
- Run commands from `backend/` directory using the local Python

## Canonical Fixture Data
- `backend/bets/fixtures/historical_fixtures.json` is the **single source of truth** for historical form data
- All machines must load this file — do NOT use `backfill_history` to build the DB independently (it produces different record counts and causes different picks on different machines)
- Current export: **8,737 fixtures** (season 2024 + 2025)
- When you add significant new data, re-export: `python manage.py dumpdata bets.HistoricalFixture --indent 2 > bets/fixtures/historical_fixtures.json` then commit it

## Hosting / Multi-user
- SQLite (default) is local-only — each machine has its own DB → different picks per user
- For consistent picks across all users when hosted, use a **shared PostgreSQL DB**
  - Set `DATABASE_URL` in `.env` pointing to the shared Postgres instance
  - Run `migrate` + `loaddata` once on the server — all users then hit the same data

## Key Commands
- Daily prediction: `python manage.py run_accumulator --date YYYY-MM-DD`
- Backtest: `python scorecard.py` (Fri/Sat/Sun, Oct 2025–Apr 2026)
- Re-export fixture data: `python manage.py dumpdata bets.HistoricalFixture --indent 2 > bets/fixtures/historical_fixtures.json`
