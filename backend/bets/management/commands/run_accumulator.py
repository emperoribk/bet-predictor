"""
Django management command: python manage.py run_accumulator [--date 2026-04-13]

Builds a daily accumulator using the same calibrated engine as scorecard.py:
  - Two-pass MIN_PROB: 0.72 base, 0.77 stricter floor if day hits 2.0+
  - DC markets only (DC_1X / DC_X2), min 10 games played per team
  - Over 1.5 booster (77%+) from a different fixture when day is sub-2.0
  - Derby filter: Istanbul derbies excluded
  - NEVER_BACK: Monaco
  - Target leagues: PL, PD, BL1, SA, FL1, DED, PPL, ELC, SPL, BJL, TSL, BL2
"""

import io, sys, math, requests
from datetime import date as _date_cls
from difflib import SequenceMatcher
from django.core.management.base import BaseCommand

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# ── Configuration (keep in sync with scorecard.py) ────────────────────────────
MIN_PROB       = 0.79   # base floor — only back picks we're genuinely confident in
MIN_PROB_HIGH  = 0.79   # stricter floor — applied when first pass hits 2.0+
MAX_PROB       = 0.82   # above this odds too short — 83%+ DC picks lose more than expected
BOOSTER_THRESH = 0.77   # Over 1.5 at 77%+ calibrated to 80%+ accuracy
TIER2_MIN_PROB = 0.79   # Tier 2: dominant team to score / Over 1.5 floor
TARGET_ODDS    = 2.0
MAX_PICKS      = 3
MIN_BACK_GAMES = 10     # teams with fewer games get excluded as DC picks

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
}

NEVER_BACK = {"Monaco"}  # wildly inconsistent — never back them as favourite

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

    DC_1X / DC_X2: derived from h2h (1X2) by normalising implied probabilities.
    OVER15: fetched directly from totals market.

    Returns dict with keys: bookmaker, odds, implied_prob
    or None if no match found.
    """
    sport_key = ODDS_API_SPORT.get(league_code)
    if not sport_key or not api_key:
        return None

    api_market = "h2h" if market in ("DC_1X", "DC_X2") else "totals"

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

            if market in ("DC_1X", "DC_X2"):
                # Build implied prob map from 1X2 prices
                prices = {o["name"]: o["price"] for o in mkt.get("outcomes", [])}
                home_name_api = best_game.get("home_team", "")
                away_name_api = best_game.get("away_team", "")
                p_h = 1 / prices.get(home_name_api, 999)
                p_d = 1 / prices.get("Draw", 999)
                p_a = 1 / prices.get(away_name_api, 999)
                total = p_h + p_d + p_a
                if total <= 0:
                    continue
                # Normalise to remove bookmaker margin
                p_h, p_d, p_a = p_h / total, p_d / total, p_a / total
                dc_prob = (p_h + p_d) if market == "DC_1X" else (p_d + p_a)
                dc_odds = round(1 / dc_prob, 2) if dc_prob > 0 else None
                if dc_odds and (best_odds is None or dc_odds > best_odds):
                    best_odds   = dc_odds
                    best_bookie = bookie["title"]

            elif market == "OVER15":
                # Free tier only has 2.5 — grab it as a reference
                for outcome in mkt.get("outcomes", []):
                    if outcome.get("name", "").lower() == "over" and outcome.get("point") == 2.5:
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


def _team_score_prob(xg):
    """P(team scores >= 1 goal) via Poisson."""
    return 1 - math.exp(-xg)


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
        if min_games < MIN_BACK_GAMES:
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
                    "h_xg": round(h_xg, 2), "a_xg": round(a_xg, 2),
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
                    "h_xg": round(h_xg, 2), "a_xg": round(a_xg, 2),
                }
                best_prob = p_ts

        # Over 1.5: replace team-score pick only if higher probability
        if p_o15 >= TIER2_MIN_PROB and p_o15 > best_prob:
            won = ((home_score + away_score) >= 2) if home_score is not None else None
            best_pick = {
                "fixture": fix_str, "label": "Over 1.5 Goals",
                "market": "OVER15", "probability": round(p_o15, 4),
                "odds": round(1 / p_o15, 2), "won": won,
                "score": score_str, "league": comp_code,
                "h_xg": round(h_xg, 2), "a_xg": round(a_xg, 2),
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


def _build_accumulator(fixtures_data, min_prob):
    """
    Build accumulator from pre-computed fixture data at a given min_prob floor.
    Returns (accumulator, combined_odds, booster_candidates).
    """
    candidates = []
    booster_candidates = []

    for item in fixtures_data:
        # Collect Over 1.5 boosters regardless of min_prob
        if item["booster"] and item["p_o15"] >= BOOSTER_THRESH and item["min_games"] >= MIN_BACK_GAMES:
            booster_candidates.append(item["booster"])

        # DC pick — only if within prob window
        if item["pick"] and min_prob <= item["prob"] <= MAX_PROB:
            candidates.append(item["pick"])

    if not candidates:
        return None, None, booster_candidates

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

    # If still sub-2.0 and have room, add the best Over 1.5 booster from a DIFFERENT fixture
    if combined < TARGET_ODDS and len(accumulator) < MAX_PICKS and booster_candidates:
        booster_candidates.sort(key=lambda x: -x["probability"])
        for b in booster_candidates:
            if b["fixture"] not in used_fixtures:
                accumulator.append(b)
                combined *= b["odds"]
                break

    return accumulator, combined, booster_candidates


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
            key_player_absence_factor,
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

        # Pre-compute stats + probs for every fixture once
        fixtures_data = []
        for fix in league_fixtures:
            home_name = fix["home_team"]
            away_name = fix["away_team"]
            comp_code = fix.get("competition_code", "PL")
            try:
                # Skip Istanbul derbies
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
                if min_games < 3:
                    continue

                h_xg, a_xg = _expected_xg_for_match(hs, aws)
                if h_xg <= 0 or a_xg <= 0:
                    continue

                # Key player absence — flag only, no xG adjustment
                _, h_flag = key_player_absence_factor(home_name, target_date)
                _, a_flag = key_player_absence_factor(away_name, target_date)

                p_home, p_draw, p_away = _match_probs(h_xg, a_xg)
                p_o15 = _over15_prob(h_xg, a_xg)

                fixture_str = f"{home_name} vs {away_name}"
                home_score  = fix.get("home_score")
                away_score  = fix.get("away_score")
                score_str   = f"{home_score}-{away_score}" if home_score is not None else "?"

                # Over 1.5 booster candidate
                booster = None
                if p_o15 >= BOOSTER_THRESH and min_games >= MIN_BACK_GAMES:
                    o15_won = None
                    if home_score is not None:
                        o15_won = (home_score + away_score) >= 2
                    booster = {
                        "fixture":     fixture_str,
                        "label":       "Over 1.5 Goals",
                        "market":      "OVER15",
                        "probability": round(p_o15, 4),
                        "odds":        round(1 / p_o15, 2),
                        "won":         o15_won,
                        "score":       score_str,
                        "league":      comp_code,
                        "h_xg":        round(h_xg, 2),
                        "a_xg":        round(a_xg, 2),
                    }

                # Raw fields used by Tier 2 (team-score / Over 1.5 fallback)
                raw = {
                    "h_xg": round(h_xg, 2), "a_xg": round(a_xg, 2),
                    "home_name": home_name, "away_name": away_name,
                    "home_score": home_score, "away_score": away_score,
                    "fixture_str": fixture_str, "comp_code": comp_code,
                    "score_str": score_str,
                }

                # DC pick — only back teams with MIN_BACK_GAMES+ games
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
                if home_score is not None:
                    if mkt == "DC_1X":  won = home_score >= away_score
                    elif mkt == "DC_X2": won = away_score >= home_score

                absence_flag = h_flag if mkt == "DC_1X" else a_flag

                pick = {
                    "fixture":      fixture_str,
                    "label":        label,
                    "market":       mkt,
                    "probability":  round(prob, 4),
                    "odds":         round(1 / prob, 2),
                    "won":          won,
                    "score":        score_str,
                    "league":       comp_code,
                    "h_xg":         round(h_xg, 2),
                    "a_xg":         round(a_xg, 2),
                    "absence_flag": absence_flag,
                }
                fixtures_data.append({
                    "p_o15":     p_o15,
                    "min_games": min_games,
                    "booster":   booster,
                    "prob":      round(prob, 4),
                    "pick":      pick,
                    **raw,
                })

            except Exception as e:
                if debug:
                    import traceback
                    print(f"[DEBUG] Exception for {fix.get('home_team')} vs {fix.get('away_team')}: {e}")
                    traceback.print_exc()
                continue

        if debug:
            print(f"\n[DEBUG] fixtures_data entries built: {len(fixtures_data)}")
            for fd in fixtures_data:
                p = fd.get("pick")
                if p:
                    print(f"  prob={fd['prob']:.4f}  min_games={fd['min_games']}  {p['fixture']}  {p['market']}")
                else:
                    print(f"  prob=N/A  min_games={fd['min_games']}  booster_only={fd.get('booster') is not None}")

        if not fixtures_data:
            return None

        # ── Tier 1: DC accumulator ────────────────────────────────────────────
        accumulator, combined, _ = _build_accumulator(fixtures_data, MIN_PROB)
        if accumulator and combined >= TARGET_ODDS:
            # Second pass: enforce stricter floor on over-2.0 days
            acc2, comb2, _ = _build_accumulator(fixtures_data, MIN_PROB_HIGH)
            if acc2:
                accumulator, combined = acc2, comb2
            else:
                accumulator = None  # weak picks on high-odds day — fall to Tier 2

        tier = 1
        if not accumulator:
            # ── Tier 2: dominant team to score + Over 1.5 ────────────────────
            accumulator, combined = _build_tier2_accumulator(fixtures_data)
            tier = 2

        if not accumulator:
            return None

        if verbose:
            from django.conf import settings
            odds_api_key = getattr(settings, "ODDS_API_KEY", "")
            self._print(target_date, accumulator, combined, tier, odds_api_key=odds_api_key)

        return accumulator, combined, tier

    # ── Print ─────────────────────────────────────────────────────────────────
    def _print(self, target_date, accumulator, combined, tier=1, odds_api_key=""):
        DIV = "=" * 70
        tier_label = "DC Double Chance" if tier == 1 else "Goals / Team Score"
        print(f"\n{DIV}")
        print(f"  DAILY ACCUMULATOR  |  {target_date}  |  Tier {tier}: {tier_label}")
        print(f"{DIV}\n")

        if not accumulator:
            print("  No qualifying picks today.\n")
            return

        all_win  = all(p.get("won") is True  for p in accumulator)
        any_loss = any(p.get("won") is False for p in accumulator)
        result   = "WIN" if all_win else "LOSS" if any_loss else "PENDING"
        odds_tag = ">= 2.0" if combined >= TARGET_ODDS else "< 2.0"

        print(f"  Combined odds : @{combined:.2f}  [{odds_tag}]  [{result}]\n")

        for p in accumulator:
            icon  = "[W]" if p.get("won") is True else "[L]" if p.get("won") is False else "[ ]"
            score = f"  Score: {p['score']}" if p.get("score") and p["score"] != "?" else ""

            # Parse home/away from fixture string "Home vs Away"
            parts = p["fixture"].split(" vs ", 1)
            home_name = parts[0].strip() if len(parts) == 2 else p["fixture"]
            away_name = parts[1].strip() if len(parts) == 2 else ""

            # Fetch real bookmaker odds
            real = None
            if odds_api_key:
                real = _fetch_bookmaker_odds(
                    home_name, away_name, p["market"], p["league"], odds_api_key
                )

            absence_line = f"\n       {p['absence_flag']}" if p.get("absence_flag") else ""
            print(f"  {icon}  {p['fixture']}{absence_line}")
            print(f"       {p['label']}  |  {p['league']}")
            print(f"       Our model  : {p['probability']:.1%}  @{p['odds']:.2f}"
                  f"  xG: {p.get('h_xg','?')} vs {p.get('a_xg','?')}{score}")

            if p["market"] == "OVER15":
                # Over 1.5: our Poisson odds are the primary reference
                # Bookmaker returns Over 2.5 on free tier — show as context only
                if real:
                    print(f"       Bookmaker  : Over 2.5 @{real['odds']:.2f}  [{real['bookmaker']}]"
                          f"  (Over 1.5 needs premium tier — our xG calc is reliable)")
                else:
                    print(f"       Bookmaker  : Over 1.5 not on free tier — trust our xG model @{p['odds']:.2f}")
            else:
                if real:
                    our_prob = p["probability"]
                    mkt_odds = real["odds"]
                    mkt_prob = 1 / mkt_odds
                    gap      = our_prob - mkt_prob  # positive = we're more confident than market
                    flag     = ""
                    if gap > 0.08:
                        flag = "  ⚠  Market disagrees — be cautious"
                    elif gap < -0.05:
                        flag = "  ✓  Market agrees / backs this stronger"
                    print(f"       Bookmaker  : @{mkt_odds:.2f}  (implied {mkt_prob:.1%})  [{real['bookmaker']}]{flag}")
                else:
                    print(f"       Bookmaker  : not found for this fixture")
            print()

        if combined < TARGET_ODDS:
            print(f"  [Note] {len(accumulator)} pick(s) found — "
                  f"combined @{combined:.2f} below target {TARGET_ODDS:.1f}\n")

        print(f"{DIV}\n")
