"""
Management command: backfill_understat
=======================================
Fetches Understat match-level data for every completed fixture in the Big 5 leagues:
  - Big chances created / scored / missed (shots with xG >= 0.20)
  - Goals by situation: open play, set piece, direct free kick, penalty
  - Shots by situation and shot type (head / right foot / left foot)
  - xG by situation
  - Per-player: xG, xA, shots, key passes, goals, assists, shot breakdown

Uses Understat's free API — no API-Football quota consumed.

Usage
-----
  python manage.py backfill_understat                  # all Big 5 leagues
  python manage.py backfill_understat --league PL      # one league
  python manage.py backfill_understat --resume         # skip already-fetched fixtures
"""

import time
import asyncio
from difflib import SequenceMatcher

import aiohttp
import understat as _ulib
import requests

from django.core.management.base import BaseCommand

from bets.models import HistoricalFixture, MatchUnderstatStats, PlayerUnderstatStats

# ── Config ────────────────────────────────────────────────────────────────────

SEASON = 2025   # 2025/26

LEAGUE_MAP = {
    "PL":  "EPL",
    "PD":  "La_liga",
    "BL1": "Bundesliga",
    "SA":  "Serie_A",
    "FL1": "Ligue_1",
}

UNDERSTAT_BASE = "https://understat.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}

BIG_CHANCE_XG = 0.20   # xG threshold for "big chance"
DELAY = 0.4            # seconds between requests


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _norm(name: str) -> str:
    n = name.lower()
    for w in ("fc", "af", "cf", "sc", "ac", " united", " city", " hotspur",
              " wanderers", " athletic", " albion", " rovers", " town",
              " lisboa", " cp", " sporting", "1.", "sv", "vfb", "vfl"):
        n = n.replace(w, "")
    return n.strip()


def _flt(val) -> float | None:
    try:
        return round(float(val), 4) if val is not None else None
    except (ValueError, TypeError):
        return None


def _int(val) -> int | None:
    try:
        return int(val) if val is not None else None
    except (ValueError, TypeError):
        return None


# ── Understat fixture index ───────────────────────────────────────────────────

async def _fetch_league_fixtures(slug: str, season: str) -> list[dict]:
    """Return all fixtures for a league/season from Understat including match IDs."""
    async with aiohttp.ClientSession() as session:
        u = _ulib.Understat(session)
        try:
            fixtures = await u.get_league_fixtures(slug, season)
            results  = await u.get_league_results(slug, season)
            return list(fixtures) + list(results)
        except Exception as exc:
            print(f"  [Understat] Failed to fetch fixtures for {slug}/{season}: {exc}")
            return []


def _build_fixture_index(understat_fixtures: list[dict]) -> dict:
    """
    Returns {(date, norm_home, norm_away): understat_id} for all fetched fixtures.
    """
    index = {}
    for fx in understat_fixtures:
        date = (fx.get("datetime") or fx.get("date") or "")[:10]
        home = _norm(fx.get("h", {}).get("title", "") or fx.get("h_title", ""))
        away = _norm(fx.get("a", {}).get("title", "") or fx.get("a_title", ""))
        mid  = str(fx.get("id", ""))
        if date and home and away and mid:
            index[(date, home, away)] = mid
    return index


def _find_understat_id(fixture: HistoricalFixture, index: dict) -> str | None:
    """
    Match a HistoricalFixture to an Understat match ID using date + fuzzy name.
    """
    date     = str(fixture.match_date)
    h_norm   = _norm(fixture.home_team)
    a_norm   = _norm(fixture.away_team)

    # Exact key match first
    key = (date, h_norm, a_norm)
    if key in index:
        return index[key]

    # Fuzzy match by date
    best_id    = None
    best_score = 0.0
    for (d, h, a), mid in index.items():
        if d != date:
            continue
        score = (_sim(h_norm, h) + _sim(a_norm, a)) / 2
        if score > best_score:
            best_score = score
            best_id    = mid

    if best_score >= 0.55:
        return best_id
    return None


# ── Match data fetching ───────────────────────────────────────────────────────

def _fetch_match_data(match_id: str) -> dict | None:
    """GET /main/getMatchData/{id} — returns rosters + shots."""
    try:
        r = requests.get(
            f"{UNDERSTAT_BASE}/main/getMatchData/{match_id}",
            headers={**HEADERS, "Referer": f"{UNDERSTAT_BASE}/match/{match_id}"},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        print(f"  [Understat] getMatchData failed {match_id}: {exc}")
        return None


# ── Data parsers ──────────────────────────────────────────────────────────────

def _parse_match_stats(shots_h: list, shots_a: list, home_team_id: int, away_team_id: int) -> dict:
    """
    Derive team-level stats from shots arrays.
    Returns dict ready for MatchUnderstatStats.
    """

    def _count(shots, field, value):
        return sum(1 for s in shots if s.get(field) == value)

    def _count_goals(shots, situation):
        return sum(1 for s in shots if s.get("situation") == situation and s.get("result") == "Goal")

    def _sum_xg(shots, situation=None):
        return round(sum(
            float(s.get("xG") or 0) for s in shots
            if situation is None or s.get("situation") == situation
        ), 4)

    def _big_chances(shots):
        bc = [s for s in shots if float(s.get("xG") or 0) >= BIG_CHANCE_XG]
        scored = sum(1 for s in bc if s.get("result") == "Goal")
        return len(bc), scored, len(bc) - scored

    bc_h, bcs_h, bcm_h = _big_chances(shots_h)
    bc_a, bcs_a, bcm_a = _big_chances(shots_a)

    return dict(
        big_chances_home=bc_h,
        big_chances_away=bc_a,
        big_chances_scored_home=bcs_h,
        big_chances_scored_away=bcs_a,
        big_chances_missed_home=bcm_h,
        big_chances_missed_away=bcm_a,

        goals_open_play_home=_count_goals(shots_h, "OpenPlay"),
        goals_open_play_away=_count_goals(shots_a, "OpenPlay"),
        goals_set_piece_home=_count_goals(shots_h, "FromCorner") + _count_goals(shots_h, "SetPiece"),
        goals_set_piece_away=_count_goals(shots_a, "FromCorner") + _count_goals(shots_a, "SetPiece"),
        goals_direct_fk_home=_count_goals(shots_h, "DirectFreekick"),
        goals_direct_fk_away=_count_goals(shots_a, "DirectFreekick"),
        goals_penalty_home=_count_goals(shots_h, "Penalty"),
        goals_penalty_away=_count_goals(shots_a, "Penalty"),

        shots_open_play_home=_count(shots_h, "situation", "OpenPlay"),
        shots_open_play_away=_count(shots_a, "situation", "OpenPlay"),
        shots_set_piece_home=(_count(shots_h, "situation", "FromCorner") +
                              _count(shots_h, "situation", "SetPiece")),
        shots_set_piece_away=(_count(shots_a, "situation", "FromCorner") +
                              _count(shots_a, "situation", "SetPiece")),
        shots_penalty_home=_count(shots_h, "situation", "Penalty"),
        shots_penalty_away=_count(shots_a, "situation", "Penalty"),

        xg_open_play_home=_sum_xg(shots_h, "OpenPlay"),
        xg_open_play_away=_sum_xg(shots_a, "OpenPlay"),
        xg_set_piece_home=_sum_xg(shots_h, "FromCorner") + _sum_xg(shots_h, "SetPiece"),
        xg_set_piece_away=_sum_xg(shots_a, "FromCorner") + _sum_xg(shots_a, "SetPiece"),
        xg_penalty_home=_sum_xg(shots_h, "Penalty"),
        xg_penalty_away=_sum_xg(shots_a, "Penalty"),

        shots_head_home=_count(shots_h, "shotType", "Head"),
        shots_head_away=_count(shots_a, "shotType", "Head"),
        shots_right_foot_home=_count(shots_h, "shotType", "RightFoot"),
        shots_right_foot_away=_count(shots_a, "shotType", "RightFoot"),
        shots_left_foot_home=_count(shots_h, "shotType", "LeftFoot"),
        shots_left_foot_away=_count(shots_a, "shotType", "LeftFoot"),
    )


def _parse_player_stats(match_data: dict, fixture: HistoricalFixture) -> list[dict]:
    """
    Build list of player-level stat dicts from rosters + shots data.
    """
    rosters = match_data.get("rosters", {})
    shots   = match_data.get("shots", {})
    shots_h = shots.get("h", [])
    shots_a = shots.get("a", [])

    records = []

    for side, is_home in (("h", True), ("a", False)):
        team_shots = shots_h if side == "h" else shots_a

        for _, entry in rosters.get(side, {}).items():
            pid   = str(entry.get("player_id", ""))
            pname = entry.get("player", "") or ""
            if not pid:
                continue

            # Shots by this player
            player_shots = [s for s in team_shots if str(s.get("player_id", "")) == pid]

            def _sc(field, value, shot_list=player_shots):
                return sum(1 for s in shot_list if s.get(field) == value)

            bc        = [s for s in player_shots if float(s.get("xG") or 0) >= BIG_CHANCE_XG]
            bc_scored = sum(1 for s in bc if s.get("result") == "Goal")
            bc_missed = len(bc) - bc_scored

            records.append(dict(
                fixture=fixture,
                understat_player_id=pid,
                player_name=pname,
                team_name=(fixture.home_team if is_home else fixture.away_team),
                is_home=is_home,

                xg=_flt(entry.get("xG")),
                xa=_flt(entry.get("xA")),
                shots=_int(entry.get("shots")),
                key_passes=_int(entry.get("key_passes")),
                goals=_int(entry.get("goals")) or 0,
                assists=_int(entry.get("assists")) or 0,
                own_goals=_int(entry.get("own_goals")) or 0,
                minutes=_int(entry.get("time")),
                yellow=_int(entry.get("yellow")) or 0,
                red=_int(entry.get("red")) or 0,

                shots_head=_sc("shotType", "Head"),
                shots_right_foot=_sc("shotType", "RightFoot"),
                shots_left_foot=_sc("shotType", "LeftFoot"),

                shots_open_play=_sc("situation", "OpenPlay"),
                shots_from_corner=_sc("situation", "FromCorner"),
                shots_direct_fk=_sc("situation", "DirectFreekick"),
                shots_set_piece=_sc("situation", "SetPiece"),
                shots_penalty=_sc("situation", "Penalty"),

                big_chances_scored=bc_scored,
                big_chances_missed=bc_missed,
            ))

    return records


# ── Main command ──────────────────────────────────────────────────────────────

class Command(BaseCommand):
    help = "Backfill Understat match + player stats (Big 5 leagues, no API quota used)."

    def add_arguments(self, parser):
        parser.add_argument("--league", type=str, default=None, help="Restrict to one league code e.g. PL")
        parser.add_argument("--resume", action="store_true",   help="Skip fixtures already fetched")

    def handle(self, *args, **options):
        league_filter = options["league"]
        resume        = options["resume"]

        target = {k: v for k, v in LEAGUE_MAP.items()
                  if league_filter is None or k == league_filter}

        if not target:
            self.stderr.write(f"Unknown league: {league_filter}. Choose from: {', '.join(LEAGUE_MAP)}")
            return

        season_str = str(SEASON)

        self.stdout.write(f"\n{'='*65}")
        self.stdout.write(f"  UNDERSTAT BACKFILL  |  Season {SEASON}/{SEASON+1}")
        self.stdout.write(f"  Leagues: {', '.join(target)}  |  Resume: {resume}")
        self.stdout.write(f"  No API-Football quota used (Understat is free)")
        self.stdout.write(f"{'='*65}\n")

        total_match = total_player = 0

        for code, slug in target.items():
            self.stdout.write(f"\n[{code}] Fetching Understat fixture index for {slug}/{season_str}...")

            raw_fixtures = asyncio.run(_fetch_league_fixtures(slug, season_str))
            if not raw_fixtures:
                self.stdout.write(f"  No fixtures returned — skipping {code}")
                continue

            index = _build_fixture_index(raw_fixtures)
            self.stdout.write(f"  {len(index)} fixtures in Understat index")

            qs = HistoricalFixture.objects.filter(league_code=code, season=SEASON)
            if resume:
                already_done = set(
                    MatchUnderstatStats.objects.filter(fixture__league_code=code)
                    .values_list("fixture_id", flat=True)
                )
                qs = qs.exclude(id__in=already_done)

            total_fx = qs.count()
            self.stdout.write(f"  {total_fx} fixtures to process")

            matched = 0
            skipped = 0

            for idx, fixture in enumerate(qs.order_by("match_date"), 1):
                prefix = f"  [{idx:>4}/{total_fx}] {fixture.match_date} {fixture.home_team[:16]:16s} v {fixture.away_team[:16]:16s}"

                mid = _find_understat_id(fixture, index)
                if not mid:
                    self.stdout.write(f"{prefix}  [no match ID]")
                    skipped += 1
                    continue

                data = _fetch_match_data(mid)
                time.sleep(DELAY)
                if not data:
                    self.stdout.write(f"{prefix}  [no data]")
                    skipped += 1
                    continue

                shots   = data.get("shots", {})
                shots_h = shots.get("h", [])
                shots_a = shots.get("a", [])

                if not shots_h and not shots_a:
                    self.stdout.write(f"{prefix}  [empty shots]")
                    skipped += 1
                    continue

                # ── Match-level stats ─────────────────────────────────────────
                match_stats = _parse_match_stats(shots_h, shots_a, fixture.home_team_id, fixture.away_team_id)
                MatchUnderstatStats.objects.update_or_create(
                    fixture=fixture,
                    defaults={**match_stats, "understat_match_id": mid},
                )
                total_match += 1

                # ── Player-level stats ────────────────────────────────────────
                player_records = _parse_player_stats(data, fixture)
                for rec in player_records:
                    PlayerUnderstatStats.objects.update_or_create(
                        fixture=rec.pop("fixture"),
                        understat_player_id=rec["understat_player_id"],
                        defaults=rec,
                    )
                total_player += len(player_records)

                matched += 1
                self.stdout.write(
                    f"{prefix}  OK  (BC h:{match_stats['big_chances_home']} a:{match_stats['big_chances_away']}  "
                    f"OPG h:{match_stats['goals_open_play_home']} a:{match_stats['goals_open_play_away']})"
                )

            self.stdout.write(f"\n  [{code}] Done — {matched} fetched, {skipped} skipped")

        self.stdout.write(f"\n{'='*65}")
        self.stdout.write(f"  UNDERSTAT BACKFILL COMPLETE")
        self.stdout.write(f"  Match records : {total_match}")
        self.stdout.write(f"  Player records: {total_player}")
        self.stdout.write(f"{'='*65}\n")
