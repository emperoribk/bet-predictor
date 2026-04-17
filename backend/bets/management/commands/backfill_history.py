"""
Management command: backfill_history
=====================================
Fetches all completed fixtures + statistics + lineups + player ratings
for every covered league since the start of the 2025/26 season (Aug 2025).

Usage
-----
  python manage.py backfill_history                     # full backfill
  python manage.py backfill_history --league PL         # one league only
  python manage.py backfill_history --skip-players      # skip player ratings (saves ~1/3 quota)
  python manage.py backfill_history --fixtures-only     # only sync fixture list, no deep data
  python manage.py backfill_history --resume            # skip fixtures already fully fetched

Quota estimate (full run, all leagues, ~3 500 fixtures):
  Fixture lists  : ~20 calls
  Stats          : 1 call per fixture
  Lineups        : 1 call per fixture
  Players        : 1 call per fixture
  Total          : ~10 500 calls  →  ~1.5 days of the 7 500/day Pro quota
"""

import time
import traceback
from datetime import date

from django.core.management.base import BaseCommand
from django.conf import settings
import requests

from bets.models import HistoricalFixture, MatchStats, MatchLineup, PlayerMatchRating

# ── Config ────────────────────────────────────────────────────────────────────

SEASON = 2025   # default season (overridden by --season flag)

LEAGUES = {
    "PL":   39,    # Premier League
    "PD":   140,   # La Liga
    "BL1":  78,    # Bundesliga
    "SA":   135,   # Serie A
    "FL1":  61,    # Ligue 1
    "DED":  88,    # Eredivisie
    "PPL":  94,    # Primeira Liga
    "ELC":  40,    # Championship
    "BJL":  144,   # Jupiler Pro League
    "TSL":  203,   # Süper Lig
    "BL2":  79,    # 2. Bundesliga
    "SSL":  207,   # Swiss Super League
    "SPL":  179,   # Scottish Premiership
    "CL":   2,     # Champions League
    "EL":   3,     # Europa League
    "UECL": 848,   # Conference League
}

FINISHED_STATUSES = {"FT", "AET", "PEN", "AWD", "WO"}

BASE_URL = "https://v3.football.api-sports.io"
DELAY     = 0.35   # seconds between API calls (stays well within rate limit)
MAX_RETRY = 3


# ── Helpers ───────────────────────────────────────────────────────────────────

def _api(endpoint: str, params: dict, api_key: str) -> dict | None:
    """Single API-Football call with retry on transient errors."""
    url = f"{BASE_URL}/{endpoint}"
    headers = {"x-apisports-key": api_key}
    for attempt in range(1, MAX_RETRY + 1):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=15)
            data = r.json()
            errors = data.get("errors", {})
            if errors:
                err_str = str(errors)
                if "rateLimit" in err_str or "requests" in err_str or "limit" in err_str.lower():
                    print(f"\n[QUOTA] Daily limit hit — stopping. Re-run tomorrow to continue.")
                    return "QUOTA_EXHAUSTED"
                print(f"  [API error] /{endpoint}: {errors}")
                return None
            return data
        except requests.RequestException as e:
            if attempt < MAX_RETRY:
                print(f"  [retry {attempt}] {e}")
                time.sleep(2 * attempt)
            else:
                print(f"  [failed] /{endpoint}: {e}")
                return None
    return None


def _int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _float(val):
    try:
        return float(str(val).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _stat(stats_list: list, team_idx: int, stat_name: str):
    """Pull one stat value from the API fixture/statistics response."""
    try:
        team_stats = stats_list[team_idx]["statistics"]
        for item in team_stats:
            if item["type"] == stat_name:
                return item["value"]
    except (IndexError, KeyError, TypeError):
        pass
    return None


# ── Main command ──────────────────────────────────────────────────────────────

class Command(BaseCommand):
    help = "Backfill historical fixture data (stats, lineups, player ratings) for all covered leagues."

    def add_arguments(self, parser):
        parser.add_argument("--league",        type=str,  default=None,  help="Restrict to one league code e.g. PL")
        parser.add_argument("--season",        type=int,  default=SEASON, help="Season start year e.g. 2024 for 2024/25")
        parser.add_argument("--skip-players",  action="store_true",      help="Skip player rating calls")
        parser.add_argument("--fixtures-only", action="store_true",      help="Only sync fixture list")
        parser.add_argument("--resume",        action="store_true",      help="Skip fully-fetched fixtures")
        parser.add_argument("--reverse",       action="store_true",      help="Process deep data newest-first (most recent months first)")

    def handle(self, *args, **options):
        api_key = getattr(settings, "API_FOOTBALL_KEY", None)
        if not api_key:
            import os
            api_key = os.environ.get("API_FOOTBALL_KEY")
        if not api_key:
            self.stderr.write("ERROR: API_FOOTBALL_KEY not found in settings or environment.")
            return

        season        = options["season"]
        league_filter = options["league"]
        skip_players  = options["skip_players"]
        fixtures_only = options["fixtures_only"]
        resume        = options["resume"]
        reverse       = options["reverse"]

        target_leagues = {k: v for k, v in LEAGUES.items()
                          if league_filter is None or k == league_filter}

        if not target_leagues:
            self.stderr.write(f"Unknown league code: {league_filter}. Choose from: {', '.join(LEAGUES)}")
            return

        self.stdout.write(f"\n{'='*65}")
        self.stdout.write(f"  SPORTY HISTORY BACKFILL  |  Season {season}/{season+1}")
        self.stdout.write(f"  Leagues: {', '.join(target_leagues)}")
        self.stdout.write(f"  Skip players: {skip_players}  |  Fixtures only: {fixtures_only}  |  Resume: {resume}")
        self.stdout.write(f"{'='*65}\n")

        total_new = total_stats = total_lineups = total_players = 0

        for code, league_id in target_leagues.items():

            # ── Step 1: Fetch fixture list (skip if already fully done on resume) ──
            already_in_db = HistoricalFixture.objects.filter(league_code=code, season=season).count()
            pending_deep  = HistoricalFixture.objects.filter(
                league_code=code, season=season,
                stats_fetched=False
            ).count() if resume else 1  # if not resume, always re-fetch list

            if resume and already_in_db > 0 and pending_deep == 0 and not fixtures_only:
                self.stdout.write(f"\n[{code}] Already complete ({already_in_db} fixtures) — skipping fixture list call")
                data = None
            else:
                self.stdout.write(f"\n[{code}] Fetching fixture list...")
                data = _api("fixtures", {"league": league_id, "season": season}, api_key)
                time.sleep(DELAY)

            if data == "QUOTA_EXHAUSTED":
                return
            if data is not None and not data.get("response"):
                self.stdout.write(f"  No fixtures returned for {code}")
                continue

            if data is None:
                # Skipped fixture list fetch — use what's already in DB
                new_count = 0
            else:
                fixtures_raw = data["response"]
                completed    = [f for f in fixtures_raw
                                if f["fixture"]["status"]["short"] in FINISHED_STATUSES]

                self.stdout.write(f"  {len(completed)} completed fixtures found (of {len(fixtures_raw)} total)")

                league_name = fixtures_raw[0]["league"]["name"] if fixtures_raw else code

                new_count = 0
                for fx in completed:
                    fid   = fx["fixture"]["id"]
                    fd    = fx["fixture"]["date"][:10]  # YYYY-MM-DD
                    score = fx["goals"]
                    teams = fx["teams"]
                    lge   = fx["league"]

                    h_score  = _int(score.get("home"))
                    a_score  = _int(score.get("away"))
                    ht_score = fx.get("score", {}).get("halftime", {})

                    if teams["home"]["winner"] is True:
                        winner = "HOME"
                    elif teams["away"]["winner"] is True:
                        winner = "AWAY"
                    else:
                        winner = "DRAW"

                    obj, created = HistoricalFixture.objects.update_or_create(
                        fixture_id=fid,
                        defaults=dict(
                            league_code=code,
                            league_id=league_id,
                            league_name=league_name,
                            season=season,
                            match_date=fd,
                            kickoff=fx["fixture"]["date"],
                            round=lge.get("round", ""),
                            venue=fx["fixture"].get("venue", {}).get("name", "") or "",
                            home_team=teams["home"]["name"],
                            away_team=teams["away"]["name"],
                            home_team_id=teams["home"]["id"],
                            away_team_id=teams["away"]["id"],
                            home_score=h_score,
                            away_score=a_score,
                            home_ht=_int(ht_score.get("home")) if ht_score else None,
                            away_ht=_int(ht_score.get("away")) if ht_score else None,
                            status=fx["fixture"]["status"]["short"],
                            winner=winner,
                        )
                    )
                    if created:
                        new_count += 1

            self.stdout.write(f"  Saved {new_count} new fixtures to DB")
            total_new += new_count

            if fixtures_only:
                continue

            # ── Step 2: Fetch deep data for each fixture ─────────────────────
            qs = HistoricalFixture.objects.filter(league_code=code, season=season)
            if resume:
                need_stats   = qs.filter(stats_fetched=False)
                need_lineups = qs.filter(lineup_fetched=False)
                need_players = qs.filter(players_fetched=False) if not skip_players else qs.none()
            else:
                need_stats   = qs
                need_lineups = qs
                need_players = qs if not skip_players else qs.none()

            total_fx = qs.count()
            self.stdout.write(f"  Deep data: {need_stats.count()} stats  |  {need_lineups.count()} lineups  |  {need_players.count() if not skip_players else 'skipped'} players")

            order = '-match_date' if reverse else 'match_date'
            for idx, fixture in enumerate(qs.order_by(order), 1):
                _h = fixture.home_team[:18].encode('ascii', 'replace').decode()
                _a = fixture.away_team[:18].encode('ascii', 'replace').decode()
                prefix = f"  [{idx:>4}/{total_fx}] {fixture.match_date} {_h} v {_a}"

                # ── Stats ────────────────────────────────────────────────────
                if not resume or not fixture.stats_fetched:
                    sdata = _api("fixtures/statistics", {"fixture": fixture.fixture_id}, api_key)
                    time.sleep(DELAY)
                    if sdata == "QUOTA_EXHAUSTED":
                        return
                    if sdata and sdata.get("response"):
                        sl = sdata["response"]
                        # identify home/away by team_id
                        home_idx = 0 if sl[0]["team"]["id"] == fixture.home_team_id else 1
                        away_idx = 1 - home_idx

                        def gs(name, idx=home_idx):
                            return _stat(sl, idx, name)

                        xg_h_raw = gs("expected_goals", home_idx)
                        xg_a_raw = gs("expected_goals", away_idx)

                        MatchStats.objects.update_or_create(
                            fixture=fixture,
                            defaults=dict(
                                possession_home=_float(gs("Ball Possession", home_idx)),
                                possession_away=_float(gs("Ball Possession", away_idx)),
                                shots_total_home=_int(gs("Total Shots", home_idx)),
                                shots_total_away=_int(gs("Total Shots", away_idx)),
                                shots_on_home=_int(gs("Shots on Goal", home_idx)),
                                shots_on_away=_int(gs("Shots on Goal", away_idx)),
                                shots_off_home=_int(gs("Shots off Goal", home_idx)),
                                shots_off_away=_int(gs("Shots off Goal", away_idx)),
                                blocked_home=_int(gs("Blocked Shots", home_idx)),
                                blocked_away=_int(gs("Blocked Shots", away_idx)),
                                xg_home=_float(xg_h_raw),
                                xg_away=_float(xg_a_raw),
                                passes_total_home=_int(gs("Total passes", home_idx)),
                                passes_total_away=_int(gs("Total passes", away_idx)),
                                pass_accuracy_home=_float(gs("Passes accurate", home_idx)),
                                pass_accuracy_away=_float(gs("Passes accurate", away_idx)),
                                key_passes_home=_int(gs("Passes %", home_idx)),   # reused field
                                key_passes_away=_int(gs("Passes %", away_idx)),
                                corners_home=_int(gs("Corner Kicks", home_idx)),
                                corners_away=_int(gs("Corner Kicks", away_idx)),
                                fouls_home=_int(gs("Fouls", home_idx)),
                                fouls_away=_int(gs("Fouls", away_idx)),
                                offsides_home=_int(gs("Offsides", home_idx)),
                                offsides_away=_int(gs("Offsides", away_idx)),
                                yellow_home=_int(gs("Yellow Cards", home_idx)),
                                yellow_away=_int(gs("Yellow Cards", away_idx)),
                                red_home=_int(gs("Red Cards", home_idx)),
                                red_away=_int(gs("Red Cards", away_idx)),
                                saves_home=_int(gs("Goalkeeper Saves", home_idx)),
                                saves_away=_int(gs("Goalkeeper Saves", away_idx)),
                                raw={"home": sl[home_idx], "away": sl[away_idx]},
                            )
                        )
                        fixture.stats_fetched = True
                        fixture.save(update_fields=["stats_fetched"])
                        total_stats += 1
                        self.stdout.write(f"{prefix}  stats OK", ending="\r")

                # ── Lineups ──────────────────────────────────────────────────
                if not resume or not fixture.lineup_fetched:
                    ldata = _api("fixtures/lineups", {"fixture": fixture.fixture_id}, api_key)
                    time.sleep(DELAY)
                    if ldata == "QUOTA_EXHAUSTED":
                        return
                    if ldata and ldata.get("response"):
                        for team_block in ldata["response"]:
                            tid  = team_block["team"]["id"]
                            is_h = (tid == fixture.home_team_id)
                            starters = [
                                {"id": p["player"]["id"], "name": p["player"]["name"],
                                 "number": p["player"]["number"], "pos": p["player"]["pos"],
                                 "grid": p["player"]["grid"]}
                                for p in team_block.get("startXI", [])
                            ]
                            subs = [
                                {"id": p["player"]["id"], "name": p["player"]["name"],
                                 "number": p["player"]["number"], "pos": p["player"]["pos"]}
                                for p in team_block.get("substitutes", [])
                            ]
                            MatchLineup.objects.update_or_create(
                                fixture=fixture,
                                team_id=tid,
                                defaults=dict(
                                    team_name=team_block["team"]["name"],
                                    is_home=is_h,
                                    formation=team_block.get("formation") or "",
                                    coach_name=(team_block.get("coach") or {}).get("name") or "",
                                    starters=starters,
                                    substitutes=subs,
                                )
                            )
                        fixture.lineup_fetched = True
                        fixture.save(update_fields=["lineup_fetched"])
                        total_lineups += 1
                        self.stdout.write(f"{prefix}  lineups OK", ending="\r")

                # ── Player ratings ───────────────────────────────────────────
                if not skip_players and (not resume or not fixture.players_fetched):
                    pdata = _api("fixtures/players", {"fixture": fixture.fixture_id}, api_key)
                    time.sleep(DELAY)
                    if pdata == "QUOTA_EXHAUSTED":
                        return
                    if pdata and pdata.get("response"):
                        for team_block in pdata["response"]:
                            tid  = team_block["team"]["id"]
                            tname = team_block["team"]["name"]
                            is_h  = (tid == fixture.home_team_id)
                            for p in team_block.get("players", []):
                                pi   = p["player"]
                                st   = (p.get("statistics") or [{}])[0]
                                gm   = st.get("games", {})
                                sh   = st.get("shots", {}) or {}
                                ps   = st.get("passes", {}) or {}
                                dr   = st.get("dribbles", {}) or {}
                                du   = st.get("duels", {}) or {}
                                tk   = st.get("tackles", {}) or {}
                                fo   = st.get("fouls", {}) or {}
                                gl   = st.get("goals", {}) or {}
                                ca   = st.get("cards", {}) or {}

                                rating_raw = gm.get("rating")
                                try:
                                    rating = float(rating_raw) if rating_raw else None
                                except (ValueError, TypeError):
                                    rating = None

                                PlayerMatchRating.objects.update_or_create(
                                    fixture=fixture,
                                    player_id=pi["id"],
                                    defaults=dict(
                                        team_id=tid,
                                        team_name=tname,
                                        is_home=is_h,
                                        player_name=pi["name"],
                                        position=gm.get("position") or "",
                                        number=_int(pi.get("number")),
                                        rating=rating,
                                        minutes_played=_int(gm.get("minutes")),
                                        was_starter=bool(gm.get("captain") is not None
                                                         or _int(gm.get("minutes") or 0) > 45),
                                        goals=_int(gl.get("total")) or 0,
                                        assists=_int(gl.get("assists")) or 0,
                                        shots_total=_int(sh.get("total")),
                                        shots_on=_int(sh.get("on")),
                                        passes_total=_int(ps.get("total")),
                                        key_passes=_int(ps.get("key")),
                                        pass_accuracy=_float(ps.get("accuracy")),
                                        dribbles_attempts=_int(dr.get("attempts")),
                                        dribbles_success=_int(dr.get("success")),
                                        duels_total=_int(du.get("total")),
                                        duels_won=_int(du.get("won")),
                                        tackles=_int(tk.get("total")),
                                        interceptions=_int(tk.get("interceptions")),
                                        clearances=_int(tk.get("clearances")),
                                        yellow_card=bool(ca.get("yellow")),
                                        red_card=bool(ca.get("red")),
                                        raw=st,
                                    )
                                )
                        fixture.players_fetched = True
                        fixture.save(update_fields=["players_fetched"])
                        total_players += 1

                self.stdout.write(f"{prefix}  OK")

        # ── Summary ───────────────────────────────────────────────────────────
        self.stdout.write(f"\n{'='*65}")
        self.stdout.write(f"  BACKFILL COMPLETE")
        self.stdout.write(f"  New fixtures  : {total_new}")
        self.stdout.write(f"  Stats saved   : {total_stats}")
        self.stdout.write(f"  Lineups saved : {total_lineups}")
        self.stdout.write(f"  Players saved : {total_players}")
        self.stdout.write(f"{'='*65}\n")
