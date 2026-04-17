import os, sys, io, django, math
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")
django.setup()

from bets.services.football_data import get_fixtures_by_date, _get_team_stats_from_db, _should_block_o15_booster, key_player_absence_factor
from bets.services.daily_predictions import _match_probs, _expected_xg_for_match
from bets.models import HistoricalFixture
from difflib import SequenceMatcher

TARGET_DATE  = sys.argv[1] if len(sys.argv) > 1 else "2026-04-18"
TARGET_CODES = {"PL","PD","BL1","SA","FL1","DED","PPL","ELC","SPL","BJL","TSL","BL2"}
CONF_THRESH  = 0.77
MAX_PROB     = 0.92
TARGET_ODDS  = 2.0

# ── resolve canonical team IDs from our DB by name ────────────────────────────
def _resolve_team_id(team_name: str, league_code: str) -> int:
    """Fuzzy-match team_name against DB records to get the canonical team_id."""
    rows = (HistoricalFixture.objects
            .filter(league_code=league_code)
            .values_list("home_team", "home_team_id")
            .distinct())
    best_id, best_score = None, 0.0
    for db_name, db_id in rows:
        score = SequenceMatcher(None, team_name.lower(), db_name.lower()).ratio()
        if score > best_score:
            best_score, best_id = score, db_id
    return best_id if best_score >= 0.50 else None

# ── probability helpers ───────────────────────────────────────────────────────
def p_o15(h, a):
    p0 = math.exp(-h) * math.exp(-a)
    p1 = math.exp(-h)*h*math.exp(-a) + math.exp(-h)*math.exp(-a)*a
    return 1 - p0 - p1

def p_u25(h, a):
    lam = h + a
    return math.exp(-lam) * (1 + lam + lam**2 / 2)

def p_score(xg):
    return 1 - math.exp(-xg)

# ── fetch fixtures ─────────────────────────────────────────────────────────────
fixtures   = get_fixtures_by_date(TARGET_DATE)
league_fix = [f for f in fixtures if f.get("competition_code") in TARGET_CODES]

print(f"\nScanning {len(league_fix)} fixtures on {TARGET_DATE}")
print(f"Leagues : {', '.join(sorted(set(f.get('competition_code') for f in league_fix)))}")
print("=" * 72)

all_picks = []

for fix in league_fix:
    home = fix["home_team"]
    away = fix["away_team"]
    code = fix.get("competition_code", "")
    fstr = f"{home} vs {away}"

    try:
        # always resolve IDs from DB by name — API IDs differ from DB IDs for future games
        h_id = _resolve_team_id(home, code)
        a_id = _resolve_team_id(away, code)
        if not h_id or not a_id:
            continue

        hs  = _get_team_stats_from_db(team_id=h_id, team_name=home,
                                       competition_code=code, match_date_str=TARGET_DATE, season=2025)
        aws = _get_team_stats_from_db(team_id=a_id, team_name=away,
                                       competition_code=code, match_date_str=TARGET_DATE, season=2025)
        if not hs or not aws:
            continue
        if min(hs.get("played", 0), aws.get("played", 0)) < 10:
            continue

        h_xg, a_xg = _expected_xg_for_match(hs, aws)
        if h_xg <= 0 or a_xg <= 0:
            continue

        ph, pd, pa   = _match_probs(h_xg, a_xg)
        po15         = p_o15(h_xg, a_xg)
        pu25         = p_u25(h_xg, a_xg)
        ph_sc        = p_score(h_xg)
        pa_sc        = p_score(a_xg)

        _, h_flag = key_player_absence_factor(home, TARGET_DATE)
        _, a_flag = key_player_absence_factor(away, TARGET_DATE)
        _block_b, _ = _should_block_o15_booster(home, away, code, TARGET_DATE)

        # ── every market ────────────────────────────────────────────────────
        options = []
        if CONF_THRESH <= ph+pd <= MAX_PROB:
            options.append((f"{home} or Draw (1X)",   "DC_1X",      ph+pd,  h_flag))
        if CONF_THRESH <= pd+pa <= MAX_PROB:
            options.append((f"Draw or {away} (X2)",   "DC_X2",      pd+pa,  a_flag))
        if CONF_THRESH <= po15 <= MAX_PROB and not _block_b:
            options.append(("Over 1.5 Goals",         "OVER15",     po15,   None))
        if CONF_THRESH <= pu25 <= MAX_PROB:
            options.append(("Under 2.5 Goals",        "UNDER25",    pu25,   None))
        if CONF_THRESH <= ph_sc <= MAX_PROB:
            options.append((f"{home} to Score",       "TEAM_SCORE", ph_sc,  h_flag))
        if CONF_THRESH <= pa_sc <= MAX_PROB:
            options.append((f"{away} to Score",       "TEAM_SCORE", pa_sc,  a_flag))

        if not options:
            continue

        print(f"\n  [{code}]  {fstr}  |  xG: {h_xg:.2f} vs {a_xg:.2f}")
        for label, mkt, prob, flag in sorted(options, key=lambda x: -x[2]):
            flag_s = f"  {flag}" if flag else ""
            print(f"    {prob:.1%}  @{round(1/prob,2):.2f}  {label}{flag_s}")

        for label, mkt, prob, flag in options:
            all_picks.append({
                "fixture": fstr, "label": label, "market": mkt,
                "prob": round(prob, 4), "odds": round(1 / prob, 2),
                "league": code, "h_xg": h_xg, "a_xg": a_xg,
                "absence_flag": flag,
            })

    except Exception:
        continue

# ── build accumulator: best pick per fixture, sorted by confidence ─────────────
print(f"\n\n{'=' * 72}")
print(f"  RECOMMENDED ACCUMULATOR  —  target @{TARGET_ODDS}+")
print(f"{'=' * 72}")

# best pick per fixture = highest probability option
best_per_fix = {}
for p in all_picks:
    k = p["fixture"]
    if k not in best_per_fix or p["prob"] > best_per_fix[k]["prob"]:
        best_per_fix[k] = p

candidates = sorted(best_per_fix.values(), key=lambda x: -x["prob"])

accumulator   = []
combined      = 1.0
used_fixtures = set()

for pick in candidates:
    if combined >= 4.0 or len(accumulator) >= 7:
        break
    if pick["fixture"] in used_fixtures:
        continue
    new_combined = combined * pick["odds"]
    if new_combined > 4.0:
        continue
    accumulator.append(pick)
    combined = round(new_combined, 2)
    used_fixtures.add(pick["fixture"])

if accumulator:
    tag = "QUALIFIES >= @2.0" if combined >= TARGET_ODDS else f"sub-2.0 — @{combined:.2f}"
    print(f"\n  Model combined : @{combined:.2f}  [{tag}]")
    print(f"  Picks          : {len(accumulator)}\n")
    for i, p in enumerate(accumulator, 1):
        flag_s = f"\n       {p['absence_flag']}" if p.get("absence_flag") else ""
        print(f"  {i}. [{p['league']}]  {p['fixture']}{flag_s}")
        print(f"       {p['label']}")
        print(f"       Model: {p['prob']:.1%}  @{p['odds']:.2f}  |  xG: {p['h_xg']:.2f} vs {p['a_xg']:.2f}")
        print()
else:
    print("\n  No qualifying accumulator found.\n")
