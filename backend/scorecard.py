import os, sys, io, django
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")
django.setup()

from bets.services import football_data as _fd
from bets.services.football_data import get_fixtures_by_date, _get_team_stats_from_db, key_player_absence_factor
from bets.services.daily_predictions import _match_probs, _expected_xg_for_match, _poisson
from bets.models import HistoricalFixture
from datetime import datetime, timedelta
from collections import defaultdict

# DB-only wrapper — never falls through to live Understat/API during backtest
def get_team_season_stats(team_id, team_name, competition_code, match_date):
    if not team_id:
        return None
    import time as _time
    now = _time.time()
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

MIN_PROB       = 0.79   # base floor — only back picks we're genuinely confident in
MIN_PROB_HIGH  = 0.79   # stricter floor applied only when day ends up over 2.0
MAX_PROB       = 0.82   # above this odds too short — 83%+ DC picks lose more than expected
BOOSTER_THRESH = 0.77   # Over 1.5 at 77%+ wins 80%+ of the time (calibrated)
TIER2_MIN_PROB = 0.79   # Tier 2: dominant team to score / Over 1.5 floor
TARGET_ODDS = 2.0
MAX_PICKS   = 3

TARGET_CODES = {
    "PL","PD","BL1","SA","FL1","DED","PPL","ELC","SPL","BJL","TSL","BL2"
}  # SSL removed — Swiss league too volatile, model lacks reliable data

# Big 5 preferred over smaller leagues when probabilities are similar
LEAGUE_PRIORITY = {
    "PL": 1, "PD": 1, "BL1": 1, "SA": 1, "FL1": 1,   # Big 5 — most data, most reliable
    "DED": 2, "PPL": 2, "ELC": 2,                       # Strong second tier
    "BL2": 3, "SPL": 3, "TSL": 3, "BJL": 3,             # Third tier — higher upset rate
}

# Istanbul derby — uniquely volatile, form irrelevant, skip both sides.
# Other derbies (El Clasico, Old Firm etc.) can still produce good picks.
DERBY_PAIRS = {
    frozenset({"Beşiktaş", "Fenerbahçe"}),
    frozenset({"Beşiktaş", "Galatasaray"}),
    frozenset({"Fenerbahçe", "Galatasaray"}),
}

# Teams too unpredictable to back regardless of probability.
# Monaco: wildly inconsistent — beat PSG at home, lost to Lorient/Lens as favourites.
# We can still back their opponents; just never back Monaco themselves.
NEVER_BACK = {"Monaco"}

def _over15_prob(h_xg, a_xg):
    """P(total goals >= 2) via Poisson."""
    import math
    p_0 = math.exp(-h_xg) * math.exp(-a_xg)
    p_1 = math.exp(-h_xg) * h_xg * math.exp(-a_xg) + math.exp(-h_xg) * math.exp(-a_xg) * a_xg
    return 1 - p_0 - p_1


def _team_score_prob(xg):
    """P(team scores >= 1 goal) via Poisson."""
    import math
    return 1 - math.exp(-xg)


def _build_accumulator(fixtures_data, min_prob):
    """
    Build accumulator from DC picks.
    If sub-2.0 with room, add Over 1.5 booster from a different fixture.
    """
    candidates         = []
    booster_candidates = []

    for item in fixtures_data:
        if item["booster"] and item["p_o15"] >= BOOSTER_THRESH and item["min_games"] >= 10:
            booster_candidates.append(item["booster"])
        if item["pick"] and min_prob <= item["prob"] <= MAX_PROB:
            candidates.append(item["pick"])

    if not candidates:
        return None, None, booster_candidates

    candidates.sort(key=lambda x: -x["probability"])
    accumulator   = []
    combined      = 1.0
    used_fixtures = set()

    for pick in candidates:
        if combined >= TARGET_ODDS or len(accumulator) >= MAX_PICKS:
            break
        accumulator.append(pick)
        combined *= pick["odds"]
        used_fixtures.add(pick["fixture"])

    # Sub-2.0 with room: add best Over 1.5 booster from a different fixture
    if combined < TARGET_ODDS and len(accumulator) < MAX_PICKS and booster_candidates:
        booster_candidates.sort(key=lambda x: -x["probability"])
        for b in booster_candidates:
            if b["fixture"] not in used_fixtures:
                accumulator.append(b)
                combined *= b["odds"]
                break

    return accumulator, combined, booster_candidates


def _build_tier2_accumulator(fixtures_data):
    """
    Tier 2 fallback: used when no DC picks qualify.
    Per fixture: pick the single best qualifying pick —
      dominant team to score (higher-xG side, >= TIER2_MIN_PROB) OR
      Over 1.5 Goals (>= TIER2_MIN_PROB), whichever probability is higher.
    Returns top-3 picks by probability, one per fixture.
    """
    candidates = []
    for item in fixtures_data:
        h_xg      = item.get("h_xg", 0)
        a_xg      = item.get("a_xg", 0)
        p_o15     = item.get("p_o15", 0)
        min_games = item.get("min_games", 0)
        if min_games < 10:
            continue

        fix_str    = item.get("fixture_str", "")
        comp_code  = item.get("comp_code", "")
        home_name  = item.get("home_name", "")
        away_name  = item.get("away_name", "")
        home_score = item.get("home_score")
        away_score = item.get("away_score")
        score_str  = item.get("score_str", "?")

        best_pick = None
        best_prob = TIER2_MIN_PROB - 0.0001  # threshold sentinel

        # Dominant team to score: side with higher xG
        if h_xg >= a_xg:
            p_ts = _team_score_prob(h_xg)
            if p_ts >= TIER2_MIN_PROB and home_name not in NEVER_BACK:
                won = (home_score >= 1) if home_score is not None else None
                best_pick = {
                    "fixture": fix_str, "label": f"{home_name} to Score",
                    "market": "TEAM_SCORE", "probability": round(p_ts, 4),
                    "odds": round(1 / p_ts, 2), "won": won,
                    "score": score_str, "league": comp_code,
                }
                best_prob = p_ts
        else:
            p_ts = _team_score_prob(a_xg)
            if p_ts >= TIER2_MIN_PROB and away_name not in NEVER_BACK:
                won = (away_score >= 1) if away_score is not None else None
                best_pick = {
                    "fixture": fix_str, "label": f"{away_name} to Score",
                    "market": "TEAM_SCORE", "probability": round(p_ts, 4),
                    "odds": round(1 / p_ts, 2), "won": won,
                    "score": score_str, "league": comp_code,
                }
                best_prob = p_ts

        # Over 1.5: replace team-score pick only if it has higher probability
        if p_o15 >= TIER2_MIN_PROB and p_o15 > best_prob:
            won = ((home_score + away_score) >= 2) if home_score is not None else None
            best_pick = {
                "fixture": fix_str, "label": "Over 1.5 Goals",
                "market": "OVER15", "probability": round(p_o15, 4),
                "odds": round(1 / p_o15, 2), "won": won,
                "score": score_str, "league": comp_code,
            }

        if best_pick:
            candidates.append(best_pick)

    if not candidates:
        return None, None

    candidates.sort(key=lambda x: -x["probability"])
    accumulator = candidates[:MAX_PICKS]
    combined    = 1.0
    for p in accumulator:
        combined *= p["odds"]
    return accumulator, round(combined, 4)


def run_date(target_date):
    try:
        fixtures = get_fixtures_by_date(target_date)
    except Exception:
        return []
    league_fixtures = [f for f in fixtures if f.get("competition_code") in TARGET_CODES]

    fixtures_data  = []
    MIN_BACK_GAMES = 10

    for fix in league_fixtures:
        home_name = fix["home_team"]
        away_name = fix["away_team"]
        comp_code = fix.get("competition_code", "PL")
        try:
            if frozenset({home_name, away_name}) in DERBY_PAIRS:
                continue

            hs = get_team_season_stats(team_id=fix.get("home_team_id", 0), team_name=home_name,
                                       competition_code=comp_code, match_date=target_date)
            aws = get_team_season_stats(team_id=fix.get("away_team_id", 0), team_name=away_name,
                                        competition_code=comp_code, match_date=target_date)
            if not hs or not aws:
                continue

            h_games   = hs.get("played", 0) or 0
            a_games   = aws.get("played", 0) or 0
            min_games = min(h_games, a_games)
            if min_games < 3:
                continue

            h_xg, a_xg = _expected_xg_for_match(hs, aws)
            if h_xg <= 0 or a_xg <= 0:
                continue

            # Key player absence check — flag picks where star scorer has been missing
            h_factor, h_flag = key_player_absence_factor(home_name, target_date)
            a_factor, a_flag = key_player_absence_factor(away_name, target_date)

            p_home, p_draw, p_away = _match_probs(h_xg, a_xg)
            p_o15 = _over15_prob(h_xg, a_xg)

            home_score  = fix.get("home_score")
            away_score  = fix.get("away_score")
            fixture_str = f"{home_name} vs {away_name}"
            score_str   = (f"{home_score}-{away_score}"
                           if home_score is not None and away_score is not None else "?")

            booster = None
            if p_o15 >= BOOSTER_THRESH and min_games >= 10:
                o15_won = None
                if home_score is not None and away_score is not None:
                    o15_won = (home_score + away_score) >= 2
                booster = {"fixture": fixture_str, "label": "Over 1.5 Goals",
                           "market": "OVER15", "probability": round(p_o15, 4),
                           "odds": round(1 / p_o15, 2), "won": o15_won,
                           "score": score_str, "league": comp_code}

            # Raw fields used by Tier 2 (team-score / Over 1.5 fallback)
            raw = {
                "h_xg": round(h_xg, 2), "a_xg": round(a_xg, 2),
                "home_name": home_name, "away_name": away_name,
                "home_score": home_score, "away_score": away_score,
                "fixture_str": fixture_str, "comp_code": comp_code,
                "score_str": score_str,
            }

            markets = {}
            if h_games >= MIN_BACK_GAMES and home_name not in NEVER_BACK:
                markets["DC_1X"] = (p_home + p_draw, f"{home_name} or Draw (1X)")
            if a_games >= MIN_BACK_GAMES and away_name not in NEVER_BACK:
                markets["DC_X2"] = (p_draw + p_away, f"Draw or {away_name} (X2)")

            if not markets:
                fixtures_data.append({"p_o15": p_o15, "min_games": min_games,
                                      "booster": booster, "prob": -1, "pick": None, **raw})
                continue

            best = sorted(markets.items(),
                          key=lambda x: (-x[1][0], {"DC_1X": 0, "DC_X2": 1}.get(x[0], 99)))[0]
            mkt, (prob, label) = best

            won = None
            if home_score is not None and away_score is not None:
                if mkt == "DC_1X":  won = home_score >= away_score
                elif mkt == "DC_X2": won = away_score >= home_score

            # Attach absence flag if the backed team's key player was out
            absence_flag = h_flag if mkt == "DC_1X" else a_flag

            pick = {"fixture": fixture_str, "label": label, "market": mkt,
                    "probability": round(prob, 4), "odds": round(1 / prob, 2),
                    "won": won, "score": score_str, "league": comp_code,
                    "absence_flag": absence_flag}
            fixtures_data.append({"p_o15": p_o15, "min_games": min_games,
                                   "booster": booster, "prob": round(prob, 4), "pick": pick,
                                   **raw})
        except Exception:
            continue

    if not fixtures_data:
        return []

    # ── Tier 1: DC accumulator ────────────────────────────────────────────────
    accumulator, combined, _ = _build_accumulator(fixtures_data, MIN_PROB)
    if accumulator and combined >= TARGET_ODDS:
        # Second pass: enforce stricter floor on over-2.0 days
        acc2, comb2, _ = _build_accumulator(fixtures_data, MIN_PROB_HIGH)
        if acc2:
            accumulator, combined = acc2, comb2
        else:
            accumulator = None  # weak picks on high-odds day — fall to Tier 2

    if accumulator:
        return accumulator, combined, 1

    # ── Tier 2: dominant team to score + Over 1.5 (days with no DC picks) ────
    acc2, comb2 = _build_tier2_accumulator(fixtures_data)
    if acc2:
        return acc2, comb2, 2

    return []


# --- collect dates --- Jan–Mar 2026 only (Oct–Dec excluded until 2024/25 data loaded)
dates = list(
    HistoricalFixture.objects.filter(
        home_score__isnull=False,
        league_code__in=list(TARGET_CODES),
        match_date__gte="2026-04-01",
        match_date__lte="2026-04-30",
    )
    .values_list("match_date", flat=True)
    .distinct()
    .order_by("match_date")
)
# Friday=4, Saturday=5, Sunday=6
dates = [str(d) for d in dates if d.weekday() in (4, 5, 6)]
print(f"Scanning {len(dates)} Fri/Sat/Sun dates (April 2026)...\n", flush=True)

# --- run all dates ---
all_days = []   # (date_str, acc, combined, all_win, any_loss, has_unknown, tier)
for d in dates:
    _fd._season_stats_cache.clear()
    result = run_date(d)
    if not result:
        continue
    acc, combined, tier = result
    if not acc:
        continue
    all_win     = all(p.get("won") is True  for p in acc)
    any_loss    = any(p.get("won") is False for p in acc)
    has_unknown = any(p.get("won") is None  for p in acc)
    all_days.append((d, acc, combined, all_win, any_loss, has_unknown, tier))

perfect_days   = [(d, acc, combined, tier) for d, acc, combined, all_win, any_loss, _, tier in all_days if all_win]
imperfect_days = [(d, acc, combined, tier) for d, acc, combined, all_win, any_loss, _, tier in all_days if any_loss]

t1_days = [(d, acc, combined, aw, al, hu) for d, acc, combined, aw, al, hu, tier in all_days if tier == 1]
t2_days = [(d, acc, combined, aw, al, hu) for d, acc, combined, aw, al, hu, tier in all_days if tier == 2]
t1_wins = sum(1 for *_, aw, al, hu in t1_days if aw)
t2_wins = sum(1 for *_, aw, al, hu in t2_days if aw)

print(f"Total betting days : {len(all_days)}")
print(f"  Tier 1 (DC)      : {len(t1_days)} days  |  {t1_wins} wins  ({t1_wins/len(t1_days)*100:.1f}%)" if t1_days else "  Tier 1 (DC)      : 0 days")
print(f"  Tier 2 (Goals)   : {len(t2_days)} days  |  {t2_wins} wins  ({t2_wins/len(t2_days)*100:.1f}%)" if t2_days else "  Tier 2 (Goals)   : 0 days")
print(f"Perfect days       : {len(perfect_days)}")
print(f"Imperfect days     : {len(imperfect_days)}\n", flush=True)

def print_days(days, header):
    print(f"\n{'#'*80}")
    print(f"  {header}  ({len(days)} days)")
    print(f"{'#'*80}")
    current_month = None
    for d, acc, combined, tier in days:
        month = d[:7]
        if month != current_month:
            current_month = month
            print(f"\n  --- {MONTH_NAMES.get(month, month)} ---")
        odds_tag = f"@{combined:.2f}" + (" [>=2.0]" if combined >= TARGET_ODDS else " [<2.0] ")
        tier_tag = f"[T{tier}]"
        dow = datetime.strptime(d, "%Y-%m-%d").strftime("%a")
        print(f"\n  {d} ({dow})  {odds_tag} {tier_tag}  ({len(acc)} picks)")
        for p in acc:
            icon = "[W]" if p["won"] is True else "[L]" if p["won"] is False else "[ ]"
            flag = f"  {p['absence_flag']}" if p.get("absence_flag") else ""
            print(f"    {icon} {p['fixture']}{flag}")
            print(f"         {p['label']}  |  {p['league']}  |  Score: {p['score']}  |  {p['probability']:.1%}  @{p['odds']:.2f}")

MONTH_NAMES = {
    "2025-08":"AUGUST 2025","2025-09":"SEPTEMBER 2025",
    "2025-10":"OCTOBER 2025","2025-11":"NOVEMBER 2025",
    "2025-12":"DECEMBER 2025","2026-01":"JANUARY 2026",
    "2026-02":"FEBRUARY 2026","2026-03":"MARCH 2026","2026-04":"APRIL 2026",
}

print_days(perfect_days,  "PERFECT DAYS — all picks won")
print_days(imperfect_days, "IMPERFECT DAYS — at least one loss")

# ── BUILD SUMMARIES ───────────────────────────────────────────────────────────
monthly_summary = defaultdict(lambda: {"days": 0, "wins": 0, "losses": 0})
weekly_summary  = defaultdict(lambda: {"days": 0, "wins": 0, "losses": 0})

for d, acc, combined, all_win, any_loss, has_unknown, tier in all_days:
    month = d[:7]
    dt = datetime.strptime(d, "%Y-%m-%d")
    iso_yr, iso_wk, _ = dt.isocalendar()
    wk = f"{iso_yr}-W{iso_wk:02d}"
    monthly_summary[month]["days"] += 1
    weekly_summary[wk]["days"] += 1
    if all_win:
        monthly_summary[month]["wins"] += 1
        weekly_summary[wk]["wins"] += 1
    elif any_loss:
        monthly_summary[month]["losses"] += 1
        weekly_summary[wk]["losses"] += 1

# ── MONTHLY BREAKDOWN ─────────────────────────────────────────────────────────
print(f"\n\n{'='*80}")
print("  MONTHLY BREAKDOWN")
print(f"{'='*80}")
print(f"  {'Month':<20} {'Days':>5}  {'Wins':>5}  {'Loss':>5}  {'Win%':>6}")
print(f"  {'-'*50}")
total_d = total_w = total_l = 0
month_rows = []
for month in sorted(monthly_summary):
    s = monthly_summary[month]
    d, w, l = s["days"], s["wins"], s["losses"]
    pct = w / d * 100 if d else 0
    total_d += d; total_w += w; total_l += l
    month_rows.append((month, d, w, l, pct))
    name = MONTH_NAMES.get(month, month)
    print(f"  {name:<20} {d:>5}  {w:>5}  {l:>5}  {pct:>5.1f}%")

print(f"  {'-'*50}")
total_pct = total_w / total_d * 100 if total_d else 0
print(f"  {'TOTAL':<20} {total_d:>5}  {total_w:>5}  {total_l:>5}  {total_pct:>5.1f}%")

print(f"\n  Top months by win rate (min 3 days):")
ranked = sorted([r for r in month_rows if r[1] >= 3], key=lambda x: -x[4])
for i, (month, d, w, l, pct) in enumerate(ranked, 1):
    print(f"  #{i:>2}  {MONTH_NAMES.get(month, month):<20}  {w}/{d}  ({pct:.1f}%)")

# ── WEEKLY BREAKDOWN ──────────────────────────────────────────────────────────
print(f"\n\n{'='*80}")
print("  WEEKLY BREAKDOWN  (all Fri/Sat/Sun weeks chronologically)")
print(f"{'='*80}")
print(f"  {'Week':<12} {'Days':>5}  {'Wins':>5}  {'Loss':>5}  {'Win%':>6}  Bar")
print(f"  {'-'*55}")

week_rows = []
for wk in sorted(weekly_summary):
    s = weekly_summary[wk]
    d, w, l = s["days"], s["wins"], s["losses"]
    pct = w / d * 100 if d else 0
    week_rows.append((wk, d, w, l, pct))

for wk, d, w, l, pct in week_rows:
    bar = "W" * w + "." * l
    print(f"  {wk:<12} {d:>5}  {w:>5}  {l:>5}  {pct:>5.1f}%  {bar}")

print(f"\n  Top 10 weeks by win rate (min 2 days):")
top_weeks = sorted([r for r in week_rows if r[1] >= 2], key=lambda x: (-x[4], -x[2]))[:10]
for i, (wk, d, w, l, pct) in enumerate(top_weeks, 1):
    print(f"  #{i:>2}  {wk}  {w}/{d}  ({pct:.1f}%)")

print(f"\n{'='*80}\n")
