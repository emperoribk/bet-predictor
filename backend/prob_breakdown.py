import os, sys, io, django
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")
django.setup()

from bets.services import football_data as _fd
from bets.services.football_data import get_fixtures_by_date, _get_team_stats_from_db
from bets.services.daily_predictions import _match_probs, _expected_xg_for_match
from bets.models import HistoricalFixture
from datetime import datetime
import math

MIN_PROB       = 0.72
MAX_PROB       = 0.82
BOOSTER_THRESH = 0.77
TARGET_ODDS    = 2.0
MAX_PICKS      = 3
MIN_BACK_GAMES = 10

TARGET_CODES = {
    "PL","PD","BL1","SA","FL1","DED","PPL","ELC","SPL","BJL","TSL","BL2"
}
DERBY_PAIRS = {
    frozenset({"Besiktas", "Fenerbahce"}),
    frozenset({"Besiktas", "Galatasaray"}),
    frozenset({"Fenerbahce", "Galatasaray"}),
}
NEVER_BACK = {"Monaco"}

def get_team_season_stats(team_id, team_name, competition_code, match_date):
    if not team_id:
        return None
    import time as _t
    now = _t.time()
    cache_key = (team_id, competition_code)
    cached = _fd._season_stats_cache.get(cache_key)
    if cached:
        ts, data = cached
        if now - ts < 6 * 3600:
            return data
    try:
        result = _get_team_stats_from_db(
            team_id=team_id, team_name=team_name,
            competition_code=competition_code,
            match_date_str=match_date, season=2025,
        )
    except Exception:
        result = None
    if result is not None:
        _fd._season_stats_cache[cache_key] = (now, result)
    return result

def _over15_prob(h_xg, a_xg):
    p_0 = math.exp(-h_xg) * math.exp(-a_xg)
    p_1 = (math.exp(-h_xg) * h_xg * math.exp(-a_xg) +
           math.exp(-h_xg) * math.exp(-a_xg) * a_xg)
    return 1 - p_0 - p_1

def run_date(target_date):
    try:
        fixtures = get_fixtures_by_date(target_date)
    except Exception:
        return None
    league_fixtures = [f for f in fixtures if f.get("competition_code") in TARGET_CODES]
    candidates = []
    booster_candidates = []
    for fix in league_fixtures:
        home_name = fix["home_team"]
        away_name = fix["away_team"]
        comp_code = fix.get("competition_code", "PL")
        try:
            if frozenset({home_name, away_name}) in DERBY_PAIRS:
                continue
            hs = get_team_season_stats(fix.get("home_team_id", 0), home_name, comp_code, target_date)
            aws = get_team_season_stats(fix.get("away_team_id", 0), away_name, comp_code, target_date)
            if not hs or not aws:
                continue
            h_games = hs.get("played", 0) or 0
            a_games = aws.get("played", 0) or 0
            min_games = min(h_games, a_games)
            if min_games < 3:
                continue
            h_xg, a_xg = _expected_xg_for_match(hs, aws)
            if h_xg <= 0 or a_xg <= 0:
                continue
            p_home, p_draw, p_away = _match_probs(h_xg, a_xg)
            p_o15 = _over15_prob(h_xg, a_xg)

            if p_o15 >= BOOSTER_THRESH and min_games >= MIN_BACK_GAMES:
                home_score = fix.get("home_score")
                away_score = fix.get("away_score")
                o15_won = None
                score_str = "?"
                if home_score is not None and away_score is not None:
                    score_str = f"{home_score}-{away_score}"
                    o15_won = (home_score + away_score) >= 2
                booster_candidates.append({
                    "fixture": f"{home_name} vs {away_name}",
                    "market": "OVER15",
                    "label": "Over 1.5 Goals",
                    "probability": round(p_o15, 4),
                    "odds": round(1 / p_o15, 2),
                    "won": o15_won,
                    "score": score_str,
                    "league": comp_code,
                })

            markets = {}
            if h_games >= MIN_BACK_GAMES and home_name not in NEVER_BACK:
                markets["DC_1X"] = (p_home + p_draw, f"{home_name} or Draw (1X)")
            if a_games >= MIN_BACK_GAMES and away_name not in NEVER_BACK:
                markets["DC_X2"] = (p_draw + p_away, f"Draw or {away_name} (X2)")

            valid = [(mkt, prob, label) for mkt, (prob, label) in markets.items()
                     if MIN_PROB <= prob <= MAX_PROB]
            if not valid:
                continue
            valid.sort(key=lambda x: (-x[1], {"DC_1X": 0, "DC_X2": 1}.get(x[0], 99)))
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
                "market": mkt,
                "label": label,
                "probability": round(prob, 4),
                "odds": round(1 / prob, 2),
                "won": won,
                "score": score_str,
                "league": comp_code,
            })
        except Exception:
            continue

    if not candidates:
        return None

    candidates.sort(key=lambda x: -x["probability"])
    accumulator = []
    combined = 1.0
    used_fixtures = set()
    for pick in candidates:
        if combined >= TARGET_ODDS or len(accumulator) >= MAX_PICKS:
            break
        accumulator.append(pick)
        combined *= pick["odds"]
        used_fixtures.add(pick["fixture"])

    if combined < TARGET_ODDS and len(accumulator) < MAX_PICKS and booster_candidates:
        booster_candidates.sort(key=lambda x: -x["probability"])
        for b in booster_candidates:
            if b["fixture"] not in used_fixtures:
                accumulator.append(b)
                combined *= b["odds"]
                break

    return accumulator, combined


dates = list(
    HistoricalFixture.objects.filter(
        home_score__isnull=False,
        league_code__in=list(TARGET_CODES),
        match_date__gte="2025-10-01",
        match_date__lte="2026-03-31",
    )
    .values_list("match_date", flat=True)
    .distinct()
    .order_by("match_date")
)
dates = [str(d) for d in dates if d.weekday() in (4, 5, 6)]

sub2_picks = []   # (prob, won, date, fixture, market)
over2_picks = []

for d in dates:
    _fd._season_stats_cache.clear()
    result = run_date(d)
    if not result:
        continue
    acc, combined = result
    if not acc:
        continue
    for pick in acc:
        entry = (pick["probability"], pick.get("won"), d, pick["fixture"], pick["market"], pick["odds"], pick["score"])
        if combined < TARGET_ODDS:
            sub2_picks.append(entry)
        else:
            over2_picks.append(entry)

def print_picks(picks, header):
    print(f"\n{'='*75}")
    print(f"  {header}  ({len(picks)} picks across {len(set(p[2] for p in picks))} days)")
    print(f"{'='*75}")
    print(f"  {'Prob':>6}  {'Odds':>5}  {'W/L':>3}  {'Score':>5}  {'Mkt':>6}  Fixture")
    print(f"  {'-'*65}")
    for prob, won, d, fixture, mkt, odds, score in sorted(picks, key=lambda x: -x[0]):
        icon = "W" if won is True else "L" if won is False else "?"
        print(f"  {prob:.1%}  @{odds:<4}  {icon:>3}  {score:>5}  {mkt:>6}  {fixture}")

    # Probability distribution buckets
    print(f"\n  Probability buckets:")
    buckets = [(0.72,0.74),(0.74,0.76),(0.76,0.78),(0.78,0.80),(0.80,0.82),(0.82,1.0)]
    for lo, hi in buckets:
        subset = [p for p in picks if lo <= p[0] < hi]
        if not subset:
            continue
        wins = sum(1 for p in subset if p[1] is True)
        losses = sum(1 for p in subset if p[1] is False)
        pct = wins / len(subset) * 100 if subset else 0
        label = f"{lo:.0%}-{hi:.0%}"
        bar = "W"*wins + "L"*losses
        print(f"    {label}  {len(subset):>3} picks  {wins}W {losses}L  {pct:.0f}%  {bar}")

print_picks(sub2_picks,  "SUB-2.0 DAYS — individual pick probabilities")
print_picks(over2_picks, "OVER-2.0 DAYS — individual pick probabilities")

# Overall pick-level win rates by prob bucket across all picks
all_picks = sub2_picks + over2_picks
print(f"\n\n{'='*75}")
print(f"  ALL PICKS COMBINED — win rate by probability bucket")
print(f"{'='*75}")
print(f"  {'Range':>10}  {'Picks':>5}  {'Wins':>5}  {'Loss':>5}  {'Win%':>6}")
print(f"  {'-'*45}")
buckets = [(0.72,0.74),(0.74,0.76),(0.76,0.78),(0.78,0.80),(0.80,0.82),(0.82,1.0)]
for lo, hi in buckets:
    subset = [p for p in all_picks if lo <= p[0] < hi]
    if not subset:
        continue
    wins = sum(1 for p in subset if p[1] is True)
    losses = sum(1 for p in subset if p[1] is False)
    unknowns = sum(1 for p in subset if p[1] is None)
    pct = wins / (wins + losses) * 100 if (wins + losses) else 0
    label = f"{lo:.0%}–{hi:.0%}"
    print(f"  {label:>10}  {len(subset):>5}  {wins:>5}  {losses:>5}  {pct:>5.0f}%")

print(f"\n{'='*75}\n")
