import os, sys, io, django
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")
django.setup()

import math
from collections import defaultdict
from bets.models import HistoricalFixture
from bets.services import football_data as _fd
from bets.services.football_data import _get_team_stats_from_db
from bets.services.daily_predictions import _match_probs, _expected_xg_for_match, _poisson

TARGET_CODES = {
    "PL","PD","BL1","SA","FL1","DED","PPL","ELC","SPL","BJL","TSL","BL2"
}

def poisson_prob(lam, k):
    return (lam**k * math.exp(-lam)) / math.factorial(k)

def market_probs(h_xg, a_xg):
    p_home, p_draw, p_away = _match_probs(h_xg, a_xg)

    # Goal total probs via Poisson
    max_g = 8
    p_over15 = 0.0
    p_over25 = 0.0
    p_over35 = 0.0
    p_under15 = 0.0
    p_under25 = 0.0
    p_btts    = 0.0

    for hg in range(max_g + 1):
        for ag in range(max_g + 1):
            p = poisson_prob(h_xg, hg) * poisson_prob(a_xg, ag)
            total = hg + ag
            if total >= 2: p_over15 += p
            if total >= 3: p_over25 += p
            if total >= 4: p_over35 += p
            if total <= 1: p_under15 += p
            if total <= 2: p_under25 += p
            if hg >= 1 and ag >= 1: p_btts += p

    p_under35 = 1.0 - p_over35  # Under 3.5 = not Over 3.5

    return {
        "Home Win":     p_home,
        "Away Win":     p_away,
        "Draw":         p_draw,
        "DC 1X":        p_home + p_draw,
        "DC X2":        p_draw + p_away,
        "Over 1.5":     p_over15,
        "Over 2.5":     p_over25,
        "Over 3.5":     p_over35,
        "Under 1.5":    p_under15,
        "Under 2.5":    p_under25,
        "Under 3.5":    p_under35,
        "BTTS":         p_btts,
    }

def actual_results(hs, as_):
    total = hs + as_
    return {
        "Home Win":     hs > as_,
        "Away Win":     as_ > hs,
        "Draw":         hs == as_,
        "DC 1X":        hs >= as_,
        "DC X2":        as_ >= hs,
        "Over 1.5":     total >= 2,
        "Over 2.5":     total >= 3,
        "Over 3.5":     total >= 4,
        "Under 1.5":    total <= 1,
        "Under 2.5":    total <= 2,
        "Under 3.5":    total <= 3,
        "BTTS":         hs >= 1 and as_ >= 1,
    }

# Pull Jan–Mar 2026 fixtures only — model has enough data by this point,
# thresholds from this window reflect real-season accuracy not early-season noise
print("Loading fixtures from DB (Jan–Mar 2026)...")
fixtures = list(
    HistoricalFixture.objects.filter(
        season=2025,
        home_score__isnull=False,
        away_score__isnull=False,
        league_code__in=list(TARGET_CODES),
        match_date__gte="2026-01-01",
        match_date__lte="2026-03-31",
    ).values(
        "fixture_id","home_team_id","away_team_id",
        "home_team","away_team","league_code",
        "match_date","home_score","away_score",
    ).order_by("match_date")
)
print(f"  {len(fixtures)} fixtures found\n")

# For each fixture compute model probs and actual outcome
# bucket: market -> list of (predicted_prob, correct: bool)
market_data = defaultdict(list)

processed = 0
skipped = 0
prev_date = None

for fx in fixtures:
    d = str(fx["match_date"])
    if d != prev_date:
        _fd._season_stats_cache.clear()
        prev_date = d

    try:
        hs_stats = _get_team_stats_from_db(
            team_id=fx["home_team_id"], team_name=fx["home_team"],
            competition_code=fx["league_code"], match_date_str=d, season=2025,
        )
        as_stats = _get_team_stats_from_db(
            team_id=fx["away_team_id"], team_name=fx["away_team"],
            competition_code=fx["league_code"], match_date_str=d, season=2025,
        )
        if not hs_stats or not as_stats:
            skipped += 1
            continue
        if min(hs_stats.get("played",0) or 0, as_stats.get("played",0) or 0) < 3:
            skipped += 1
            continue

        h_xg, a_xg = _expected_xg_for_match(hs_stats, as_stats)
        if h_xg <= 0 or a_xg <= 0:
            skipped += 1
            continue

        probs   = market_probs(h_xg, a_xg)
        actuals = actual_results(fx["home_score"], fx["away_score"])

        for mkt, prob in probs.items():
            market_data[mkt].append((prob, actuals[mkt]))

        processed += 1
    except Exception:
        skipped += 1
        continue

print(f"Processed: {processed}  |  Skipped: {skipped}\n")

# ── Analysis: for each market find threshold where accuracy = 80-85% ──────────
MARKETS = [
    "DC 1X", "DC X2", "Over 1.5", "Over 2.5",
    "Under 3.5", "Under 2.5", "Home Win", "Away Win",
    "BTTS", "Over 3.5", "Under 1.5",
]

print(f"{'='*80}")
print(f"  MARKET CALIBRATION — threshold for 80-85% accuracy")
print(f"  (how confident the model needs to be before a pick is reliable)")
print(f"{'='*80}\n")

for mkt in MARKETS:
    data = market_data[mkt]
    if not data:
        continue

    # Sort by prob descending, compute rolling accuracy at each threshold
    data.sort(key=lambda x: -x[0])

    # Try thresholds from 50% to 95% in 1% steps
    print(f"\n  {mkt}")
    print(f"  {'Threshold':>10}  {'Picks':>7}  {'Correct':>8}  {'Accuracy':>9}  {'Coverage':>9}")
    print(f"  {'-'*52}")

    prev_acc = None
    target_row = None
    for thresh_pct in range(50, 96):
        thresh = thresh_pct / 100
        subset = [(p, c) for p, c in data if p >= thresh]
        if len(subset) < 20:
            break
        correct = sum(1 for _, c in subset if c)
        acc = correct / len(subset)
        total = len(data)
        coverage = len(subset) / total * 100

        # Print every 5% and the crossover point
        if thresh_pct % 5 == 0 or (prev_acc and prev_acc < 0.80 and acc >= 0.80):
            marker = " <<< TARGET" if 0.80 <= acc <= 0.87 else ""
            print(f"  {thresh_pct:>9}%  {len(subset):>7}  {correct:>8}  {acc:>8.1%}  {coverage:>8.1f}%{marker}")
            if 0.80 <= acc <= 0.87 and target_row is None:
                target_row = (thresh_pct, len(subset), acc, coverage)

        prev_acc = acc

    if target_row:
        t, n, a, cov = target_row
        print(f"\n  --> Best threshold: {t}%  |  {n} picks  |  {a:.1%} accuracy  |  covers {cov:.1f}% of all games")
    else:
        print(f"\n  --> No threshold found in range achieving 80-85%")

print(f"\n{'='*80}\n")
