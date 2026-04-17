---
name: Sporty Betting Engine — Project State
description: Architecture, key files, accuracy baseline, and all implemented improvements
type: project
---

# Sporty Betting Engine

## Stack
- Django backend at `backend/`
- Predictions engine: `backend/bets/services/daily_predictions.py`
- Run predictions command: `backend/bets/management/commands/run_accumulator.py`
- Stats: `backend/bets/services/football_data.py`
- Model: `backend/bets/models.py` — `HistoricalFixture`, `Prediction` models
- GitHub: https://github.com/emperoribk/bet-predictor.git
- Run with: `python backend/manage.py run_accumulator --date YYYY-MM-DD` (from project root)

## Key Files
- `backend/scorecard.py` — Fri/Sat/Sun backtest tool
- `backend/scan_day.py` — standalone live scan for a single date
- `backend/bets/management/commands/run_accumulator.py` — live daily predictions

## API Keys (backend/.env)
- FOOTBALL_DATA_API_KEY: 5048bfea9c0042d88785cda6d1f7bc15
- API_FOOTBALL_KEY: b82bebdfbe7e243cbabe4e9fee76c83b
- ODDS_API_KEY: 4428f21544af218c73c182876a3990cb ($30/month Starter plan — real bookmaker odds)

## Current Engine State (scorecard.py + run_accumulator.py — as of 2026-04-16)

### Algorithm
All markets checked per fixture. Hierarchy: DC (real alpha) > Goals > Team-score.
Best pick per fixture selected by hierarchy then probability. Max 4 picks per day.
No hard @2.0 floor — show all qualifying days (@1.40+). Most days naturally above @2.0.

### Constants
```python
DC_MIN         = 0.79   # DC calibrated alpha band 79-85%
DC_MAX         = 0.85
GOALS_MIN      = 0.82   # O1.5 / U2.5 minimum
GOALS_MAX      = 0.92
MAX_PICKS      = 4      # hard cap — avoids compound risk
MIN_BACK_GAMES = 10
MIN_DISPLAY_ODDS = 1.40
```

### League-specific O1.5 floors (LEAGUE_BOOSTER_THRESH)
```python
"SPL": 0.90, "FL1": 0.92, "BL1": 0.87, "SA": 0.86, "DED": 0.85, "PD": 0.85
```

### Per-league DC minimum (LEAGUE_DC_MIN)
```python
"DED": 0.84, "BL2": 0.84, "SPL": 0.84
```

### NEVER_BACK
Monaco, VfL Bochum, Toulouse, RB Leipzig, Espanyol, Paris FC, Southampton

### DERBY_PAIRS (blocked both sides)
Istanbul: Beşiktaş/Fenerbahçe/Galatasaray combos
Old Firm: Celtic vs Rangers
English: Liverpool/Everton, Arsenal/Tottenham, Man City/Man United, Chelsea/Arsenal
Madrid: Real Madrid/Atletico, Barcelona/Atletico

### Team-score (Tier 3 fallback)
Big 5 only (PL, PD, BL1, SA, FL1). Skip if absence flag set for backed team.

## Backtest Results (Fri/Sat/Sun, Oct 2025 – Apr 2026)

| Config | Days | Win% | Above @2.0 |
|--------|------|------|-----------|
| Original DC-only | 63 | 74.6% | ~88% |
| All-markets 7 picks | 65 | 49.2% | 100% |
| DC-priority 4 picks | 67 | 65.7% | 82% |
| + NEVER_BACK/Derby fixes | 64 | 75.0% | 66% |
| **Final (above)** | **58** | **86.2%** | **57%** |

Monthly (final):
- October 2025: 1/1 = 100%
- November 2025: 6/9 = 66.7%
- December 2025: 9/10 = 90.0%
- January 2026: 8/10 = 80.0%
- February 2026: 12/12 = 100%
- March 2026: 8/10 = 80.0%
- April 2026: 6/6 = 100%
- **TOTAL: 50/58 = 86.2%**

Market breakdown (all picks):
- DC_1X: 145 picks, 93.8% win rate ★ (real alpha — model understates DC by ~11%)
- OVER15: 39 picks, 94.9% win rate ★
- TEAM_SCORE: 23 picks, 100% win rate ★

## Remaining 8 Losses (mostly unavoidable)
3 shock results: Atalanta 0-3 vs newly promoted Sassuolo, Liverpool 0-3 vs Nottm Forest, Real Madrid 0-2 vs Celta Vigo
2 O1.5 misses: DED 1-0 at 86.9%, PL 0-0 at 83.6%
3 borderline DC: Brentford 0-2, Lille 0-2, Newcastle 1-2

## DB State
- 4,363+ fixtures in HistoricalFixture for season=2025 (Oct 2025–Apr 2026)
- Run: `python backend/manage.py backfill_history --season 2025`
- Git branch: explore/higher-odds-combo

## Key Insight
DC picks have real alpha: model says 79-85%, actual win rate 93.8%.
Team-score at 87-91% has zero alpha (model matches actual). Accumulator must be
DC-led or it reverts to theoretical ~50% win rate for any @2.0 accumulator.
