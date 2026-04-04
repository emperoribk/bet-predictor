"""
API-Football (api-sports.io) integration.
Provides injury and lineup data per fixture to enrich prediction signals.
"""
import time
import requests
from difflib import SequenceMatcher
from django.conf import settings

BASE_URL = "https://v3.football.api-sports.io"

# Simple in-memory caches — injuries/lineups don't change often
_injuries_cache: dict = {}
_lineups_cache: dict = {}
_CACHE_TTL = 3 * 3600   # 3 hours

# Quota guard — once the daily limit is hit, skip all further API calls
_quota_exhausted: bool = False
# Connection failure counter — stop after 3 consecutive failures (timeouts/network errors)
_consecutive_failures: int = 0
_MAX_CONSECUTIVE_FAILURES = 3


_NOISE = {"fc", "cf", "rc", "rcd", "as", "ss", "us", "ac", "sc", "rb", "vfl",
          "bsc", "rsca", "afc", "ssc", "1.", "fsv", "sv", "tsg", "ogc", "sl"}


def _name_sim(api_name: str, our_name: str) -> float:
    """
    Similarity score between an API-Football team name and one of our team names.
    Strips common noise prefixes before comparing so that e.g.
    'FC Barcelona' vs 'FC Barcelona' → 1.0 but
    'FC Barcelona' vs 'RCD Espanyol de Barcelona' → ~0.5 (different club).
    """
    def _clean(n: str) -> str:
        parts = [w for w in n.lower().split() if w not in _NOISE]
        return " ".join(parts)

    a = _clean(api_name)
    b = _clean(our_name)
    return SequenceMatcher(None, a, b).ratio()


def _headers() -> dict:
    return {
        "x-apisports-key": settings.API_FOOTBALL_KEY,
    }


def _api_get(endpoint: str, params: dict) -> dict | None:
    """Raw GET with basic error handling. Returns parsed JSON or None."""
    global _quota_exhausted, _consecutive_failures
    if _quota_exhausted:
        return None
    if not settings.API_FOOTBALL_KEY:
        return None
    try:
        resp = requests.get(
            f"{BASE_URL}/{endpoint}",
            headers=_headers(),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        # API-Football wraps results in {"response": [...], "errors": {...}}
        errors = data.get("errors", {})
        if errors:
            err_str = str(errors)
            if "request limit" in err_str or "requests" in errors:
                _quota_exhausted = True
                print("[API-Football] Daily quota exhausted — skipping all further API-Football calls.")
            else:
                print(f"[API-Football] Error on /{endpoint}: {errors}")
            return None
        _consecutive_failures = 0  # reset on success
        return data
    except requests.RequestException as e:
        _consecutive_failures += 1
        if _consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            _quota_exhausted = True
            print(f"[API-Football] {_consecutive_failures} consecutive connection failures — API unreachable, skipping all further calls.")
        else:
            print(f"[API-Football] Request failed /{endpoint}: {e}")
        return None


# ── League ID map (API-Football uses numeric IDs) ─────────────────────────────
_LEAGUE_IDS = {
    # ── Big 5 (Understat xG available) ──────────────────────────────────────
    "PL":   39,   # Premier League (England)
    "PD":   140,  # La Liga (Spain)
    "BL1":  78,   # Bundesliga (Germany)
    "SA":   135,  # Serie A (Italy)
    "FL1":  61,   # Ligue 1 (France)
    # ── Extended (API-Football xG fallback) ─────────────────────────────────
    "DED":  88,   # Eredivisie (Netherlands)
    "PPL":  94,   # Primeira Liga (Portugal)
    "ELC":  40,   # Championship (England)
    "SPL":  179,  # Scottish Premiership
    "BJL":  144,  # Jupiler Pro League (Belgium)
    "TSL":  203,  # Süper Lig (Turkey)
    "BL2":  79,   # 2. Bundesliga (Germany)
    "BSA":  71,   # Campeonato Brasileiro Série A
    "MLS":  253,  # Major League Soccer (USA)
    "SSL":  207,  # Super League (Switzerland)
    "ASL":  128,  # Liga Profesional (Argentina)
    "ABL":  218,  # Bundesliga (Austria)
    # ── European cups ───────────────────────────────────────────────────────
    "CL":   2,    # UEFA Champions League
    "EL":   3,    # UEFA Europa League
    "UECL": 848,  # UEFA Conference League
    # ── Internal ────────────────────────────────────────────────────────────
    "SA2":  136,  # Serie B (Italy)
}

CURRENT_SEASON = 2025   # 2025/26 season

# ── Team xG stats cache (fallback for non-Understat leagues) ──────────────────
_team_xg_cache: dict = {}
_XG_CACHE_TTL = 6 * 3600   # 6 hours


def get_team_xg_stats(team_id: int, comp_code: str, season: int = CURRENT_SEASON, n: int = 5) -> dict:
    """
    Fetches xG stats for a team from their last N finished fixtures via API-Football.
    Used as a fallback xG source for leagues not covered by Understat (big-5 only).

    Returns dict with: xg_per_game, xga_per_game, home_xg_pg, home_xga_pg,
                       away_xg_pg, away_xga_pg
    Returns {} if unavailable or league not mapped.
    """
    if not settings.API_FOOTBALL_KEY or not team_id:
        return {}

    league_id = _LEAGUE_IDS.get(comp_code)
    if not league_id:
        return {}

    now = time.time()
    cache_key = (team_id, comp_code, season)
    cached = _team_xg_cache.get(cache_key)
    if cached:
        ts, data = cached
        if now - ts < _XG_CACHE_TTL:
            return data

    # Step 1: last N finished fixtures for this team in this league/season
    fix_data = _api_get("fixtures", {
        "team": team_id,
        "league": league_id,
        "season": season,
        "last": n,
    })
    if not fix_data:
        _team_xg_cache[cache_key] = (now, {})
        return {}

    fixtures = fix_data.get("response", [])
    if not fixtures:
        _team_xg_cache[cache_key] = (now, {})
        return {}

    # Step 2: collect xG from each fixture's statistics
    xg_for, xg_against = [], []
    home_xg_list, home_xga_list = [], []
    away_xg_list, away_xga_list = [], []

    for fix in fixtures:
        fix_id = fix.get("fixture", {}).get("id")
        if not fix_id:
            continue
        home_id = fix.get("teams", {}).get("home", {}).get("id")
        is_home = (home_id == team_id)

        stats_data = _api_get("fixtures/statistics", {"fixture": fix_id})
        if not stats_data:
            continue

        team_xg = opp_xg = None
        for team_stats in stats_data.get("response", []):
            tid = team_stats.get("team", {}).get("id")
            for stat in team_stats.get("statistics", []):
                if stat.get("type") == "expected_goals":
                    try:
                        val = float(stat.get("value") or 0)
                    except (TypeError, ValueError):
                        val = None
                    if tid == team_id:
                        team_xg = val
                    else:
                        opp_xg = val

        if team_xg is not None:
            xg_for.append(team_xg)
            (home_xg_list if is_home else away_xg_list).append(team_xg)
        if opp_xg is not None:
            xg_against.append(opp_xg)
            (home_xga_list if is_home else away_xga_list).append(opp_xg)

    if not xg_for:
        _team_xg_cache[cache_key] = (now, {})
        return {}

    def _avg(lst):
        return round(sum(lst) / len(lst), 3) if lst else None

    result = {
        "xg_per_game":  _avg(xg_for),
        "xga_per_game": _avg(xg_against),
        "home_xg_pg":   _avg(home_xg_list),
        "home_xga_pg":  _avg(home_xga_list),
        "away_xg_pg":   _avg(away_xg_list),
        "away_xga_pg":  _avg(away_xga_list),
    }
    _team_xg_cache[cache_key] = (now, result)
    print(f"[API-Football xG] {comp_code} team {team_id}: xG={result['xg_per_game']}, xGA={result['xga_per_game']}")
    return result


def get_fixture_id(home_team: str, away_team: str, date: str, comp_code: str) -> int | None:
    """
    Look up the API-Football fixture ID for a match.
    Returns None if not found or API unavailable.
    """
    league_id = _LEAGUE_IDS.get(comp_code)
    if not league_id:
        return None

    data = _api_get("fixtures", {
        "league": league_id,
        "season": CURRENT_SEASON,
        "date": date,
    })
    if not data:
        return None

    for fix in data.get("response", []):
        h = fix.get("teams", {}).get("home", {}).get("name", "").lower()
        a = fix.get("teams", {}).get("away", {}).get("name", "").lower()
        if home_team.lower()[:6] in h or h[:6] in home_team.lower():
            if away_team.lower()[:6] in a or a[:6] in away_team.lower():
                return fix.get("fixture", {}).get("id")
    return None


def get_injuries(fixture_id: int) -> dict:
    """
    Returns injury/suspension data for a fixture.
    Result: {
        "home": [{"player": str, "reason": str, "type": str}, ...],
        "away": [{"player": str, "reason": str, "type": str}, ...],
    }
    """
    now = time.time()
    cached = _injuries_cache.get(fixture_id)
    if cached:
        ts, data = cached
        if now - ts < _CACHE_TTL:
            return data

    data = _api_get("injuries", {"fixture": fixture_id})
    result = {"home": [], "away": []}

    if not data:
        _injuries_cache[fixture_id] = (now, result)
        return result

    for entry in data.get("response", []):
        team_side = entry.get("team", {}).get("name", "")
        player_name = entry.get("player", {}).get("name", "")
        reason = entry.get("player", {}).get("reason", "")
        typ = entry.get("player", {}).get("type", "")   # "Missing Fixture" | "Questionable"

        record = {"player": player_name, "reason": reason, "type": typ}
        # We don't know home/away from name alone — store both sides keyed by team name
        side_key = entry.get("team", {}).get("id")
        if side_key not in result:
            result[side_key] = []
        result[side_key].append(record)

    _injuries_cache[fixture_id] = (now, result)
    return result


def get_injuries_summary(fixture_id: int) -> dict:
    """
    Returns a simplified summary keyed by team API-Football ID:
    {
        team_id: {
            "missing": int,       # confirmed out
            "questionable": int,  # doubt
            "players": [str],     # names of missing players
        }
    }
    """
    now = time.time()
    cached = _injuries_cache.get(f"summary_{fixture_id}")
    if cached:
        ts, data = cached
        if now - ts < _CACHE_TTL:
            return data

    data = _api_get("injuries", {"fixture": fixture_id})
    result = {}

    if not data:
        _injuries_cache[f"summary_{fixture_id}"] = (now, result)
        return result

    for entry in data.get("response", []):
        team_id   = entry.get("team", {}).get("id")
        team_name = entry.get("team", {}).get("name", "")
        p_name    = entry.get("player", {}).get("name", "")
        p_type    = entry.get("player", {}).get("type", "")  # "Missing Fixture" | "Questionable"

        if team_id not in result:
            result[team_id] = {"team_name": team_name, "missing": 0, "questionable": 0, "players": []}

        if p_type == "Missing Fixture":
            result[team_id]["missing"] += 1
            result[team_id]["players"].append(p_name)
        elif p_type == "Questionable":
            result[team_id]["questionable"] += 1

    _injuries_cache[f"summary_{fixture_id}"] = (now, result)
    return result


def get_fixture_injuries_for_match(
    home_team: str,
    away_team: str,
    date: str,
    comp_code: str,
    home_api_football_id: int | None = None,
    away_api_football_id: int | None = None,
) -> dict:
    """
    High-level helper: given a match, returns injury counts for home and away.
    Returns:
    {
        "available": bool,
        "fixture_id": int | None,
        "home": {"missing": int, "questionable": int, "players": [str]},
        "away": {"missing": int, "questionable": int, "players": [str]},
    }
    """
    empty = {
        "available": False,
        "fixture_id": None,
        "home": {"missing": 0, "questionable": 0, "players": []},
        "away": {"missing": 0, "questionable": 0, "players": []},
    }

    if not settings.API_FOOTBALL_KEY:
        return empty

    fixture_id = get_fixture_id(home_team, away_team, date, comp_code)
    if not fixture_id:
        return empty

    summary = get_injuries_summary(fixture_id)
    if not summary:
        return {**empty, "available": True, "fixture_id": fixture_id}

    teams = list(summary.values())
    home_inj = {"missing": 0, "questionable": 0, "players": []}
    away_inj = {"missing": 0, "questionable": 0, "players": []}

    for tid, info in summary.items():
        api_name = info.get("team_name", "")
        h_score = _name_sim(api_name, home_team)
        a_score = _name_sim(api_name, away_team)
        # Only assign if similarity is meaningful AND clearly better than the other side
        if h_score >= 0.5 and h_score > a_score:
            home_inj = info
        elif a_score >= 0.5 and a_score > h_score:
            away_inj = info

    return {
        "available": True,
        "fixture_id": fixture_id,
        "home": home_inj,
        "away": away_inj,
    }
