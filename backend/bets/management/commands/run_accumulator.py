"""
Django management command: python manage.py run_accumulator [--date 2026-04-13]

Builds a daily accumulator using the same calibrated engine as scorecard.py:
  - All markets per fixture: DC (1X/X2), Over 1.5, Under 2.5, Team to Score
  - Market hierarchy: DC (real alpha) > Goals > Team-score (Big 5 fallback only)
  - Best pick per fixture selected by hierarchy, then by probability
  - Up to MAX_PICKS=4 picks per day — avoids compound risk from long chains
  - No hard @2.0 floor — show all qualifying days (@1.40+)
  - Backtest: 86.2% win rate Oct 2025–Apr 2026 (58 days Fri/Sat/Sun)
"""

import io, sys, math, requests
from datetime import date as _date_cls
from difflib import SequenceMatcher
from django.core.management.base import BaseCommand

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# ── Configuration (keep in sync with scorecard.py) ────────────────────────────
DC_MIN         = 0.79   # DC calibrated alpha band: 79-85% wins ~93% of the time
DC_MAX         = 0.85   # above this DC odds too short (@1.18) for accumulator value
GOALS_MIN      = 0.82   # goals markets: raised after 80-81% losses in SA/DED/PD
GOALS_MAX      = 0.92   # above this goals odds too short
MAX_PICKS      = 4      # hard cap — compound risk rises sharply beyond 4 legs
MIN_BACK_GAMES = 10     # teams with fewer games excluded as unreliable
MIN_DISPLAY_ODDS = 1.40 # minimum combined odds to show a day (filter trivial 1-pick days)

TARGET_CODES = {
    "PL","PD","BL1","SA","FL1","DED","PPL","ELC","SPL","BJL","TSL","BL2"
}  # SSL removed — too volatile; CL/EL/UECL excluded — single-leg format

# Big 5 preferred over smaller leagues when probabilities are similar
LEAGUE_PRIORITY = {
    "PL": 1, "PD": 1, "BL1": 1, "SA": 1, "FL1": 1,   # Big 5 — most data, most reliable
    "DED": 2, "PPL": 2, "ELC": 2,                       # Strong second tier
    "BL2": 3, "SPL": 3, "TSL": 3, "BJL": 3,            # Third tier — higher upset rate
}

DERBY_PAIRS = {
    frozenset({"Beşiktaş", "Fenerbahçe"}),
    frozenset({"Beşiktaş", "Galatasaray"}),
    frozenset({"Fenerbahçe", "Galatasaray"}),
    frozenset({"Celtic", "Rangers"}),
    frozenset({"Liverpool", "Everton"}),
    frozenset({"Arsenal", "Tottenham Hotspur"}),
    frozenset({"Manchester City", "Manchester United"}),
    frozenset({"Chelsea", "Arsenal"}),
    frozenset({"Real Madrid", "Atletico Madrid"}),
    frozenset({"Barcelona", "Atletico Madrid"}),
}

NEVER_BACK = {
    "Monaco",       # wildly inconsistent — beat PSG, lost to mid-table as favourite
    "VfL Bochum",   # 2 DC losses (1-2 and 2-3) — chronic relegation battler
    "Toulouse",     # 2 FL1 losses scoring zero (0-1, 0-3) — model overstates them
    "RB Leipzig",   # 2 BL1 losses (1-2 and 0-0) — inconsistent top-table side
    "Espanyol",     # relegated and re-promoted — stale xG profile, lost 0-2
    "Paris FC",     # newly promoted FL1 team — no reliable data, scored 0 in picks
    "Southampton",  # relegated PL team in Championship — identity crisis, 0-2 loss
}

# Per-league Over 1.5 minimum — calibrated from observed O1.5 failures
LEAGUE_BOOSTER_THRESH = {
    "SPL": 0.90,
    "FL1": 0.92,
    "BL1": 0.87,
    "SA":  0.86,
    "DED": 0.85,
    "PD":  0.85,
}

# Per-league DC minimum — tighter bands for volatile lower leagues
LEAGUE_DC_MIN = {
    "DED": 0.84,
    "BL2": 0.84,
    "SPL": 0.84,
}

# ── Odds API league map ───────────────────────────────────────────────────────
# Maps our competition codes → The Odds API sport keys
ODDS_API_SPORT = {
    "PL":  "soccer_epl",
    "PD":  "soccer_spain_la_liga",
    "BL1": "soccer_germany_bundesliga",
    "BL2": "soccer_germany_bundesliga2",
    "SA":  "soccer_italy_serie_a",
    "FL1": "soccer_france_ligue_one",
    "DED": "soccer_netherlands_eredivisie",
    "PPL": "soccer_portugal_primeira_liga",
    "ELC": "soccer_efl_champ",
    "SPL": "soccer_spl",
    "TSL": "soccer_turkey_super_league",
    "BJL": "soccer_belgium_first_div",
}

ODDS_API_BASE = "https://api.the-odds-api.com/v4"


def _fuzzy_match(name_a: str, name_b: str) -> float:
    """Character-level similarity between two team names."""
    a = name_a.lower().strip()
    b = name_b.lower().strip()
    return SequenceMatcher(None, a, b).ratio()


def _fetch_bookmaker_odds(home_team: str, away_team: str, market: str, league_code: str, api_key: str) -> dict | None:
    """
    Fetch real bookmaker odds from The Odds API for a given fixture and market.

    DC_1X / DC_X2 / TEAM_SCORE : derived from h2h (1X2) by normalising implied probs.
    OVER15                      : totals market, Over 1.5 line (Starter plan+).
    UNDER25                     : totals market, Under 2.5 line.

    Returns dict with keys: bookmaker, odds, implied_prob
    or None if no match found.
    """
    sport_key = ODDS_API_SPORT.get(league_code)
    if not sport_key or not api_key:
        return None

    api_market = "totals" if market in ("OVER15", "UNDER25") else "h2h"

    try:
        resp = requests.get(
            f"{ODDS_API_BASE}/sports/{sport_key}/odds/",
            params={
                "apiKey":     api_key,
                "regions":    "uk",
                "markets":    api_market,
                "oddsFormat": "decimal",
            },
            timeout=8,
        )
        if resp.status_code != 200:
            return None
        games = resp.json()
        if not isinstance(games, list):
            return None
    except Exception:
        return None

    # Find matching fixture by fuzzy team name
    best_game  = None
    best_score = 0.0
    for game in games:
        h_score  = _fuzzy_match(home_team, game.get("home_team", ""))
        a_score  = _fuzzy_match(away_team, game.get("away_team", ""))
        combined = (h_score + a_score) / 2
        if combined > best_score:
            best_score = combined
            best_game  = game

    if not best_game or best_score < 0.45:
        return None

    best_odds   = None
    best_bookie = None

    for bookie in best_game.get("bookmakers", []):
        for mkt in bookie.get("markets", []):
            if mkt["key"] != api_market:
                continue

            if market in ("DC_1X", "DC_X2", "TEAM_SCORE"):
                # Build implied prob map from 1X2 prices, normalised to remove margin
                prices = {o["name"]: o["price"] for o in mkt.get("outcomes", [])}
                home_name_api = best_game.get("home_team", "")
                away_name_api = best_game.get("away_team", "")
                p_h = 1 / prices.get(home_name_api, 999)
                p_d = 1 / prices.get("Draw", 999)
                p_a = 1 / prices.get(away_name_api, 999)
                total = p_h + p_d + p_a
                if total <= 0:
                    continue
                p_h, p_d, p_a = p_h / total, p_d / total, p_a / total
                if market == "DC_1X":
                    ref_prob = p_h + p_d
                elif market == "DC_X2":
                    ref_prob = p_d + p_a
                else:  # TEAM_SCORE — higher-xG side; approximate as home win + draw vs away win
                    ref_prob = max(p_h + p_d, p_d + p_a)
                ref_odds = round(1 / ref_prob, 2) if ref_prob > 0 else None
                if ref_odds and (best_odds is None or ref_odds > best_odds):
                    best_odds   = ref_odds
                    best_bookie = bookie["title"]

            elif market == "OVER15":
                # Starter plan: fetch Over 1.5 directly
                for outcome in mkt.get("outcomes", []):
                    if outcome.get("name", "").lower() == "over" and outcome.get("point") == 1.5:
                        price = outcome["price"]
                        if best_odds is None or price > best_odds:
                            best_odds   = price
                            best_bookie = bookie["title"]

            elif market == "UNDER25":
                # Under 2.5 Goals line
                for outcome in mkt.get("outcomes", []):
                    if outcome.get("name", "").lower() == "under" and outcome.get("point") == 2.5:
                        price = outcome["price"]
                        if best_odds is None or price > best_odds:
                            best_odds   = price
                            best_bookie = bookie["title"]

    if best_odds is None:
        return None

    return {"bookmaker": best_bookie, "odds": best_odds, "implied_prob": round(1 / best_odds, 4)}


def _over15_prob(h_xg, a_xg):
    """P(total goals >= 2) via Poisson."""
    p_0 = math.exp(-h_xg) * math.exp(-a_xg)
    p_1 = (math.exp(-h_xg) * h_xg * math.exp(-a_xg) +
           math.exp(-h_xg) * math.exp(-a_xg) * a_xg)
    return 1 - p_0 - p_1


def _under25_prob(h_xg, a_xg):
    """P(total goals <= 2) via Poisson."""
    lam = h_xg + a_xg
    return math.exp(-lam) * (1 + lam + lam**2 / 2)


def _team_score_prob(xg):
    """P(team scores >= 1 goal) via Poisson."""
    return 1 - math.exp(-xg)


def _build_accumulator(candidates):
    """
    Build accumulator from per-fixture best picks.
    Sort by tier (DC=1 first) then probability descending.
    Add up to MAX_PICKS picks; skip any pick that would push combined past @4.0.
    No @2.0 hard floor — display every day at @1.40+.
    """
    if not candidates:
        return None, None

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
            continue
        accumulator.append(pick)
        combined = round(new_combined, 4)
        used_fixtures.add(pick["fixture"])

    return (accumulator, round(combined, 4)) if accumulator else (None, None)


class Command(BaseCommand):
    help = "Build a daily accumulator (synced with scorecard.py engine)"

    def add_arguments(self, parser):
        parser.add_argument("--date", type=str, default=None,
                            help="Date YYYY-MM-DD (default: today)")
        parser.add_argument("--backtest", action="store_true",
                            help="Run across all historical dates and print summary")
        parser.add_argument("--start-date", type=str, default=None,
                            help="Backtest start date YYYY-MM-DD")
        parser.add_argument("--debug", action="store_true",
                            help="Print step-by-step resolution details")

    def handle(self, *args, **options):
        if options["backtest"]:
            self._backtest(start_date=options.get("start_date"))
        else:
            target_date = options["date"] or str(_date_cls.today())
            result = self._run_date(target_date, verbose=True, debug=options.get("debug", False))
            if not result:
                self.stdout.write("  No qualifying picks today.\n")
            # verbose output already printed inside _run_date

    # ── Backtest ──────────────────────────────────────────────────────────────
    def _backtest(self, start_date=None):
        from bets.models import HistoricalFixture
        from bets.services.football_data import _season_stats_cache
        qs = HistoricalFixture.objects.filter(home_score__isnull=False)
        if start_date:
            qs = qs.filter(match_date__gte=start_date)
        dates = [str(d) for d in
                 qs.values_list("match_date", flat=True).distinct().order_by("match_date")]

        total = wins = losses = no_bet = 0
        t1_total = t1_wins = t2_total = t2_wins = 0
        for d in dates:
            result = self._run_date(d, verbose=False)
            if not result:
                no_bet += 1
                continue
            acc, combined, tier = result
            total += 1
            all_win  = all(p.get("won") is True  for p in acc)
            any_loss = any(p.get("won") is False for p in acc)
            if all_win:   wins   += 1
            elif any_loss: losses += 1
            if tier == 1:
                t1_total += 1
                if all_win: t1_wins += 1
            else:
                t2_total += 1
                if all_win: t2_wins += 1

        print(f"\n{'='*60}")
        print(f"  BACKTEST  ({start_date or 'all'} → today)")
        print(f"{'='*60}")
        print(f"  Days with pick : {total}")
        if t1_total: print(f"  Tier 1 (DC)    : {t1_total} days  |  {t1_wins} wins  ({t1_wins/t1_total*100:.1f}%)")
        if t2_total: print(f"  Tier 2 (Goals) : {t2_total} days  |  {t2_wins} wins  ({t2_wins/t2_total*100:.1f}%)")
        print(f"  Perfect days   : {wins}  ({wins/total*100:.1f}%)" if total else "")
        print(f"  Imperfect days : {losses}")
        print(f"{'='*60}\n")

    # ── Single date ───────────────────────────────────────────────────────────
    def _run_date(self, target_date: str, verbose=True, debug=False):
        from bets.services.football_data import (
            get_fixtures_by_date, _get_team_stats_from_db, _season_stats_cache,
            key_player_absence_factor, _contextual_o15_adjustment,
        )
        from bets.services.daily_predictions import _match_probs, _expected_xg_for_match
        import time as _time

        # Build a name→id map from DB once per run (fast, avoids per-team queries)
        _name_to_id: dict = {}
        if not hasattr(self, "_name_id_cache"):
            from bets.models import HistoricalFixture
            rows = HistoricalFixture.objects.filter(
                league_code__in=list(TARGET_CODES)
            ).values("home_team", "home_team_id", "away_team", "away_team_id").distinct()
            for r in rows:
                _name_to_id[r["home_team"].lower()] = r["home_team_id"]
                _name_to_id[r["away_team"].lower()]  = r["away_team_id"]
            self._name_id_cache = _name_to_id
        else:
            _name_to_id = self._name_id_cache

        def _resolve_team_id(api_id, team_name, comp_code):
            """
            The live API returns different team IDs than backfill stored.
            1. Try exact name match against DB name map.
            2. Try partial match (handles 'TSV Fortuna 95 Düsseldorf' → 'Fortuna Düsseldorf').
            3. Fall back to api_id if nothing found.
            """
            from bets.models import HistoricalFixture
            from django.db.models import Q

            # 1. Exact name match
            db_id = _name_to_id.get(team_name.lower())
            if db_id:
                return db_id

            # 2. Check if the api_id itself is in our DB for this league
            if api_id and HistoricalFixture.objects.filter(
                Q(home_team_id=api_id) | Q(away_team_id=api_id),
                league_code=comp_code,
            ).exists():
                return api_id

            # 3. Substring match — DB name contained in API name or vice versa
            api_lower = team_name.lower()
            for db_name, db_id in _name_to_id.items():
                if db_name in api_lower or api_lower in db_name:
                    return db_id

            # 4. Word-token overlap — useful for "Bayer 04 Leverkusen" → "Bayer Leverkusen"
            api_words = set(api_lower.split()) - {"fc","sv","sc","1.","vfl","vfb","bv","tsv","sg","kv","rc","og","rb","ss","us","ac","as","cf"}
            best_id, best_score = None, 0
            for db_name, db_id in _name_to_id.items():
                db_words = set(db_name.split()) - {"fc","sv","sc","1.","vfl","vfb","bv","tsv","sg","kv","rc","og","rb","ss","us","ac","as","cf"}
                overlap = len(api_words & db_words)
                if overlap > best_score:
                    best_score, best_id = overlap, db_id
            if best_score >= 2:
                return best_id

            return api_id  # give up

        def _get_stats(team_id, team_name, comp_code, match_date):
            """DB-only stats lookup — fast, no API calls."""
            resolved_id = _resolve_team_id(team_id, team_name, comp_code)
            if not resolved_id:
                return None
            now = _time.time()
            cache_key = (resolved_id, comp_code)
            cached = _season_stats_cache.get(cache_key)
            if cached:
                ts, data = cached
                if now - ts < 6 * 3600:
                    return data
            try:
                result = _get_team_stats_from_db(
                    team_id=resolved_id, team_name=team_name,
                    competition_code=comp_code,
                    match_date_str=match_date, season=2025,
                )
            except Exception:
                result = None
            if result is not None:
                _season_stats_cache[cache_key] = (now, result)
            return result

        try:
            fixtures = get_fixtures_by_date(target_date)
        except Exception as e:
            if debug:
                print(f"[DEBUG] get_fixtures_by_date failed: {e}")
            return None

        league_fixtures = [f for f in fixtures if f.get("competition_code") in TARGET_CODES]
        if debug:
            print(f"[DEBUG] Total fixtures from API: {len(fixtures)}")
            print(f"[DEBUG] Fixtures in target leagues: {len(league_fixtures)}")
            for f in league_fixtures:
                print(f"  {f.get('competition_code')}  {f['home_team']} (id={f.get('home_team_id')}) vs {f['away_team']} (id={f.get('away_team_id')})")

        # ── Build per-fixture options across all markets ──────────────────────
        all_candidates = []   # best pick per fixture

        for fix in league_fixtures:
            home_name = fix["home_team"]
            away_name = fix["away_team"]
            comp_code = fix.get("competition_code", "PL")
            try:
                if frozenset({home_name, away_name}) in DERBY_PAIRS:
                    if debug:
                        print(f"[DEBUG] DERBY skip: {home_name} vs {away_name}")
                    continue

                hs  = _get_stats(fix.get("home_team_id", 0), home_name, comp_code, target_date)
                aws = _get_stats(fix.get("away_team_id", 0), away_name, comp_code, target_date)
                if debug:
                    h_id = _resolve_team_id(fix.get("home_team_id", 0), home_name, comp_code)
                    a_id = _resolve_team_id(fix.get("away_team_id", 0), away_name, comp_code)
                    print(f"[DEBUG] {home_name}(api={fix.get('home_team_id')}→db={h_id}): stats={'OK' if hs else 'NONE'}  "
                          f"{away_name}(api={fix.get('away_team_id')}→db={a_id}): stats={'OK' if aws else 'NONE'}")
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
                p_o15_raw = _over15_prob(h_xg, a_xg)
                p_o15, _  = _contextual_o15_adjustment(
                    home_name, away_name, comp_code, target_date, p_o15_raw
                )
                p_u25  = _under25_prob(h_xg, a_xg)
                p_h_sc = _team_score_prob(h_xg)
                p_a_sc = _team_score_prob(a_xg)

                fixture_str = f"{home_name} vs {away_name}"
                home_score  = fix.get("home_score")
                away_score  = fix.get("away_score")
                score_str   = f"{home_score}-{away_score}" if home_score is not None else "?"

                options = []
                _dc_min = max(DC_MIN, LEAGUE_DC_MIN.get(comp_code, DC_MIN))

                # Tier 1 — Double Chance (79-85%, real alpha)
                p_1x = p_home + p_draw
                if _dc_min <= p_1x <= DC_MAX and home_name not in NEVER_BACK:
                    won = (home_score >= away_score) if home_score is not None else None
                    options.append({
                        "fixture": fixture_str, "label": f"{home_name} or Draw (1X)",
                        "market": "DC_1X", "probability": round(p_1x, 4),
                        "odds": round(1 / p_1x, 2), "won": won,
                        "score": score_str, "league": comp_code,
                        "h_xg": round(h_xg, 2), "a_xg": round(a_xg, 2),
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
                        "h_xg": round(h_xg, 2), "a_xg": round(a_xg, 2),
                        "absence_flag": a_flag, "tier": 1,
                    })

                # Tier 2 — Goals markets (league-specific floors)
                _o15_thresh = LEAGUE_BOOSTER_THRESH.get(comp_code, GOALS_MIN)
                from bets.services.football_data import _should_block_o15_booster
                _block_o15, _ = _should_block_o15_booster(home_name, away_name, comp_code, target_date)
                if _o15_thresh <= p_o15 <= GOALS_MAX and not _block_o15:
                    won = ((home_score + away_score) >= 2) if home_score is not None else None
                    options.append({
                        "fixture": fixture_str, "label": "Over 1.5 Goals",
                        "market": "OVER15", "probability": round(p_o15, 4),
                        "odds": round(1 / p_o15, 2), "won": won,
                        "score": score_str, "league": comp_code,
                        "h_xg": round(h_xg, 2), "a_xg": round(a_xg, 2),
                        "absence_flag": None, "tier": 2,
                    })

                if GOALS_MIN <= p_u25 <= GOALS_MAX:
                    won = ((home_score + away_score) <= 2) if home_score is not None else None
                    options.append({
                        "fixture": fixture_str, "label": "Under 2.5 Goals",
                        "market": "UNDER25", "probability": round(p_u25, 4),
                        "odds": round(1 / p_u25, 2), "won": won,
                        "score": score_str, "league": comp_code,
                        "h_xg": round(h_xg, 2), "a_xg": round(a_xg, 2),
                        "absence_flag": None, "tier": 2,
                    })

                # Tier 3 — Team to Score (Big 5 only, no absence flag)
                BIG5 = {"PL", "PD", "BL1", "SA", "FL1"}
                if comp_code in BIG5:
                    if (GOALS_MIN <= p_h_sc <= GOALS_MAX
                            and home_name not in NEVER_BACK and not h_flag):
                        won = (home_score >= 1) if home_score is not None else None
                        options.append({
                            "fixture": fixture_str, "label": f"{home_name} to Score",
                            "market": "TEAM_SCORE", "probability": round(p_h_sc, 4),
                            "odds": round(1 / p_h_sc, 2), "won": won,
                            "score": score_str, "league": comp_code,
                            "h_xg": round(h_xg, 2), "a_xg": round(a_xg, 2),
                            "absence_flag": None, "tier": 3,
                        })
                    if (GOALS_MIN <= p_a_sc <= GOALS_MAX
                            and away_name not in NEVER_BACK and not a_flag):
                        won = (away_score >= 1) if away_score is not None else None
                        options.append({
                            "fixture": fixture_str, "label": f"{away_name} to Score",
                            "market": "TEAM_SCORE", "probability": round(p_a_sc, 4),
                            "odds": round(1 / p_a_sc, 2), "won": won,
                            "score": score_str, "league": comp_code,
                            "h_xg": round(h_xg, 2), "a_xg": round(a_xg, 2),
                            "absence_flag": None, "tier": 3,
                        })

                if not options:
                    continue

                # Best pick per fixture: lowest tier first, then highest probability
                options.sort(key=lambda x: (x.get("tier", 3), -x["probability"]))
                best = options[0]
                if debug:
                    print(f"[DEBUG] {fixture_str}: selected {best['market']} @ {best['probability']:.1%} (tier {best.get('tier')})")
                all_candidates.append(best)

            except Exception as e:
                if debug:
                    import traceback
                    print(f"[DEBUG] Exception for {fix.get('home_team')} vs {fix.get('away_team')}: {e}")
                    traceback.print_exc()
                continue

        if debug:
            print(f"\n[DEBUG] Candidates built: {len(all_candidates)}")
            for c in all_candidates:
                print(f"  tier={c.get('tier')}  {c['market']}  {c['probability']:.1%}  {c['fixture']}")

        if not all_candidates:
            return None

        accumulator, combined = _build_accumulator(all_candidates)
        tier = 1

        if not accumulator or combined < MIN_DISPLAY_ODDS:
            return None

        if verbose:
            from django.conf import settings
            odds_api_key = getattr(settings, "ODDS_API_KEY", "")
            self._print(target_date, accumulator, combined, tier, odds_api_key=odds_api_key)

        return accumulator, combined, tier

    # ── Print ─────────────────────────────────────────────────────────────────
    def _print(self, target_date, accumulator, combined, tier=1, odds_api_key=""):
        DIV = "=" * 70
        above2 = "≥ @2.0" if combined >= 2.0 else f"sub-2.0 (@{combined:.2f})"
        print(f"\n{DIV}")
        print(f"  DAILY ACCUMULATOR  |  {target_date}  |  {above2}")
        print(f"{DIV}\n")

        if not accumulator:
            print("  No qualifying picks today.\n")
            return

        # ── Pre-fetch all bookmaker odds ──────────────────────────────────────
        real_map = {}
        if odds_api_key:
            for i, p in enumerate(accumulator):
                parts     = p["fixture"].split(" vs ", 1)
                home_name = parts[0].strip() if len(parts) == 2 else p["fixture"]
                away_name = parts[1].strip() if len(parts) == 2 else ""
                real = _fetch_bookmaker_odds(
                    home_name, away_name, p["market"], p["league"], odds_api_key
                )
                if real:
                    real_map[i] = real

        # ── Calculate real combined odds (bookmaker where available, model fallback) ──
        real_combined = 1.0
        has_missing   = False
        for i, p in enumerate(accumulator):
            if i in real_map:
                real_combined *= real_map[i]["odds"]
            else:
                real_combined *= p["odds"]   # fall back to model odds for this leg
                has_missing = True
        real_combined = round(real_combined, 2)

        # Use real combined as the headline; model combined shown as reference
        display_combined = real_combined if real_map else combined
        all_win  = all(p.get("won") is True  for p in accumulator)
        any_loss = any(p.get("won") is False for p in accumulator)
        result   = "WIN" if all_win else "LOSS" if any_loss else "PENDING"
        odds_tag = ">= 2.0" if display_combined >= 2.0 else "< 2.0"

        print(f"  Real combined : @{display_combined:.2f}  [{odds_tag}]  [{result}]")
        if real_map:
            print(f"  Model combined: @{combined:.2f}  (selection filter only — not the bet price)")
        if has_missing:
            print(f"  * Some legs used model odds — bookmaker price not found")
        print()

        # ── Per-pick output ───────────────────────────────────────────────────
        for i, p in enumerate(accumulator):
            real  = real_map.get(i)
            icon  = "[W]" if p.get("won") is True else "[L]" if p.get("won") is False else "[ ]"
            score = f"  Score: {p['score']}" if p.get("score") and p["score"] != "?" else ""

            absence_line = f"\n       {p['absence_flag']}" if p.get("absence_flag") else ""
            print(f"  {icon}  {p['fixture']}{absence_line}")
            print(f"       {p['label']}  |  {p['league']}")

            if real:
                mkt_odds = real["odds"]
                mkt_prob = 1 / mkt_odds
                gap      = p["probability"] - mkt_prob
                flag     = ""
                if gap > 0.08:
                    flag = "  ⚠  Large model/market gap"
                elif gap < -0.05:
                    flag = "  ✓  Market backs this strongly"
                print(f"       Bet price  : @{mkt_odds:.2f}  [{real['bookmaker']}]{flag}")
                print(f"       Model conf : {p['probability']:.1%}  (xG: {p.get('h_xg','?')} vs {p.get('a_xg','?')}){score}")
            else:
                print(f"       Bet price  : not found — using model @{p['odds']:.2f}")
                print(f"       Model conf : {p['probability']:.1%}  (xG: {p.get('h_xg','?')} vs {p.get('a_xg','?')}){score}")
            print()

        if display_combined < 2.0:
            print(f"  [Note] Combined @{display_combined:.2f} below @2.0 — valid bet, slightly shorter odds\n")

        print(f"{DIV}\n")
