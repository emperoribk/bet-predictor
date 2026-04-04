"""
Standalone runner: python run_pl_predictions.py [YYYY-MM-DD]
Mirrors the Django management command run_predictions exactly.
"""
import os, sys, django, io

# Force UTF-8 output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from bets.services.football_data import (
    get_fixtures_by_date, get_team_season_stats, get_team_context, get_h2h_scored
)
from bets.services.daily_predictions import get_all_bet_signals
from bets.services.api_football import get_fixture_injuries_for_match

# Accept date from CLI arg, fall back to hardcoded default
from datetime import date as _date
TARGET_DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-03-28"
ACCURACY_THRESHOLD = 85
DIV = "=" * 70

TARGET_LEAGUES = [
    # ── Big 5 (Understat xG available) ──────────────────────────────────────
    ("Premier League",                    "England",       "PL"),
    ("La Liga",                           "Spain",         "PD"),
    ("Primera Division",                  "Spain",         "PD"),
    ("Bundesliga",                        "Germany",       "BL1"),
    ("Serie A",                           "Italy",         "SA"),
    ("Ligue 1",                           "France",        "FL1"),
    # ── Extended Tier 1 (API-Football xG fallback) ───────────────────────────
    ("Eredivisie",                        "Netherlands",   "DED"),
    ("Primeira Liga",                     "Portugal",      "PPL"),
    ("Championship",                      "England",       "ELC"),
    ("Premier League",                    "Scotland",      "SPL"),
    ("Jupiler Pro League",                "Belgium",       "BJL"),
    ("Süper Lig",                         "Turkey",        "TSL"),
    ("2. Bundesliga",                     "Germany",       "BL2"),
    # ── Extended Tier 2 ──────────────────────────────────────────────────────
    ("Campeonato Brasileiro Série A",     "Brazil",        "BSA"),
    ("MLS",                               "United States", "MLS"),
    ("Super League",                      "Switzerland",   "SSL"),
    ("Liga Profesional",                  "Argentina",     "ASL"),
    ("Bundesliga",                        "Austria",       "ABL"),
    # ── European cups ────────────────────────────────────────────────────────
    ("UEFA Champions League",             "Europe",        "CL"),
    ("UEFA Europa League",                "Europe",        "EL"),
    ("UEFA Conference League",            "Europe",        "UECL"),
]


def run():
    print(f"\n{DIV}")
    print(f"  SPORTY TOP PICKS  |  {TARGET_DATE}  |  {ACCURACY_THRESHOLD}%+ CONFIDENCE ONLY")
    print(f"{DIV}\n")
    print(f"  Analysing matches, please wait...\n")

    import requests as _req
    from django.conf import settings as _cfg
    try:
        _ping = _req.get(
            "https://api.football-data.org/v4/competitions/PL/matches",
            headers={"X-Auth-Token": _cfg.FOOTBALL_DATA_API_KEY},
            params={"dateFrom": TARGET_DATE, "dateTo": TARGET_DATE},
            timeout=12,
        )
        _ping_matches = len(_ping.json().get("matches", [])) if _ping.status_code == 200 else 0
        print(f"  API connection: OK (status {_ping.status_code}, {_ping_matches} PL matches on {TARGET_DATE})")
        if _ping.status_code == 401:
            print(f"  ERROR: Invalid API key — check FOOTBALL_DATA_API_KEY in your .env")
            return
        if _ping.status_code == 429:
            print(f"  ERROR: API rate limit hit — wait a minute and try again.")
            return
    except Exception as _e:
        print(f"  [Warning] Connectivity ping failed ({type(_e).__name__}) — continuing anyway.")

    try:
        fixtures = get_fixtures_by_date(TARGET_DATE)
    except ConnectionError as e:
        print(f"\n  ERROR: {e}")
        print(f"  Tip: add the results manually to seed_results.py and run:")
        print(f"       python manage.py seed_results")
        return
    except Exception as e:
        print(f"  ERROR: Could not fetch fixtures — {e}")
        return

    target_codes = {t[2] for t in TARGET_LEAGUES}
    target_pairs = {(t[0], t[1]) for t in TARGET_LEAGUES}

    league_fixtures = [
        f for f in fixtures
        if f.get("competition_code") in target_codes
        or (f.get("competition"), f.get("area")) in target_pairs
    ]

    if not league_fixtures:
        if not fixtures:
            print(f"  No top-league matches found for {TARGET_DATE}.")
            print(f"  (Could be an international break, or no fixtures in supported leagues)")
        else:
            print(f"  No top-league fixtures on {TARGET_DATE}.")
            print(f"  ({len(fixtures)} matches found in other competitions)")
        return

    all_picks = []

    for fix in league_fixtures:
        home_name = fix["home_team"]
        away_name = fix["away_team"]
        kickoff   = fix.get("kickoff", "")
        comp_code = fix.get("competition_code", "PL")

        try:
            home_stats = get_team_season_stats(
                team_id=fix.get("home_team_id", 0),
                team_name=home_name,
                competition_code=comp_code,
                match_date=TARGET_DATE,
            )
            away_stats = get_team_season_stats(
                team_id=fix.get("away_team_id", 0),
                team_name=away_name,
                competition_code=comp_code,
                match_date=TARGET_DATE,
            )

            if not home_stats or not away_stats:
                continue

            home_ctx = get_team_context(home_stats.get("team_id", 0), comp_code)
            away_ctx = get_team_context(away_stats.get("team_id", 0), comp_code)

            # H2H check — penalise if team failed to score vs this opponent recently
            h2h = get_h2h_scored(
                fix.get("home_team_id", 0),
                fix.get("away_team_id", 0)
            )
            if h2h.get("home_blanked_h2h", 0) >= 2:
                for key in ("xg_per_game", "home_xg_pg", "scored_per_game"):
                    if home_stats.get(key):
                        home_stats[key] = round(home_stats[key] * 0.82, 3)
                home_stats["h2h_note"] = f"scored in only {3 - h2h['home_blanked_h2h']}/3 recent H2H vs {away_name}"
            elif h2h.get("home_blanked_h2h", 0) == 1 and not h2h.get("home_scored_last_h2h"):
                for key in ("xg_per_game", "home_xg_pg", "scored_per_game"):
                    if home_stats.get(key):
                        home_stats[key] = round(home_stats[key] * 0.90, 3)
                home_stats["h2h_note"] = f"failed to score vs {away_name} in last meeting ({h2h.get('last_h2h_date', '')})"

            if h2h.get("away_blanked_h2h", 0) >= 2:
                for key in ("xg_per_game", "away_xg_pg", "scored_per_game"):
                    if away_stats.get(key):
                        away_stats[key] = round(away_stats[key] * 0.82, 3)

            # Fetch injury data and apply penalty before signal generation
            injuries = get_fixture_injuries_for_match(
                home_name, away_name, TARGET_DATE, comp_code
            )
            if injuries["available"]:
                h_eff = min(injuries["home"]["missing"], 4)
                a_eff = min(injuries["away"]["missing"], 4)
                if h_eff > 0:
                    penalty = 1 - h_eff * 0.05
                    for key in ("xg_per_game", "home_xg_pg", "scored_per_game"):
                        if home_stats.get(key):
                            home_stats[key] = round(home_stats[key] * penalty, 3)
                if a_eff > 0:
                    penalty = 1 - a_eff * 0.05
                    for key in ("xg_per_game", "away_xg_pg", "scored_per_game"):
                        if away_stats.get(key):
                            away_stats[key] = round(away_stats[key] * penalty, 3)

            signals = get_all_bet_signals(home_stats, away_stats, home_ctx, away_ctx)

            league_label = f"{fix.get('competition', '')} ({fix.get('area', '')})"
            for s in signals:
                gi = s.get("grade_info", {})
                inj_notes = []
                if injuries.get("available"):
                    h_miss = injuries["home"]["missing"]
                    a_miss = injuries["away"]["missing"]
                    h_players = injuries["home"].get("players", [])
                    a_players = injuries["away"].get("players", [])
                    if h_miss:
                        names = ', '.join(p for p in h_players[:3] if p)
                        inj_notes.append(f"{home_name}: {h_miss} missing ({names})")
                    if a_miss:
                        names = ', '.join(p for p in a_players[:3] if p)
                        inj_notes.append(f"{away_name}: {a_miss} missing ({names})")

                all_picks.append({
                    "fixture":     f"{home_name} vs {away_name}",
                    "league":      league_label,
                    "kickoff":     kickoff,
                    "bet":         s.get("label", s.get("type")),
                    "confidence":  s.get("confidence", 0),
                    "grade":       gi.get("grade", "?"),
                    "grade_score": gi.get("grade_score", 0),
                    "accuracy":    gi.get("accuracy_pct", "?"),
                    "grade_label": gi.get("grade_label", ""),
                    "evidence":    s.get("evidence", []),
                    "reaches_95":  gi.get("reaches_95", False),
                    "penalties":   gi.get("penalties", []),
                    "injuries":    inj_notes,
                })

        except Exception:
            import traceback; traceback.print_exc()
            continue

    if not all_picks:
        print("  No signals generated — stats data may be unavailable for this date.")
        return

    all_picks.sort(
        key=lambda x: (x["accuracy"] if isinstance(x["accuracy"], (int, float)) else 0, x["grade_score"]),
        reverse=True,
    )

    threshold_picks = [p for p in all_picks if p["grade_score"] >= 79]

    seen = {}
    for p in threshold_picks:
        if p["fixture"] not in seen:
            seen[p["fixture"]] = p
    deduped = list(seen.values())

    def _bar(score, w=28):
        f = int(score / 100 * w)
        return "[" + "#" * f + "." * (w - f) + "]"

    print(f"\n{DIV}")
    print(f"  TOP PICKS  |  {len(deduped)} match(es) with {ACCURACY_THRESHOLD}%+ confidence")
    print(f"{DIV}\n")

    if not deduped:
        print(f"  No picks met the {ACCURACY_THRESHOLD}% threshold for {TARGET_DATE}.\n")
    else:
        for i, p in enumerate(deduped, 1):
            star = "  *** 95%+ ***" if p["reaches_95"] else ""
            print(f"  [{i}] {p['fixture']}")
            print(f"       League   : {p['league']}")
            print(f"       Kickoff  : {p['kickoff']}")
            print(f"       Bet      : {p['bet']}")
            print(f"       Grade    : {p['grade']}  {p['grade_score']}/100  {_bar(p['grade_score'])}{star}")
            print(f"       Conf/Acc : {p['confidence']}% confidence  |  {p['accuracy']}% accuracy")
            print(f"       Verdict  : {p['grade_label']}")
            if p["evidence"]:
                print(f"       Evidence :")
                for ev in p["evidence"][:5]:
                    print(f"         - {ev}")
            if p["penalties"]:
                print(f"       Caution  :")
                for pen in p["penalties"]:
                    print(f"         ! {pen}")
            if p.get("injuries"):
                print(f"       Injuries :")
                for inj in p["injuries"]:
                    print(f"         ~ {inj}")
            print()

    print(f"{DIV}\n")


if __name__ == "__main__":
    run()
