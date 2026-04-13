import os, sys, io, django
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")
django.setup()

# Import the cache so we can clear it between dates (otherwise teams loaded on
# the first date they appear get cached and reused for all subsequent dates,
# which means teams from leagues that start later always show 0 games played).
from bets.services import football_data as _fd

from bets.models import HistoricalFixture
from bets.services.football_data import get_fixtures_by_date, get_team_season_stats
from bets.services.daily_predictions import _match_probs, _score_probs, _expected_xg_for_match, _poisson

MIN_PROB = 0.54
MAX_PROB = 0.82
TARGET_ODDS = 2.0
MAX_PICKS = 3

TARGET_CODES = {
    "PL","PD","BL1","SA","FL1","DED","PPL","ELC","SPL","BJL","TSL","BL2","SSL"
}

def run_date(target_date):
    try:
        fixtures = get_fixtures_by_date(target_date)
    except Exception:
        return []

    league_fixtures = [f for f in fixtures if f.get("competition_code") in TARGET_CODES]
    candidates = []

    for fix in league_fixtures:
        home_name = fix["home_team"]
        away_name = fix["away_team"]
        comp_code = fix.get("competition_code", "PL")
        try:
            home_stats = get_team_season_stats(
                team_id=fix.get("home_team_id", 0), team_name=home_name,
                competition_code=comp_code, match_date=target_date)
            away_stats = get_team_season_stats(
                team_id=fix.get("away_team_id", 0), team_name=away_name,
                competition_code=comp_code, match_date=target_date)
            if not home_stats or not away_stats:
                continue

            min_games = min(home_stats.get("played", 0) or 0, away_stats.get("played", 0) or 0)
            if min_games < 3:
                continue

            h_xg, a_xg = _expected_xg_for_match(home_stats, away_stats)
            if h_xg <= 0 or a_xg <= 0:
                continue

            p_home, p_draw, p_away = _match_probs(h_xg, a_xg)
            p_00 = _poisson(h_xg, 0) * _poisson(a_xg, 0)

            markets = {
                "DC_1X": (p_home + p_draw, f"{home_name} or Draw (1X)"),
                "DC_X2": (p_draw + p_away, f"Draw or {away_name} (X2)"),
            }

            valid = [(mkt, prob, label) for mkt, (prob, label) in markets.items()
                     if MIN_PROB <= prob <= MAX_PROB]
            if not valid:
                continue

            priority = {"DC_1X": 0, "DC_X2": 1}
            valid.sort(key=lambda x: (-x[1], priority.get(x[0], 99)))
            mkt, prob, label = valid[0]

            home_score = fix.get("home_score")
            away_score = fix.get("away_score")
            won = None
            score_str = "?"
            if home_score is not None and away_score is not None:
                score_str = f"{home_score}-{away_score}"
                if mkt == "DC_1X":
                    won = home_score >= away_score
                elif mkt == "DC_X2":
                    won = away_score >= home_score

            candidates.append({
                "fixture": f"{home_name} vs {away_name}",
                "label": label,
                "market": mkt,
                "probability": round(prob, 4),
                "odds": round(1 / prob, 2),
                "won": won,
                "score": score_str,
            })
        except Exception:
            continue

    if not candidates:
        return []

    candidates.sort(key=lambda x: -x["probability"])
    accumulator = []
    combined = 1.0
    for pick in candidates:
        if combined >= TARGET_ODDS or len(accumulator) >= MAX_PICKS:
            break
        accumulator.append(pick)
        combined *= pick["odds"]

    return accumulator, combined


# Only scan dates that have domestic league fixtures (skip pure CL/EL/UECL nights)
from bets.models import HistoricalFixture
dates = list(
    HistoricalFixture.objects.filter(
        home_score__isnull=False,
        league_code__in=list(TARGET_CODES),
    )
    .values_list("match_date", flat=True)
    .distinct()
    .order_by("match_date")
)
dates = [str(d) for d in dates]

print(f"Scanning {len(dates)} dates for perfect days...\n")
sys.stdout.flush()

perfect_days = []
for d in dates:
    _fd._season_stats_cache.clear()   # ensure per-date fresh stats
    result = run_date(d)
    if not result:
        continue
    acc, combined = result
    if not acc:
        continue
    all_won = all(p.get("won") is True for p in acc)
    any_loss = any(p.get("won") is False for p in acc)
    if all_won:
        perfect_days.append((d, acc, combined))

print(f"Found {len(perfect_days)} perfect days (all picks correct)\n")
print("=" * 80)

current_month = None
month_count = 0

for d, acc, combined in perfect_days:
    month = d[:7]  # YYYY-MM
    if month != current_month:
        if current_month is not None:
            print(f"\n  [{month_count} perfect days in {current_month}]\n")
        current_month = month
        month_count = 0
        month_label = {
            "2025-09": "SEPTEMBER 2025", "2025-10": "OCTOBER 2025",
            "2025-11": "NOVEMBER 2025", "2025-12": "DECEMBER 2025",
            "2026-01": "JANUARY 2026",  "2026-02": "FEBRUARY 2026",
            "2026-03": "MARCH 2026",    "2026-04": "APRIL 2026",
        }.get(month, month)
        print(f"\n{'-'*80}")
        print(f"  {month_label}")
        print(f"{'-'*80}")

    month_count += 1
    odds_tag = f"@{combined:.2f}" + (" [>=2.0]" if combined >= TARGET_ODDS else " [<2.0]")
    print(f"\n  {d}  ALL WIN  {odds_tag}  ({len(acc)} picks)")
    for p in acc:
        print(f"    [W] {p['fixture']}")
        print(f"         {p['label']}  |  Score: {p['score']}  |  Prob: {p['probability']:.1%}  Odds: @{p['odds']:.2f}")

if current_month is not None:
    print(f"\n  [{month_count} perfect days in {current_month}]\n")

print("\n" + "="*80)
above = sum(1 for _, _, c in perfect_days if c >= TARGET_ODDS)
below = len(perfect_days) - above
print(f"  TOTAL PERFECT DAYS : {len(perfect_days)}")
print(f"  >= 2.0 combined    : {above}")
print(f"  < 2.0 combined     : {below}")
print("="*80 + "\n")
