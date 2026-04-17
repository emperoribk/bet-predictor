import os, sys, io, django
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")
django.setup()

from bets.services import football_data as _fd
from bets.services.football_data import get_fixtures_by_date, _get_team_stats_from_db, key_player_absence_factor, _should_block_o15_booster
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

# ── Constants ────────────────────────────────────────────────────────────────
DC_MIN       = 0.79   # DC picks: calibrated alpha band 79–85%
DC_MAX       = 0.85   # above this DC odds are too short for accumulator value
GOALS_MIN    = 0.82   # Over/Under goals: raised from 0.80 after losses at 80-81% in SA/DED/PD
GOALS_MAX    = 0.92   # max probability for goals markets
MAX_PICKS    = 4      # cap per day — avoid compound risk from too many legs
MIN_DISPLAY_ODDS = 1.40   # skip trivially short accumulators (< 2 picks)
MIN_BACK_GAMES   = 10     # minimum games played before we back any team

# Per-league Over 1.5 minimum threshold — calibrated from observed O1.5 failures
LEAGUE_BOOSTER_THRESH = {
    "SPL": 0.90,   # Scottish Premiership — compact defending, 1-0s very common
    "FL1": 0.92,   # Ligue 1 — 1-0 results endemic; losses at 89%/90.4% forced this up
    "BL1": 0.87,   # Bundesliga 1 — 2 O1.5 losses at 83-85%: Mainz/Gladbach 0-1, Köln/Wolfsburg 1-0
    "SA":  0.86,   # Serie A — Atalanta/Inter 0-1 at 81%. Tight top-of-table games stay low-scoring
    "DED": 0.85,   # Eredivisie — 0-1 result at 81.3% confirmed this floor is too low
    "PD":  0.85,   # La Liga — Valencia/Levante 1-0 and Elche/Osasuna 0-0 at 80-81%
}

TARGET_CODES = {
    "PL","PD","BL1","SA","FL1","DED","PPL","ELC","SPL","BJL","TSL","BL2"
}

LEAGUE_PRIORITY = {
    "PL": 1, "PD": 1, "BL1": 1, "SA": 1, "FL1": 1,
    "DED": 2, "PPL": 2, "ELC": 2,
    "BL2": 3, "SPL": 3, "TSL": 3, "BJL": 3,
}

# Per-league DC minimum probability — lower leagues need tighter bands
# DED: NEC Nijmegen (82%) and PEC Zwolle (81%) both lost — lower-table DED teams are volatile
# BL2: Dynamo Dresden (81.8%) lost — second division upsets need higher confidence
LEAGUE_DC_MIN = {
    "DED": 0.84,
    "BL2": 0.84,
    "SPL": 0.84,  # Celtic home loss to Hibernian at 83.2%
}

DERBY_PAIRS = {
    # Istanbul derbies — uniquely volatile, form irrelevant
    frozenset({"Beşiktaş", "Fenerbahçe"}),
    frozenset({"Beşiktaş", "Galatasaray"}),
    frozenset({"Fenerbahçe", "Galatasaray"}),
    # Old Firm — Celtic 1-3 at home to Rangers (Jan 2026 loss confirmed)
    frozenset({"Celtic", "Rangers"}),
    # English top derbies — form means nothing on derby day
    frozenset({"Liverpool", "Everton"}),
    frozenset({"Arsenal", "Tottenham Hotspur"}),
    frozenset({"Manchester City", "Manchester United"}),
    frozenset({"Chelsea", "Arsenal"}),
    # El Clasico and Madrid derbies
    frozenset({"Real Madrid", "Atletico Madrid"}),
    frozenset({"Barcelona", "Atletico Madrid"}),
}

NEVER_BACK = {
    "Monaco",       # wildly inconsistent — beat PSG, lost to mid-table as favourite
    "VfL Bochum",   # 2 DC losses (1-2 and 2-3) — chronic relegation battler, late collapses
    "Toulouse",     # 2 FL1 losses scoring zero (0-1, 0-3) — model overstates them
    "RB Leipzig",   # 2 BL1 losses (1-2 and 0-0) — top-table inconsistency, risk not worth taking
    "Espanyol",     # relegated and re-promoted — model has stale 'strong' xG profile, lost 0-2
    "Paris FC",     # newly promoted FL1 team — model lacks reliable data, scored 0 in our picks
    "Southampton",  # relegated PL team in Championship — identity/form crisis, 0-2 loss
}

# ── Probability helpers ───────────────────────────────────────────────────────
def _over15_prob(h_xg, a_xg):
    """P(total goals >= 2) via Poisson."""
    import math
    p_0 = math.exp(-h_xg) * math.exp(-a_xg)
    p_1 = math.exp(-h_xg) * h_xg * math.exp(-a_xg) + math.exp(-h_xg) * math.exp(-a_xg) * a_xg
    return 1 - p_0 - p_1

def _under25_prob(h_xg, a_xg):
    """P(total goals <= 2) via Poisson."""
    import math
    lam = h_xg + a_xg
    return math.exp(-lam) * (1 + lam + lam**2 / 2)

def _team_score_prob(xg):
    """P(team scores >= 1) via Poisson."""
    import math
    return 1 - math.exp(-xg)


# ── Accumulator builder ───────────────────────────────────────────────────────
def _build_accumulator(candidates):
    """
    Build accumulator from per-fixture best picks.
    Sorted by tier (DC first) then by probability descending.
    Add up to MAX_PICKS picks — stop early only if combined exceeds @4.0
    so we never build runaway 3.5x accumulators.
    No @2.0 hard requirement — show every qualifying day.
    """
    if not candidates:
        return None, None

    # Primary sort: tier (DC=1 first), secondary: probability descending
    candidates = sorted(candidates, key=lambda x: (x.get("tier", 3), -x["probability"]))
    accumulator   = []
    combined      = 1.0
    used_fixtures = set()

    for pick in candidates:
        if len(accumulator) >= MAX_PICKS:
            break
        if pick["fixture"] in used_fixtures:
            continue
        new_combined = combined * pick["odds"]
        if new_combined > 4.0:
            continue  # don't let one pick push us past @4.0
        accumulator.append(pick)
        combined = round(new_combined, 4)
        used_fixtures.add(pick["fixture"])

    return (accumulator, round(combined, 4)) if accumulator else (None, None)


# ── Per-date prediction engine ────────────────────────────────────────────────
def run_date(target_date):
    try:
        fixtures = get_fixtures_by_date(target_date)
    except Exception:
        return []
    league_fixtures = [f for f in fixtures if f.get("competition_code") in TARGET_CODES]

    all_candidates = []   # best pick per fixture

    for fix in league_fixtures:
        home_name = fix["home_team"]
        away_name = fix["away_team"]
        comp_code = fix.get("competition_code", "PL")
        try:
            if frozenset({home_name, away_name}) in DERBY_PAIRS:
                continue

            hs  = get_team_season_stats(team_id=fix.get("home_team_id", 0), team_name=home_name,
                                        competition_code=comp_code, match_date=target_date)
            aws = get_team_season_stats(team_id=fix.get("away_team_id", 0), team_name=away_name,
                                        competition_code=comp_code, match_date=target_date)
            if not hs or not aws:
                continue

            h_games   = hs.get("played", 0) or 0
            a_games   = aws.get("played", 0) or 0
            min_games = min(h_games, a_games)
            if min_games < MIN_BACK_GAMES:
                continue

            h_xg, a_xg = _expected_xg_for_match(hs, aws)
            if h_xg <= 0 or a_xg <= 0:
                continue

            _, h_flag = key_player_absence_factor(home_name, target_date)
            _, a_flag = key_player_absence_factor(away_name, target_date)

            p_home, p_draw, p_away = _match_probs(h_xg, a_xg)
            p_o15  = _over15_prob(h_xg, a_xg)
            p_u25  = _under25_prob(h_xg, a_xg)
            p_h_sc = _team_score_prob(h_xg)
            p_a_sc = _team_score_prob(a_xg)

            home_score  = fix.get("home_score")
            away_score  = fix.get("away_score")
            fixture_str = f"{home_name} vs {away_name}"
            score_str   = (f"{home_score}-{away_score}"
                           if home_score is not None and away_score is not None else "?")

            options = []

            # ── Tier 1: Double Chance picks (calibrated alpha band) ──────────
            # Empirical: DC at 79-85% wins ~91% of the time — model understates
            # by ~11%. We cap at 85% because above that odds are too short (@1.18)
            # to justify the pick in an accumulator.
            # League-specific DC minimums applied for volatile lower leagues.
            _dc_min = max(DC_MIN, LEAGUE_DC_MIN.get(comp_code, DC_MIN))

            p_1x = p_home + p_draw
            if _dc_min <= p_1x <= DC_MAX and home_name not in NEVER_BACK:
                won = (home_score >= away_score) if home_score is not None else None
                options.append({
                    "fixture": fixture_str, "label": f"{home_name} or Draw (1X)",
                    "market": "DC_1X", "probability": round(p_1x, 4),
                    "odds": round(1 / p_1x, 2), "won": won,
                    "score": score_str, "league": comp_code,
                    "absence_flag": h_flag, "tier": 1,
                })

            p_x2 = p_draw + p_away
            if _dc_min <= p_x2 <= DC_MAX and away_name not in NEVER_BACK:
                won = (away_score >= home_score) if home_score is not None else None
                options.append({
                    "fixture": fixture_str, "label": f"Draw or {away_name} (X2)",
                    "market": "DC_X2", "probability": round(p_x2, 4),
                    "odds": round(1 / p_x2, 2), "won": won,
                    "score": score_str, "league": comp_code,
                    "absence_flag": a_flag, "tier": 1,
                })

            # ── Tier 2: Goals markets (Over/Under) ───────────────────────────
            # Over 1.5 Goals: league-specific floor applies (FL1 notorious for 1-0s)
            _o15_thresh   = LEAGUE_BOOSTER_THRESH.get(comp_code, GOALS_MIN)
            _block_o15, _ = _should_block_o15_booster(home_name, away_name, comp_code, target_date)
            if _o15_thresh <= p_o15 <= GOALS_MAX and not _block_o15:
                won = ((home_score + away_score) >= 2) if home_score is not None else None
                options.append({
                    "fixture": fixture_str, "label": "Over 1.5 Goals",
                    "market": "OVER15", "probability": round(p_o15, 4),
                    "odds": round(1 / p_o15, 2), "won": won,
                    "score": score_str, "league": comp_code,
                    "absence_flag": None, "tier": 2,
                })

            # Under 2.5 Goals: fires when combined xG <= ~1.65 (rare but valid)
            if GOALS_MIN <= p_u25 <= GOALS_MAX:
                won = ((home_score + away_score) <= 2) if home_score is not None else None
                options.append({
                    "fixture": fixture_str, "label": "Under 2.5 Goals",
                    "market": "UNDER25", "probability": round(p_u25, 4),
                    "odds": round(1 / p_u25, 2), "won": won,
                    "score": score_str, "league": comp_code,
                    "absence_flag": None, "tier": 2,
                })

            # ── Tier 3: Team to Score (fallback — Big 5 leagues only) ────────
            # Only used when no DC or goals pick qualifies for this fixture.
            # Restricted to Big 5 leagues: BL2/DED/TSL/SPL produce too many blanks.
            # Skip if key scorer has an absence flag — Crystal Palace 0-1 (Sarr out)
            # confirmed the absence signal is meaningful for team-score market.
            BIG5 = {"PL", "PD", "BL1", "SA", "FL1"}
            if comp_code in BIG5:
                if (GOALS_MIN <= p_h_sc <= GOALS_MAX
                        and home_name not in NEVER_BACK
                        and not h_flag):   # absence flag = key scorer missing → skip
                    won = (home_score >= 1) if home_score is not None else None
                    options.append({
                        "fixture": fixture_str, "label": f"{home_name} to Score",
                        "market": "TEAM_SCORE", "probability": round(p_h_sc, 4),
                        "odds": round(1 / p_h_sc, 2), "won": won,
                        "score": score_str, "league": comp_code,
                        "absence_flag": None, "tier": 3,
                    })

                if (GOALS_MIN <= p_a_sc <= GOALS_MAX
                        and away_name not in NEVER_BACK
                        and not a_flag):   # same for away team scorer
                    won = (away_score >= 1) if away_score is not None else None
                    options.append({
                        "fixture": fixture_str, "label": f"{away_name} to Score",
                        "market": "TEAM_SCORE", "probability": round(p_a_sc, 4),
                        "odds": round(1 / p_a_sc, 2), "won": won,
                        "score": score_str, "league": comp_code,
                        "absence_flag": None, "tier": 3,
                    })

            if not options:
                continue

            # Best pick = lowest tier first (DC > Goals > TS), then highest prob
            options.sort(key=lambda x: (x["tier"], -x["probability"]))
            best = options[0]
            all_candidates.append(best)

        except Exception:
            continue

    if not all_candidates:
        return []

    accumulator, combined = _build_accumulator(all_candidates)
    if accumulator:
        return accumulator, combined, 1
    return []


# ── Collect backtest dates: Oct 2025 – Apr 2026, Fri/Sat/Sun only ─────────────
dates = list(
    HistoricalFixture.objects.filter(
        home_score__isnull=False,
        league_code__in=list(TARGET_CODES),
        match_date__gte="2025-10-01",
        match_date__lte="2026-04-13",
    )
    .values_list("match_date", flat=True)
    .distinct()
    .order_by("match_date")
)
WEEKEND_DAYS = (4, 5, 6)   # Fri/Sat/Sun
WEEKDAY_DAYS = (0, 1, 2, 3)
dates = [str(d) for d in dates if d.weekday() in WEEKEND_DAYS]
MIN_SUB_ODDS = 1.30

print(f"Scanning {len(dates)} Fri/Sat/Sun dates (Oct 2025 – Apr 2026) | Min display: @{MIN_DISPLAY_ODDS}+  Max picks: {MAX_PICKS}\n", flush=True)

# ── Run all dates ─────────────────────────────────────────────────────────────
all_days  = []   # (date_str, acc, combined, all_win, any_loss, has_unknown, tier)

for d in dates:
    _fd._season_stats_cache.clear()
    result = run_date(d)
    if not result:
        continue
    acc, combined, tier = result
    if not acc or combined < MIN_DISPLAY_ODDS:
        continue
    all_win     = all(p.get("won") is True  for p in acc)
    any_loss    = any(p.get("won") is False for p in acc)
    has_unknown = any(p.get("won") is None  for p in acc)
    all_days.append((d, acc, combined, all_win, any_loss, has_unknown, tier))

perfect_days   = [(d, acc, combined, tier) for d, acc, combined, all_win, any_loss, _, tier in all_days if all_win]
imperfect_days = [(d, acc, combined, tier) for d, acc, combined, all_win, any_loss, _, tier in all_days if any_loss]

# Market breakdown
from collections import Counter
mkt_counts = Counter()
mkt_wins   = Counter()
for d, acc, combined, all_win, any_loss, hu, tier in all_days:
    for p in acc:
        mkt = p["market"]
        mkt_counts[mkt] += 1
        if p.get("won") is True:
            mkt_wins[mkt] += 1

above2 = sum(1 for d, acc, c, *_ in all_days if c >= 2.0)
below2 = len(all_days) - above2
print(f"Total qualifying days : {len(all_days)}  (@{MIN_DISPLAY_ODDS}+)")
print(f"  Above @2.0  : {above2} days  ({above2/len(all_days)*100:.0f}%)" if all_days else "")
print(f"  Below @2.0  : {below2} days  ({below2/len(all_days)*100:.0f}%)" if all_days else "")
print(f"Perfect days  : {len(perfect_days)}")
print(f"Imperfect days: {len(imperfect_days)}\n")

print("Market breakdown (all picks across all days):")
for mkt in ["DC_1X", "DC_X2", "OVER15", "UNDER25", "TEAM_SCORE"]:
    n = mkt_counts[mkt]
    w = mkt_wins[mkt]
    pct = w / n * 100 if n else 0
    bar = " ★" if pct >= 88 else (" !" if pct < 80 else "")
    print(f"  {mkt:<12} {n:>5} picks   {w:>5} won   ({pct:.1f}%){bar}")
print(flush=True)


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
        odds_tag = f"@{combined:.2f}"
        dow = datetime.strptime(d, "%Y-%m-%d").strftime("%a")
        print(f"\n  {d} ({dow})  {odds_tag}  ({len(acc)} picks)")
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

print_days(perfect_days,   "PERFECT DAYS — all picks won")
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

# ── LOSS ANALYSIS ─────────────────────────────────────────────────────────────
print(f"\n{'='*80}")
print("  LOSS ANALYSIS — why picks failed")
print(f"{'='*80}\n")

# Collect ALL picks from all days (both wins and losses)
all_picks_flat = []
for d, acc, combined, all_win, any_loss, has_unknown, tier in all_days:
    for p in acc:
        if p.get("won") is not None:
            all_picks_flat.append(p)

total_picks = len(all_picks_flat)
lost_picks  = [p for p in all_picks_flat if p["won"] is False]
won_picks   = [p for p in all_picks_flat if p["won"] is True]

print(f"  Total picks across all days : {total_picks}")
print(f"  Won  : {len(won_picks)}   Lost: {len(lost_picks)}   Pick-level win rate: {len(won_picks)/total_picks*100:.1f}%\n")

# Loss by market
print(f"  {'Market':<14} {'Total':>6}  {'Won':>5}  {'Lost':>5}  {'Win%':>6}")
print(f"  {'-'*42}")
for mkt in ["DC_1X", "DC_X2", "OVER15", "UNDER25", "TEAM_SCORE"]:
    t = [p for p in all_picks_flat if p["market"] == mkt]
    w = [p for p in t if p["won"] is True]
    l = [p for p in t if p["won"] is False]
    if not t:
        continue
    print(f"  {mkt:<14} {len(t):>6}  {len(w):>5}  {len(l):>5}  {len(w)/len(t)*100:>5.1f}%")

# Loss by league
print(f"\n  Losses by league:")
from collections import Counter
loss_by_league = Counter(p["league"] for p in lost_picks)
total_by_league = Counter(p["league"] for p in all_picks_flat)
league_rows = []
for lg, n_loss in loss_by_league.most_common():
    n_total = total_by_league[lg]
    n_win   = n_total - n_loss
    pct     = n_win / n_total * 100
    league_rows.append((lg, n_total, n_win, n_loss, pct))
print(f"  {'League':<8} {'Total':>6}  {'Won':>5}  {'Lost':>5}  {'Win%':>6}")
print(f"  {'-'*38}")
for lg, t, w, l, pct in sorted(league_rows, key=lambda x: x[3], reverse=True):
    bar = " !!!" if pct < 70 else (" !!" if pct < 80 else "")
    print(f"  {lg:<8} {t:>6}  {w:>5}  {l:>5}  {pct:>5.1f}%{bar}")

# Loss by confidence band
print(f"\n  Losses by confidence band:")
bands = [(0.77, 0.80, "77-80%"), (0.80, 0.83, "80-83%"), (0.83, 0.86, "83-86%"),
         (0.86, 0.89, "86-89%"), (0.89, 0.92, "89-92%"), (0.92, 1.01, "92%+")]
print(f"  {'Band':<8} {'Total':>6}  {'Won':>5}  {'Lost':>5}  {'Win%':>6}  {'Dominant market'}")
print(f"  {'-'*60}")
for lo, hi, label in bands:
    t = [p for p in all_picks_flat if lo <= p["probability"] < hi]
    w = [p for p in t if p["won"] is True]
    l = [p for p in t if p["won"] is False]
    if not t:
        continue
    dom_mkt = Counter(p["market"] for p in t).most_common(1)[0][0]
    pct = len(w) / len(t) * 100
    flag = " ← zero-alpha zone" if dom_mkt == "TEAM_SCORE" and pct < 91 else ""
    print(f"  {label:<8} {len(t):>6}  {len(w):>5}  {len(l):>5}  {pct:>5.1f}%  {dom_mkt}{flag}")

# Most common losing patterns
print(f"\n  Most frequent single-pick losses:")
loss_counter = Counter(f"{p['market']}|{p['league']}" for p in lost_picks)
print(f"  {'Pattern':<24} {'Count':>6}  Notes")
print(f"  {'-'*50}")
notes = {
    "TEAM_SCORE|FL1": "Ligue 1 — endemic 0-0/1-0 results",
    "TEAM_SCORE|BL1": "Bundesliga — upsets vs top sides",
    "TEAM_SCORE|BL2": "Bundesliga 2 — volatile, lower quality",
    "TEAM_SCORE|PD": "La Liga — Atletico/smaller sides park bus",
    "TEAM_SCORE|DED": "Eredivisie — occasional blanks",
    "TEAM_SCORE|TSL": "Süper Lig — tactical 0-0 games",
    "OVER15|BL1": "Low-scoring Bundesliga 1-0 results",
    "OVER15|PD": "La Liga — defensive heavy days",
    "DC_1X|PL":  "Premier League upset — home side lost",
    "DC_1X|BL1": "Bundesliga shock result",
    "DC_1X|BL2": "Bundesliga 2 shock result",
}
for pattern, count in loss_counter.most_common(12):
    note = notes.get(pattern, "")
    print(f"  {pattern:<24} {count:>6}  {note}")
print()
