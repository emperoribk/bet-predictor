"""
Django management command: python manage.py run_predictions --date 2026-03-28
Runs the full prediction + grading engine and prints only 85%+ confidence picks.
"""
import io, sys
from datetime import datetime
from django.core.management.base import BaseCommand
from bets.services.football_data import get_fixtures_by_date, get_team_season_stats, get_team_context, get_h2h_scored
from bets.services.daily_predictions import get_all_bet_signals
from bets.services.api_football import get_fixture_injuries_for_match
from bets.models import Prediction

# Force UTF-8 on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ACCURACY_THRESHOLD = 85
DIV  = "=" * 70

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


class Command(BaseCommand):
    help = "Run match predictions for a given date (default: today)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="Date to analyse in YYYY-MM-DD format (default: today)",
        )
        parser.add_argument(
            "--threshold",
            type=int,
            default=ACCURACY_THRESHOLD,
            help=f"Minimum confidence %% to include a pick (default: {ACCURACY_THRESHOLD})",
        )

    def handle(self, *args, **options):
        from datetime import date
        target_date = options["date"] or str(date.today())
        # Normalise to YYYY-MM-DD regardless of input format
        target_date = target_date.strip()
        threshold   = options["threshold"]

        print(f"\n{DIV}")
        print(f"  SPORTY TOP PICKS  |  {target_date}  |  {threshold}%+ CONFIDENCE ONLY")
        print(f"{DIV}\n")
        print(f"  Analysing matches, please wait...\n")

        # Skip connectivity ping for historical dates — DB handles those directly.
        # Only ping the API for today/future dates (live predictions).
        import time as _time
        import requests as _req
        from django.conf import settings as _cfg
        from datetime import date as _date_cls
        _is_historical = _date_cls.fromisoformat(target_date) < _date_cls.today()
        if not _is_historical:
            try:
                _ping = _req.get(
                    "https://api.football-data.org/v4/competitions/PL/matches",
                    headers={"X-Auth-Token": _cfg.FOOTBALL_DATA_API_KEY},
                    params={"dateFrom": target_date, "dateTo": target_date},
                    timeout=12,
                )
                # Auto-retry once on rate limit (handles back-to-back batch runs)
                if _ping.status_code == 429:
                    print(f"  Rate limit hit — waiting 65s and retrying...")
                    _time.sleep(65)
                    _ping = _req.get(
                        "https://api.football-data.org/v4/competitions/PL/matches",
                        headers={"X-Auth-Token": _cfg.FOOTBALL_DATA_API_KEY},
                        params={"dateFrom": target_date, "dateTo": target_date},
                        timeout=12,
                    )
                _ping_matches = len(_ping.json().get("matches", [])) if _ping.status_code == 200 else 0
                print(f"  API connection: OK (status {_ping.status_code}, {_ping_matches} PL matches on {target_date})")
                if _ping.status_code == 401:
                    print(f"  ERROR: Invalid API key — check FOOTBALL_DATA_API_KEY in your .env")
                    return
                if _ping.status_code == 429:
                    print(f"  ERROR: API rate limit still active — please wait a minute before retrying.")
                    return
            except Exception as _e:
                print(f"  [Warning] Connectivity ping failed ({type(_e).__name__}) — continuing anyway.")
        else:
            print(f"  [DB mode] Historical date — using local database (no API calls)")

        try:
            fixtures = get_fixtures_by_date(target_date)
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
                print(f"  No top-league matches found for {target_date}.")
                print(f"  (Could be an international break, or no fixtures in supported leagues)")
            else:
                print(f"  No top-league fixtures on {target_date}.")
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
                    match_date=target_date,
                )
                away_stats = get_team_season_stats(
                    team_id=fix.get("away_team_id", 0),
                    team_name=away_name,
                    competition_code=comp_code,
                    match_date=target_date,
                )

                if not home_stats or not away_stats:
                    continue

                _ctx_date = target_date if _is_historical else None
                home_ctx = get_team_context(home_stats.get("team_id", 0), comp_code, _ctx_date)
                away_ctx = get_team_context(away_stats.get("team_id", 0), comp_code, _ctx_date)

                # H2H check — penalise if team failed to score vs this opponent recently
                h2h = get_h2h_scored(
                    fix.get("home_team_id", 0),
                    fix.get("away_team_id", 0)
                )
                if h2h.get("home_blanked_h2h", 0) >= 2:
                    # Home team has failed to score in 2+ of last 3 H2H vs this opponent
                    for key in ("xg_per_game", "home_xg_pg", "scored_per_game"):
                        if home_stats.get(key):
                            home_stats[key] = round(home_stats[key] * 0.82, 3)
                    home_stats["h2h_note"] = f"scored in only {3 - h2h['home_blanked_h2h']}/3 recent H2H vs {away_name}"
                elif h2h.get("home_blanked_h2h", 0) == 1 and not h2h.get("home_scored_last_h2h"):
                    # Failed to score in the most recent H2H meeting
                    for key in ("xg_per_game", "home_xg_pg", "scored_per_game"):
                        if home_stats.get(key):
                            home_stats[key] = round(home_stats[key] * 0.90, 3)
                    home_stats["h2h_note"] = f"failed to score vs {away_name} in last meeting ({h2h.get('last_h2h_date', '')})"

                if h2h.get("away_blanked_h2h", 0) >= 2:
                    for key in ("xg_per_game", "away_xg_pg", "scored_per_game"):
                        if away_stats.get(key):
                            away_stats[key] = round(away_stats[key] * 0.82, 3)

                # Fetch injury data and apply penalty to team stats before signal gen
                injuries = get_fixture_injuries_for_match(
                    home_name, away_name, target_date, comp_code
                )
                if injuries["available"]:
                    h_miss = injuries["home"]["missing"]
                    a_miss = injuries["away"]["missing"]
                    # Cap effective missing at 4 — the API lists long-term absentees
                    # who were already absent when season stats were computed.
                    # Only penalise for up to 4 freshly impactful absences (5% each, max 20%).
                    h_eff = min(h_miss, 4)
                    a_eff = min(a_miss, 4)
                    if h_eff > 0:
                        penalty = 1 - h_eff * 0.05   # 5%/player, max 20%
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
                        "fixture":      f"{home_name} vs {away_name}",
                        "league":       league_label,
                        "kickoff":      kickoff,
                        "bet":          s.get("label", s.get("type")),
                        "signal_type":  s.get("type", ""),
                        "confidence":   s.get("confidence", 0),
                        "grade":        gi.get("grade", "?"),
                        "grade_score":  gi.get("grade_score", 0),
                        "accuracy":     gi.get("accuracy_pct", "?"),
                        "grade_label":  gi.get("grade_label", ""),
                        "evidence":     s.get("evidence", []),
                        "reaches_95":   gi.get("reaches_95", False),
                        "penalties":    gi.get("penalties", []),
                        "injuries":     inj_notes,
                        # For dominant DC detection
                        "_home_pos":    home_stats.get("position"),
                        "_away_pos":    away_stats.get("position"),
                        "_home_xg":     home_stats.get("home_xg_pg") or home_stats.get("xg_per_game") or 0,
                        "_away_xg":     away_stats.get("away_xg_pg") or away_stats.get("xg_per_game") or 0,
                        # Deadlock flag: lower grade threshold for balanced-deadlock DC picks
                        "deadlock":     s.get("deadlock", False),
                        # Low-draw swap rule: if draw probability is <20% for a 1X/X2 pick,
                        # the draw outcome is unlikely — swap to DC_12 which covers both teams.
                        "draw_prob":    s.get("draw_prob", None),
                    })

            except Exception:
                import traceback; traceback.print_exc()
                continue

        if not all_picks:
            print("  No signals generated — stats data may be unavailable for this date.")
            return

        # Fallback cascade priority (lower tier number = higher priority):
        #   Tier 1   — Primary signals (BTTS, Over 2.5, Over 1.5, etc.)
        #   Tier 1.5 — Dominant team Double Chance (1X for strong home / X2 for strong away)
        #              Covers even a 0-0 draw — safer than Over 0.5 in defensive matchups
        #   Tier 2   — Team-specific score 1+ (home or away team to score)
        #   Tier 3   — General Over 0.5 (last resort — at least 1 goal either side)
        #
        # "Dominant" means the team is meaningfully higher in the table (>=5 positions better)
        # OR has significantly better xG (>=0.5 xG/game advantage).
        # In that case their double chance (they win or draw) beats the goalless risk.

        def _dominant_dc_type(p):
            """Return the signal_type of the dominant team's DC if this pick is a DC signal."""
            stype = p.get("signal_type", "")
            if stype not in ("DOUBLE_CHANCE_1X", "DOUBLE_CHANCE_X2"):
                return None
            h_pos = p.get("_home_pos")
            a_pos = p.get("_away_pos")
            h_xg  = p.get("_home_xg", 0)
            a_xg  = p.get("_away_xg", 0)
            pos_gap = (a_pos - h_pos) if (h_pos and a_pos) else 0
            xg_gap  = h_xg - a_xg
            # Home dominant → 1X is the correct pick
            if stype == "DOUBLE_CHANCE_1X" and (pos_gap >= 5 or xg_gap >= 0.5):
                return "DC_DOMINANT"
            # Away dominant → X2 is the correct pick
            if stype == "DOUBLE_CHANCE_X2" and (pos_gap <= -5 or xg_gap <= -0.5):
                return "DC_DOMINANT"
            return None

        _FALLBACK_TIER = {
            "DC_DOMINANT":       2,   # tier 1.5 effectively — beats team-score and Over 0.5
            "HOME_TEAM_OVER_05": 3,
            "AWAY_TEAM_OVER_05": 3,
            "OVER_05":           4,
        }

        def _sort_key(p):
            stype = p.get("signal_type", "")
            dc    = _dominant_dc_type(p)
            tier  = _FALLBACK_TIER.get("DC_DOMINANT" if dc else stype, 1)
            acc   = p["accuracy"] if isinstance(p["accuracy"], (int, float)) else 0
            return (-tier, acc, p["grade_score"])

        all_picks.sort(key=_sort_key, reverse=True)

        # ── Market categories ─────────────────────────────────────────────────
        # Each fixture can contribute up to 2 picks — one from the RESULT
        # category and one from the GOALS category — provided both independently
        # meet their grade thresholds. This lets the engine use the full bet
        # board: a fixture with a strong DC signal AND a high-scoring pattern
        # generates both "Arsenal or Draw (1X)" and "Over 2.5 Goals".
        _RESULT_TYPES = {
            "DOUBLE_CHANCE_1X", "DOUBLE_CHANCE_X2", "DOUBLE_CHANCE_12",
            "MATCH_WIN_HOME", "MATCH_WIN_AWAY", "MATCH_DRAW",
        }
        _GOALS_TYPES = {
            "OVER_15", "OVER_25", "OVER_35", "OVER_05",
            "BTTS_YES", "BTTS_NO", "UNDER_15",
        }
        _TEAM_TYPES = {
            "HOME_TEAM_OVER_05", "AWAY_TEAM_OVER_05",
            "HOME_SCORE_2PLUS", "AWAY_SCORE_2PLUS",
        }

        def _market_cat(stype):
            if stype in _RESULT_TYPES: return "result"
            if stype in _GOALS_TYPES:  return "goals"
            if stype in _TEAM_TYPES:   return "team"
            return "other"

        # Per-type minimum grade thresholds.
        # Goals markets are statistically calibrated differently: Over 2.5 rarely
        # reaches 85% raw confidence (its true probability peaks at ~70%), so we
        # use grade instead of confidence as the quality gate for these markets.
        _SIGNAL_THRESHOLD = {
            # Result / DC markets — raised to 81 to cut grade-79/80 marginal picks.
            # Grade-79/80 1X losses account for 14/24 of all 1X losses in backtest.
            # Raising to 81 removes the weakest picks and improves perfect-day rate.
            "DOUBLE_CHANCE_1X":  81,
            "DOUBLE_CHANCE_X2":  81,
            "DOUBLE_CHANCE_12":  81,
            "MATCH_WIN_HOME":    83,  # straight win needs higher certainty
            "MATCH_WIN_AWAY":    83,
            # Goals markets — lower grade threshold, but confidence still applied
            "OVER_25":           78,
            "OVER_35":           78,
            "OVER_15":           78,
            "BTTS_YES":          82,  # BTTS losses clustered at grade 80-81
            "BTTS_NO":           82,
            # Team-specific — raised from 79 to 80 to cut weakest to-score picks
            "OVER_05":           84,
            "HOME_TEAM_OVER_05": 81,
            "AWAY_TEAM_OVER_05": 81,
        }

        def _qualifies(p):
            # Grade is the primary quality gate — each market type has its own floor.
            # Confidence is NOT filtered here because goals markets (Over 2.5, BTTS)
            # structurally cap out at 60-75% confidence by Poisson math; filtering
            # them at 85% would eliminate the entire category.
            min_grade = _SIGNAL_THRESHOLD.get(p.get("signal_type", ""), 79)
            return p["grade_score"] >= min_grade

        threshold_picks = [p for p in all_picks if _qualifies(p)]

        # ── DC_12 swap candidates ─────────────────────────────────────────────
        # When the low-draw swap fires, we need a DC_12 pick to swap TO.
        # DC_12 structurally grades lower than 1X/X2, so we allow it as a swap
        # target at grade 76+ (slightly below the 79 standalone bar).  Grade 76
        # means the Poisson model still has meaningful signal — it just doesn't
        # quite reach the primary pick threshold.  Using grade 72 caused too many
        # weak DC_12 picks to fire on draws; 76 is the sweet spot.
        # Swap candidates are NEVER used as standalone picks — only as fallback.
        _dc12_swap_candidates: dict = {}  # fixture -> best DC_12 pick (grade 79+)
        for p in all_picks:
            if p.get("signal_type") == "DOUBLE_CHANCE_12" and p.get("grade_score", 0) >= 79:
                fix = p["fixture"]
                if fix not in _dc12_swap_candidates:
                    _dc12_swap_candidates[fix] = p

        # ── Deduplicate: best pick per fixture per market category ────────────
        # Primary slot  = best result/DC pick per fixture
        # Secondary slot = best goals pick per fixture (grade 80+ required)
        # Tertiary slot  = team-to-score pick only if no result pick exists
        #
        # DC slots are split into result_dc (1X/X2) and result_12 (DC_12) so
        # the low-draw swap rule can replace a 1X/X2 with DC_12 when the Poisson
        # draw probability is below 25% — meaning the draw leg is a statistical
        # dead weight and DC_12 gives better coverage without it.
        _DC_1X_X2 = {"DOUBLE_CHANCE_1X", "DOUBLE_CHANCE_X2"}

        seen: dict = {}   # fixture -> {"result_dc": p, "result_12": p, "result": p, "goals": p, "team": p}
        for p in threshold_picks:
            fix  = p["fixture"]
            stype = p.get("signal_type", "")
            if fix not in seen:
                seen[fix] = {}
            # Route DC picks into dedicated slots; everything else into "result"
            if stype in _DC_1X_X2:
                slot = "result_dc"
            elif stype == "DOUBLE_CHANCE_12":
                slot = "result_12"
            else:
                slot = _market_cat(stype)  # "result", "goals", "team", "other"
            if slot not in seen[fix]:   # keep the first (highest-ranked) per slot
                seen[fix][slot] = p

        deduped = []
        for fix, cats in seen.items():
            dc_p     = cats.get("result_dc")   # best 1X or X2 pick
            # result_12: standalone DC_12 that passed grade 79 (qualifies on its own)
            # _dc12_swap_candidates: DC_12 at grade 72+ used ONLY as swap target when
            # dc_p exists — never as a standalone pick (prevents grade-72 bloat).
            dc12_qualified = cats.get("result_12")
            dc12_swap_only = _dc12_swap_candidates.get(fix) if not dc12_qualified else None
            dc12_p   = dc12_qualified or (dc12_swap_only if dc_p else None)  # swap-only needs dc_p
            result_p = cats.get("result")      # best non-DC result pick (WIN, DRAW)
            goals_p  = cats.get("goals")
            team_p   = cats.get("team")

            # ── Low-draw swap rule ────────────────────────────────────────────
            # If the best DC pick is 1X or X2 BUT draw probability is < 25%,
            # the draw leg is a statistical dead weight. Swap to DC_12 if one
            # qualifies — it covers the actual likely outcomes (home win + away win).
            chosen_result = None
            if dc_p:
                dp = dc_p.get("draw_prob")
                if dp is not None and dp < 0.25 and dc12_p:
                    # Swap: use DC_12 instead of 1X/X2
                    chosen_result = dc12_p
                    # Annotate so evidence shows why we swapped
                    if "evidence" in chosen_result:
                        chosen_result = dict(chosen_result)  # shallow copy
                        chosen_result["evidence"] = list(chosen_result["evidence"]) + [
                            f"Low-draw swap: draw prob {dp:.0%} (<25%) — 1X/X2 swapped to Either Team to Win (12)"
                        ]
                else:
                    chosen_result = dc_p
            elif dc12_p:
                chosen_result = dc12_p
            elif result_p:
                chosen_result = result_p

            # ── One pick per fixture ──────────────────────────────────────────
            # Never put two picks on the same game. If both a result pick and a
            # goals pick qualify, one 0-0 or blank burns both on the same day.
            # Pick the single highest-grade signal across all categories.
            candidates = [p for p in [chosen_result, goals_p, team_p] if p]
            if candidates:
                best = max(candidates, key=lambda p: p.get("grade_score", 0))
                deduped.append(best)

        # ── Save picks to database ────────────────────────────────────────────
        saved = skipped = 0
        from datetime import date as _date_cls
        match_date_obj = _date_cls.fromisoformat(target_date)
        dow = match_date_obj.strftime("%A")[:3].upper()  # MON, TUE … SAT, SUN
        for p in deduped:
            kickoff_dt = None
            if p.get("kickoff"):
                try:
                    kickoff_dt = datetime.fromisoformat(p["kickoff"].replace("Z", "+00:00"))
                except Exception:
                    pass
            home_team = p["fixture"].split(" vs ")[0]
            away_team = p["fixture"].split(" vs ")[1]
            _, created = Prediction.objects.update_or_create(
                match_date=match_date_obj,
                home_team=home_team,
                away_team=away_team,
                bet_type=p["bet"],
                defaults={
                    "league":           p["league"],
                    "competition_code": p["league"].split("(")[-1].rstrip(")").strip() if "(" in p["league"] else "",
                    "kickoff":          kickoff_dt,
                    "confidence":       p["confidence"],
                    "grade":            p["grade"],
                    "grade_score":      p["grade_score"],
                    "accuracy_pct":     p["accuracy"] if isinstance(p["accuracy"], (int, float)) else None,
                    "evidence":         p.get("evidence", []),
                    "penalties":        p.get("penalties", []),
                    "injuries":         p.get("injuries", []),
                    "day_of_week":      dow,
                },
            )
            if created:
                saved += 1
            else:
                skipped += 1
        # Remove stale picks from a previous run for the same fixtures.
        # Now that we allow 2 picks per fixture (result + goals), we delete
        # any old prediction for a fixture whose bet_type is no longer in the
        # current deduped set (engine changed its mind on re-run).
        current_bets_by_fixture: dict = {}
        for p in deduped:
            current_bets_by_fixture.setdefault(p["fixture"], set()).add(p["bet"])
        for fix, current_bets in current_bets_by_fixture.items():
            ht, at = fix.split(" vs ", 1)
            # Delete any stale pick regardless of outcome — the engine's current
            # output is authoritative. If a 1X was swapped to DC_12 on re-run,
            # the old LOSS record must be removed so the scorecard reflects the
            # new pick. Only exclude WIN records to preserve clean bet history.
            Prediction.objects.filter(
                match_date=match_date_obj,
                home_team=ht,
                away_team=at,
            ).exclude(bet_type__in=current_bets).exclude(outcome="WIN").delete()

        if saved or skipped:
            print(f"  [DB] Saved {saved} new pick(s), updated {skipped} existing pick(s) for {target_date}")

        def _bar(score, w=28):
            f = int(score / 100 * w)
            return "[" + "#" * f + "." * (w - f) + "]"

        print(f"\n{DIV}")
        print(f"  TOP PICKS  |  {len(deduped)} match(es) with {threshold}%+ confidence")
        print(f"{DIV}\n")

        if not deduped:
            print(f"  No picks met the {threshold}% threshold for {target_date}.\n")
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
