import os, sys, io, django
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")
django.setup()

import math
from bets.services import football_data as _fd
from bets.services.football_data import get_fixtures_by_date, _get_team_stats_from_db
from bets.services.daily_predictions import _match_probs, _expected_xg_for_match
from bets.models import HistoricalFixture
from datetime import datetime

MIN_PROB       = 0.54
MAX_PROB       = 0.82
TARGET_ODDS    = 2.0
MAX_PICKS      = 3
MIN_BACK_GAMES = 10
BOOSTER_THRESH = 0.77

TARGET_CODES = {
    "PL","PD","BL1","SA","FL1","DED","PPL","ELC","SPL","BJL","TSL","BL2"
}
DERBY_PAIRS = {
    frozenset({"Beşiktaş", "Fenerbahçe"}),
    frozenset({"Beşiktaş", "Galatasaray"}),
    frozenset({"Fenerbahçe", "Galatasaray"}),
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
        return []
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
            if min(h_games, a_games) < 3:
                continue
            h_xg, a_xg = _expected_xg_for_match(hs, aws)
            if h_xg <= 0 or a_xg <= 0:
                continue
            p_home, p_draw, p_away = _match_probs(h_xg, a_xg)
            p_o15 = _over15_prob(h_xg, a_xg)

            home_score = fix.get("home_score")
            away_score = fix.get("away_score")

            # Over 1.5 booster pool
            if p_o15 >= BOOSTER_THRESH and min(h_games, a_games) >= MIN_BACK_GAMES:
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
                    "o15_prob": round(p_o15, 4),
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
                "o15_prob": round(p_o15, 4),
                "home_score": home_score,
                "away_score": away_score,
            })
        except Exception:
            continue

    if not candidates:
        return []

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


# ── Only Jan–Mar 2026 ─────────────────────────────────────────────────────────
dates = list(
    HistoricalFixture.objects.filter(
        home_score__isnull=False,
        league_code__in=list(TARGET_CODES),
        match_date__gte="2026-01-01",
        match_date__lte="2026-03-31",
    )
    .values_list("match_date", flat=True)
    .distinct()
    .order_by("match_date")
)
dates = [str(d) for d in dates if d.weekday() in (4, 5, 6)]
print(f"Scanning {len(dates)} Fri/Sat/Sun dates (Jan–Mar 2026)...\n")

MONTH_NAMES = {
    "2026-01": "JANUARY 2026",
    "2026-02": "FEBRUARY 2026",
    "2026-03": "MARCH 2026",
}

losses = []

for d in dates:
    _fd._season_stats_cache.clear()
    result = run_date(d)
    if not result:
        continue
    acc, combined = result
    for pick in acc:
        if pick.get("won") is False:
            losses.append((d, pick))

print(f"{'='*90}")
print(f"  LOSS ANALYSIS — Jan to Mar 2026")
print(f"  For each losing pick: was Over 1.5 higher probability AND would it have won?")
print(f"{'='*90}\n")

current_month = None
swap_saves = 0
low_scoring_losses = 0
o15_lower_losses = 0

for d, pick in losses:
    month = d[:7]
    if month != current_month:
        current_month = month
        print(f"\n  --- {MONTH_NAMES.get(month, month)} ---\n")

    hs = pick.get("home_score") or 0
    aws = pick.get("away_score") or 0
    total = hs + aws
    o15_prob = pick["o15_prob"]
    dc_prob  = pick["probability"]
    o15_wins = total >= 2
    o15_higher = o15_prob > dc_prob

    verdict = ""
    if o15_wins and o15_higher:
        verdict = "  ✓ SWAP → O1.5 would WIN and had higher prob"
        swap_saves += 1
    elif o15_wins and not o15_higher:
        verdict = "  ~ O1.5 would win but DC prob was higher (model preferred DC)"
        o15_lower_losses += 1
    else:
        verdict = "  ✗ Low-scoring game — O1.5 also loses"
        low_scoring_losses += 1

    dc_label = pick.get("label") or pick.get("market", "")
    print(f"  {d}  [{pick['league']:4}]  {pick['fixture']}")
    print(f"    DC pick : {dc_label}  |  DC prob: {dc_prob:.1%}  |  Score: {pick['score']}  ({total} goals)")
    print(f"    O1.5 prob: {o15_prob:.1%}  |  O1.5 odds: @{round(1/o15_prob,2):.2f}  |  O1.5 result: {'WIN' if o15_wins else 'LOSS'}")
    print(f"   {verdict}\n")

print(f"\n{'='*90}")
print(f"  SUMMARY — {len(losses)} losses in Jan–Mar 2026")
print(f"{'='*90}")
print(f"  ✓ Swap DC → O1.5 would SAVE the pick   : {swap_saves}  ({swap_saves/len(losses)*100:.0f}% of losses)")
print(f"  ~ O1.5 wins but model preferred DC      : {o15_lower_losses}")
print(f"  ✗ Low-scoring game, O1.5 also loses     : {low_scoring_losses}")
print(f"\n  Conclusion: replacing DC with O1.5 on same fixture when O1.5 prob > DC prob")
print(f"  would have rescued {swap_saves} out of {len(losses)} losing picks ({swap_saves/len(losses)*100:.0f}%)")
print(f"{'='*90}\n")
