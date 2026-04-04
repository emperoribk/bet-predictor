"""
Understat scraper — xG, PPDA (pressing), deep completions, shot quality data.

How it works:
  1. For a given league/season, fetch one player's match list from
     main/getPlayerMatches/{player_id}  → gives date + team names + Understat match ID.
  2. Find the matching match ID by date + fuzzy team name matching.
  3. Fetch https://understat.com/match/{id} to read the embedded match_info JSON.

Supported leagues: EPL, La Liga, Bundesliga, Serie A, Ligue 1.
"""

import re
import json
import time
import requests
from difflib import SequenceMatcher

BASE = "https://understat.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

# football-data.org competition code → Understat league slug + season offset
# season_offset: Understat uses END year for some leagues (2024-25 EPL → season=2025)
LEAGUE_CONFIG = {
    "PL":  {"slug": "EPL",        "end_year": False},
    "PD":  {"slug": "La_liga",    "end_year": False},
    "BL1": {"slug": "Bundesliga", "end_year": False},
    "SA":  {"slug": "Serie_A",    "end_year": False},
    "FL1": {"slug": "Ligue_1",    "end_year": False},
}

# One well-known anchor player per league (Understat player ID).
# Used to bootstrap the match ID lookup.  Goalkeepers / outfield regulars preferred.
# These IDs are stable across seasons.
ANCHOR_PLAYERS = {
    "EPL":        1250,   # Mohamed Salah
    "La_liga":    839,    # Karim Benzema — if unavailable the fallback covers it
    "Bundesliga": 227,    # Robert Lewandowski (historical, still indexed)
    "Serie_A":    186,    # Ciro Immobile
    "Ligue_1":    600,    # Kylian Mbappé
}

# ── In-memory caches ─────────────────────────────────────────────────────────
# player_matches_cache: player_id → (timestamp, list[match_dict])
_player_cache: dict[int, tuple[float, list]] = {}

# match_id_cache: (date, home_norm, away_norm) → match_id
_match_id_cache: dict[tuple, str] = {}

# result_cache: match_id → (timestamp, result_dict)
_result_cache: dict[str, tuple[float, dict]] = {}

PLAYER_TTL = 86_400      # 24 h — player match list doesn't change much
RESULT_TTL = 600         # 10 min — xG for live matches can update


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _norm(name: str) -> str:
    """Strip common suffixes/noise for matching."""
    n = name.lower()
    for w in ("fc", "af", "cf", "sc", "ac", " united", " city", " hotspur",
              " wanderers", " athletic", " albion", " rovers", " town"):
        n = n.replace(w, "")
    return n.strip()


def _season_label(date_str: str, end_year: bool) -> str:
    """
    Return the season label Understat uses.
    EPL / La Liga etc. use END year: 2024-25 → '2025'.
    """
    try:
        year  = int(date_str[:4])
        month = int(date_str[5:7])
        start = year if month >= 8 else year - 1
        return str(start + 1) if end_year else str(start)
    except Exception:
        return "2025"


# match_data_cache: match_id → (timestamp, {rosters, shots})
_match_data_cache: dict[str, tuple[float, dict]] = {}
MATCH_DATA_TTL = 24 * 3600   # historical matches don't change


def _extract_match_info(html: str) -> dict | None:
    """Extract the var match_info = JSON.parse('...') block from a match page."""
    m = re.search(r"var match_info\s*=\s*JSON\.parse\('(.+?)'\)", html, re.DOTALL)
    if not m:
        return None
    raw = m.group(1)
    try:
        raw = raw.encode("raw_unicode_escape").decode("unicode_escape")
        return json.loads(raw)
    except Exception:
        return None


# ── Core data fetching ────────────────────────────────────────────────────────

def _get_player_matches(player_id: int) -> list[dict]:
    """
    Returns all matches for a player from main/getPlayerMatches/{id}.
    Cached for 24 h.
    """
    now = time.time()
    if player_id in _player_cache:
        ts, data = _player_cache[player_id]
        if now - ts < PLAYER_TTL:
            return data

    try:
        r = requests.post(
            f"{BASE}/main/getPlayerMatches/{player_id}",
            headers=HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        body = r.json()
        matches = body.get("response", {}).get("matches", [])
    except Exception:
        matches = []

    _player_cache[player_id] = (now, matches)
    return matches


def _find_match_id(
    home_team: str, away_team: str, match_date: str, league_slug: str, season: str
) -> str | None:
    """
    Searches the anchor player's match list for a game matching the given
    date and team names.  Returns the Understat match ID string or None.
    """
    cache_key = (match_date, _norm(home_team), _norm(away_team))
    if cache_key in _match_id_cache:
        return _match_id_cache[cache_key]

    anchor = ANCHOR_PLAYERS.get(league_slug)
    if not anchor:
        return None

    matches = _get_player_matches(anchor)
    # Filter by season and date
    for m in matches:
        if m.get("season") != season:
            continue
        if (m.get("date") or "")[:10] != match_date:
            continue
        mh = m.get("h_team", "")
        ma = m.get("a_team", "")
        sim = min(_sim(home_team, mh), _sim(away_team, ma))
        if sim >= 0.55:
            mid = m["id"]
            _match_id_cache[cache_key] = mid
            print(f"[Understat] Found match ID {mid} for {home_team} vs {away_team}")
            return mid

    # Anchor player may not have played in this match — scan all matches on this date
    # from the same season and return the best team-name match
    day_matches = [m for m in matches if (m.get("date") or "")[:10] == match_date and m.get("season") == season]
    if not day_matches:
        print(f"[Understat] No match found for {home_team} vs {away_team} on {match_date}")
        _match_id_cache[cache_key] = None
        return None

    best = max(day_matches, key=lambda m: min(_sim(home_team, m.get("h_team", "")),
                                              _sim(away_team, m.get("a_team", ""))))
    best_sim = min(_sim(home_team, best.get("h_team", "")), _sim(away_team, best.get("a_team", "")))
    if best_sim >= 0.4:
        mid = best["id"]
        _match_id_cache[cache_key] = mid
        return mid

    _match_id_cache[cache_key] = None
    return None


# ── Match shot + roster data ──────────────────────────────────────────────────

def _get_match_data(match_id: str) -> dict | None:
    """
    Fetches /main/getMatchData/{match_id} which returns:
      {
        rosters: { h: {roster_id: {player_id, player, shots, xG, xA, key_passes, ...}},
                   a: {...} },
        shots:   { h: [{player_id, player, X, Y, xG, lastAction, player_assisted, ...}],
                   a: [...] }
      }
    Cached 24 h — historical matches never change.
    """
    now = time.time()
    cached = _match_data_cache.get(match_id)
    if cached:
        ts, data = cached
        if now - ts < MATCH_DATA_TTL:
            return data

    try:
        r = requests.get(
            f"{BASE}/main/getMatchData/{match_id}",
            headers={**HEADERS, "X-Requested-With": "XMLHttpRequest",
                     "Referer": f"{BASE}/match/{match_id}"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        print(f"[Understat] getMatchData failed for {match_id}: {exc}")
        return None

    _match_data_cache[match_id] = (now, data)
    return data


# Penalty-area threshold in Understat's 0-1 coordinate system.
# The penalty area begins 16.5 m from the goal line on a 105 m pitch → X > 0.843
_BOX_X_THRESHOLD = 0.843


def extract_player_shot_stats(
    match_data: dict,
    player_id: str,    # Understat player_id string
    player_name: str,  # Used to match player_assisted field
) -> dict:
    """
    Derive attacking detail stats for one player from one match's shot data.

    Returns:
      shots_total, shots_inside_box, shots_outside_box,
      crosses_into_box,   ← shots where player_assisted == player_name AND lastAction == 'Cross'
      box_key_passes,     ← shots where player_assisted == player_name AND X >= box threshold
      outside_box_key_passes,
      big_chances_created ← assisted shots with xG > 0.20
      headers_created     ← shots where shotType == 'Head' AND player_assisted == player_name
      minutes             ← from rosters data
    """
    if not match_data:
        return {}

    shots_dict = match_data.get("shots", {})
    all_shots = shots_dict.get("h", []) + shots_dict.get("a", [])

    # Shots TAKEN by this player
    by_player = [s for s in all_shots if str(s.get("player_id")) == str(player_id)]

    shots_inside_box  = sum(1 for s in by_player if float(s.get("X", 0)) >= _BOX_X_THRESHOLD)
    shots_outside_box = sum(1 for s in by_player if float(s.get("X", 0)) < _BOX_X_THRESHOLD)

    # Shots CREATED by this player (where they were the last action before the shot)
    # Match player_assisted against name (case-insensitive partial match)
    name_lower = player_name.lower().strip()
    def _assisted_by(shot):
        pa = (shot.get("player_assisted") or "").lower().strip()
        return pa and (pa == name_lower or name_lower in pa or pa in name_lower)

    created = [s for s in all_shots if _assisted_by(s)]
    crosses_into_box      = sum(1 for s in created if s.get("lastAction") == "Cross")
    box_key_passes        = sum(1 for s in created if float(s.get("X", 0)) >= _BOX_X_THRESHOLD)
    outside_box_key_passes= sum(1 for s in created if float(s.get("X", 0)) < _BOX_X_THRESHOLD)
    big_chances_created   = sum(1 for s in created if float(s.get("xG", 0)) >= 0.20)
    headers_created       = sum(1 for s in created if s.get("shotType") == "Head")

    # Minutes played — from rosters
    minutes = 0
    for side in ("h", "a"):
        for roster_entry in match_data.get("rosters", {}).get(side, {}).values():
            if str(roster_entry.get("player_id")) == str(player_id):
                minutes = float(roster_entry.get("time", 0) or 0)
                break

    return {
        "shots_total":           len(by_player),
        "shots_inside_box":      shots_inside_box,
        "shots_outside_box":     shots_outside_box,
        "crosses_into_box":      crosses_into_box,
        "box_key_passes":        box_key_passes,
        "outside_box_key_passes":outside_box_key_passes,
        "big_chances_created":   big_chances_created,
        "headers_created":       headers_created,
        "minutes":               minutes,
    }


def get_player_shot_history(
    player_id: int,
    player_name: str,
    n_games: int = 5,
) -> dict | None:
    """
    Aggregates shot-detail stats (crosses, outside-box shots, box key passes)
    across the player's last n_games matches.

    Returns per-90 stats dict or None if no data available.
    Uses match-level caching — teammates share the same cached match pages.
    """
    all_matches = _get_player_matches(player_id)
    if not all_matches:
        return None

    recent = sorted(all_matches, key=lambda m: m.get("date", ""), reverse=True)[:n_games]

    totals = {
        "shots_total":           0,
        "shots_inside_box":      0,
        "shots_outside_box":     0,
        "crosses_into_box":      0,
        "box_key_passes":        0,
        "outside_box_key_passes":0,
        "big_chances_created":   0,
        "headers_created":       0,
        "minutes":               0,
    }
    games_with_data = 0

    for m in recent:
        mid = m.get("id")
        if not mid:
            continue
        md = _get_match_data(str(mid))
        if not md:
            continue
        stats = extract_player_shot_stats(md, str(player_id), player_name)
        if not stats:
            continue

        for key in totals:
            totals[key] += stats.get(key, 0)
        games_with_data += 1

    if games_with_data == 0 or totals["minutes"] < 45:
        return None

    mins = totals["minutes"]

    def p90(v):
        return round(v * 90.0 / mins, 3) if mins > 0 else 0.0

    return {
        "raw":   totals,
        "games": games_with_data,
        "per90": {
            "shots_outside_box":      p90(totals["shots_outside_box"]),
            "crosses_into_box":       p90(totals["crosses_into_box"]),
            "box_key_passes":         p90(totals["box_key_passes"]),
            "outside_box_key_passes": p90(totals["outside_box_key_passes"]),
            "big_chances_created":    p90(totals["big_chances_created"]),
            "headers_created":        p90(totals["headers_created"]),
            "shots_total":            p90(totals["shots_total"]),
            "shots_inside_box":       p90(totals["shots_inside_box"]),
        },
    }


# ── Public API ────────────────────────────────────────────────────────────────

def get_match_xg(
    home_team: str,
    away_team: str,
    match_date: str,        # "YYYY-MM-DD"
    competition_code: str,  # football-data.org code, e.g. "PL"
) -> dict | None:
    """
    Returns xG, PPDA, deep completions, shots data for a match from Understat.
    Returns None if league not supported or match not found.

    Result dict:
      home / away:
        xg, ppda, deep_completions, shots, shots_on_target, goals
      match_id: str
    """
    config = LEAGUE_CONFIG.get(competition_code)
    if not config:
        return None

    slug   = config["slug"]
    season = _season_label(match_date, config["end_year"])

    match_id = _find_match_id(home_team, away_team, match_date, slug, season)
    if not match_id:
        return None

    # Check result cache
    now = time.time()
    if match_id in _result_cache:
        ts, cached = _result_cache[match_id]
        if now - ts < RESULT_TTL:
            return cached

    # Fetch the match page to extract match_info
    try:
        r = requests.get(
            f"{BASE}/match/{match_id}",
            headers=HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        info = _extract_match_info(r.text)
    except Exception:
        return None

    if not info:
        return None

    def flt(val):
        try:
            return float(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    result = {
        "match_id": match_id,
        "home": {
            "xg":               flt(info.get("h_xg")),
            "ppda":             flt(info.get("h_ppda")),     # passes per def. action (lower = more pressing)
            "deep_completions": flt(info.get("h_deep")),     # passes into final third
            "shots":            flt(info.get("h_shot")),
            "shots_on_target":  flt(info.get("h_shotOnTarget")),
            "goals":            flt(info.get("h_goals")),
        },
        "away": {
            "xg":               flt(info.get("a_xg")),
            "ppda":             flt(info.get("a_ppda")),
            "deep_completions": flt(info.get("a_deep")),
            "shots":            flt(info.get("a_shot")),
            "shots_on_target":  flt(info.get("a_shotOnTarget")),
            "goals":            flt(info.get("a_goals")),
        },
    }

    _result_cache[match_id] = (now, result)
    print(
        f"[Understat] {home_team} vs {away_team} — "
        f"xG: {result['home']['xg']} – {result['away']['xg']} | "
        f"PPDA: {result['home']['ppda']} – {result['away']['ppda']}"
    )
    return result


# ── League team season data ───────────────────────────────────────────────────
# (league_slug, season) → (timestamp, {team_title: stats_dict})
_league_teams_cache: dict = {}
TEAMS_DATA_TTL = 6 * 3600   # 6 h — updates after each gameweek


def get_league_teams_data(competition_code: str, match_date: str) -> dict:
    """
    Fetches all teams' season stats from Understat for a given league/season.

    Returns:
      {team_title: {
        id, matches,
        xg_per_game, xga_per_game,
        ppda_avg,            # passes per defensive action (lower = more pressing)
        deep_per_game,       # passes completed into final third
        scored_per_game, missed_per_game,
        clean_sheet_rate,    # fraction of games with 0 goals conceded
        btts_rate,           # both teams scored
        over25_rate,         # total goals > 2
        over35_rate,         # total goals > 3
        home_xg_pg, home_xga_pg, home_cs_rate,
        away_xg_pg, away_xga_pg, away_cs_rate,
      }}
    Returns {} if league not supported or request fails.
    """
    config = LEAGUE_CONFIG.get(competition_code)
    if not config:
        return {}

    slug   = config["slug"]
    season = _season_label(match_date, config["end_year"])
    cache_key = (slug, season)

    now = time.time()
    cached = _league_teams_cache.get(cache_key)
    if cached:
        ts, data = cached
        if now - ts < TEAMS_DATA_TTL:
            return data

    # Use the understat pip library (async, wrapped via asyncio.run)
    try:
        import asyncio
        import aiohttp as _aiohttp
        import understat as _ulib

        async def _fetch_table():
            async with _aiohttp.ClientSession() as session:
                u = _ulib.Understat(session)
                return await u.get_league_table(slug, season)

        rows = asyncio.run(_fetch_table())
    except Exception as exc:
        print(f"[Understat] get_league_table failed for {slug}/{season}: {exc}")
        _league_teams_cache[cache_key] = (now, {})
        return {}

    if not rows or not isinstance(rows, list) or len(rows) < 2:
        _league_teams_cache[cache_key] = (now, {})
        return {}

    # rows[0] = column headers, rows[1:] = one row per team
    headers = rows[0]
    result = {}

    for row in rows[1:]:
        if len(row) < len(headers):
            continue
        entry = dict(zip(headers, row))
        team_name = entry.get("Team", "")
        if not team_name:
            continue

        m_played = int(entry.get("M", 1) or 1)

        def _pg(val):
            return round(float(val or 0) / m_played, 3)

        # PPDA is already a per-game average in Understat's table
        ppda_val = entry.get("PPDA")
        ppda_avg = round(float(ppda_val), 2) if ppda_val else None

        result[team_name] = {
            "matches":         m_played,
            "xg_per_game":     _pg(entry.get("xG",  0)),
            "xga_per_game":    _pg(entry.get("xGA", 0)),
            "ppda_avg":        ppda_avg,
            "deep_per_game":   _pg(entry.get("DC",  0)),
            "scored_per_game": _pg(entry.get("G",   0)),
            "missed_per_game": _pg(entry.get("GA",  0)),
            # Rates not provided by the league table — filled from match-history
            # fallback in football_data.get_team_season_stats()
            "clean_sheet_rate": None,
            "btts_rate":        None,
            "over25_rate":      None,
            "over35_rate":      None,
            "home_xg_pg":       None,
            "home_xga_pg":      None,
            "home_cs_rate":     None,
            "away_xg_pg":       None,
            "away_xga_pg":      None,
            "away_cs_rate":     None,
        }

    _league_teams_cache[cache_key] = (now, result)
    print(f"[Understat] Loaded team data for {slug}/{season}: {len(result)} teams")
    return result


def generate_xg_insights(home_name: str, away_name: str, xg_data: dict) -> list[dict]:
    """
    Generates insight cards from Understat xG + advanced metrics.
    """
    if not xg_data:
        return []

    insights = []
    h = xg_data.get("home", {})
    a = xg_data.get("away", {})

    home_xg    = h.get("xg") or 0
    away_xg    = a.get("xg") or 0
    home_goals = h.get("goals") or 0
    away_goals = a.get("goals") or 0
    home_ppda  = h.get("ppda")
    away_ppda  = a.get("ppda")
    home_deep  = h.get("deep_completions") or 0
    away_deep  = a.get("deep_completions") or 0
    home_shots = h.get("shots") or 0
    away_shots = a.get("shots") or 0
    home_sot   = h.get("shots_on_target") or 0
    away_sot   = a.get("shots_on_target") or 0

    # ── xG vs actual goals ────────────────────────────────────────────────────
    if home_xg and away_xg:
        home_diff = home_goals - home_xg
        away_diff = away_goals - away_xg

        if home_diff >= 0.8:
            insights.append({
                "icon": "🍀",
                "title": f"{home_name} outperformed their xG",
                "body": (
                    f"Expected to score {home_xg:.2f} based on chance quality but converted {home_goals} "
                    f"— {home_diff:+.1f} above expectation. Clinical finishing or an off-day for the keeper."
                ),
            })
        elif home_diff <= -0.8 and home_xg >= 1.0:
            insights.append({
                "icon": "😬",
                "title": f"{home_name} underperformed their xG",
                "body": (
                    f"Created enough quality to score {home_xg:.2f} goals but only netted {home_goals}. "
                    f"Poor finishing or a spectacular goalkeeping display denied them what the numbers suggest."
                ),
            })

        if away_diff >= 0.8:
            insights.append({
                "icon": "🍀",
                "title": f"{away_name} outperformed their xG",
                "body": (
                    f"Expected {away_xg:.2f} goals from their chances, scored {away_goals} "
                    f"— {away_diff:+.1f} above expectation. They made every opportunity count."
                ),
            })
        elif away_diff <= -0.8 and away_xg >= 1.0:
            insights.append({
                "icon": "😬",
                "title": f"{away_name} underperformed their xG",
                "body": (
                    f"Their {away_xg:.2f} xG deserved more than {away_goals} goal(s). "
                    f"They dominated in chance quality but couldn't convert."
                ),
            })

    # ── Pressing intensity (PPDA) ─────────────────────────────────────────────
    # PPDA = passes conceded per defensive action. Lower = more intense pressing
    if home_ppda and away_ppda:
        if home_ppda < 7 and home_ppda < away_ppda * 0.7:
            insights.append({
                "icon": "⚡",
                "title": f"{home_name} — elite high press",
                "body": (
                    f"PPDA of {home_ppda:.1f} — they allowed only {home_ppda:.1f} opposition passes per defensive "
                    f"action vs {away_ppda:.1f} for {away_name}. An aggressive, suffocating press that limited the "
                    f"opposition's ability to build from the back."
                ),
            })
        elif away_ppda < 7 and away_ppda < home_ppda * 0.7:
            insights.append({
                "icon": "⚡",
                "title": f"{away_name} — elite high press",
                "body": (
                    f"PPDA of {away_ppda:.1f} vs {home_ppda:.1f} for {home_name}. "
                    f"{away_name} pressed relentlessly, winning the ball high and denying {home_name} time on the ball."
                ),
            })
        elif home_ppda > 15 and home_ppda > away_ppda * 1.5:
            insights.append({
                "icon": "🧱",
                "title": f"{home_name} sat deep, surrendered possession",
                "body": (
                    f"PPDA of {home_ppda:.1f} — a very passive defensive approach. {home_name} "
                    f"allowed {away_name} to pass freely before engaging, prioritising shape over press."
                ),
            })

    # ── Deep completions (passes into the final third / attacking momentum) ───
    if home_deep and away_deep and (home_deep + away_deep) > 0:
        if home_deep > away_deep * 2 and home_deep >= 10:
            insights.append({
                "icon": "📈",
                "title": f"{home_name} — dominant attacking momentum",
                "body": (
                    f"{int(home_deep)} deep completions (passes into the final third) vs "
                    f"{int(away_deep)} for {away_name}. {home_name} spent far more time in dangerous "
                    f"areas, maintaining sustained attacking pressure throughout."
                ),
            })
        elif away_deep > home_deep * 2 and away_deep >= 10:
            insights.append({
                "icon": "📈",
                "title": f"{away_name} — dominant attacking momentum",
                "body": (
                    f"{int(away_deep)} deep completions vs {int(home_deep)} for {home_name}. "
                    f"{away_name} consistently penetrated the final third, generating wave after wave of pressure."
                ),
            })

    # ── Shot accuracy ─────────────────────────────────────────────────────────
    home_acc = (home_sot / home_shots * 100) if home_shots > 0 else 0
    away_acc = (away_sot / away_shots * 100) if away_shots > 0 else 0

    if home_shots >= 8 and home_acc < 25 and home_goals == 0:
        insights.append({
            "icon": "😤",
            "title": f"{home_name} — volume without precision",
            "body": (
                f"{int(home_shots)} shots but only {int(home_sot)} on target ({home_acc:.0f}% accuracy). "
                f"Plenty of attempts but most were wayward — poor decision-making in the final third."
            ),
        })
    elif away_shots >= 8 and away_acc < 25 and away_goals == 0:
        insights.append({
            "icon": "😤",
            "title": f"{away_name} — volume without precision",
            "body": (
                f"{int(away_shots)} shots, only {int(away_sot)} on target ({away_acc:.0f}% accuracy). "
                f"Lots of effort but lacked the composure to test the goalkeeper regularly."
            ),
        })

    return insights
