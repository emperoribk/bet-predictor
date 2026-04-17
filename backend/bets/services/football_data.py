import re
import time as _time
import concurrent.futures
import requests
from difflib import SequenceMatcher
from datetime import datetime, timedelta
from django.conf import settings
from .understat import get_match_xg, generate_xg_insights


BASE_URL = "https://api.football-data.org/v4"

# Noise words stripped before comparison
_STRIP_WORDS = {"fc", "cf", "sc", "bc", "ac", "afc", "fk", "sk", "united", "city",
                "the", "de", "du", "di", "van", "1.", "fsv", "sv", "as", "ss",
                "og", "og c", "rc", "rb", "tsg", "ogc", "losc", "sl", "kv"}


def _normalise(name: str) -> str:
    """Lowercase, strip punctuation and common noise words."""
    n = name.lower()
    n = re.sub(r"['''\-\.&]", " ", n)   # replace punctuation with space
    n = re.sub(r"\s+", " ", n).strip()
    return n


def _tokens(name: str) -> set:
    return {w for w in _normalise(name).split() if w not in _STRIP_WORDS and len(w) > 1}


def _similarity(a: str, b: str) -> float:
    """Combined score: character-level similarity + Jaccard token overlap."""
    na, nb = _normalise(a), _normalise(b)
    char_ratio = SequenceMatcher(None, na, nb).ratio()

    ta, tb = _tokens(a), _tokens(b)
    if ta and tb:
        jaccard = len(ta & tb) / len(ta | tb)
    else:
        jaccard = 0.0

    return max(char_ratio, jaccard * 1.2)   # token overlap weighted slightly higher


def _team_matches(sporty_name: str, api_name: str, api_short: str, api_tla: str) -> bool:
    """
    Returns True if sporty_name likely refers to the same club.
    Tries exact/substring first, then fuzzy similarity fallback.
    """
    s = _normalise(sporty_name)
    a = _normalise(api_name)
    sh = _normalise(api_short) if api_short else ""
    tla = (api_tla or "").lower().strip()

    # Fast exact / substring checks
    if s == a or s == sh or s == tla:
        return True
    if s in a or a in s:
        return True
    if sh and (s in sh or sh in s):
        return True

    # Fuzzy fallback — match against full name and short name
    if _similarity(sporty_name, api_name) >= 0.72:
        return True
    if api_short and _similarity(sporty_name, api_short) >= 0.72:
        return True

    return False


def _headers():
    return {"X-Auth-Token": settings.FOOTBALL_DATA_API_KEY}


def find_fixture_id(home_team: str, away_team: str, kickoff_time=None) -> int | None:
    """
    Searches football-data.org for a fixture matching the two team names.
    Looks within a 3-day window around kickoff_time (or today if not provided).
    Returns the fixture ID or None if not found.
    """
    if kickoff_time:
        if isinstance(kickoff_time, datetime):
            date = kickoff_time.replace(tzinfo=None)
        elif isinstance(kickoff_time, (int, float)):
            date = datetime.utcfromtimestamp(kickoff_time / 1000)
        else:
            date = datetime.fromisoformat(str(kickoff_time).replace("Z", ""))
    else:
        date = datetime.utcnow()

    date_from = (date - timedelta(days=1)).strftime("%Y-%m-%d")
    date_to = (date + timedelta(days=2)).strftime("%Y-%m-%d")

    try:
        resp = requests.get(
            f"{BASE_URL}/matches",
            headers=_headers(),
            params={"dateFrom": date_from, "dateTo": date_to},
            timeout=10,
        )
        resp.raise_for_status()
        matches = resp.json().get("matches", [])
    except requests.RequestException:
        return None

    print(f"[find_fixture_id] Searching for: '{home_team}' vs '{away_team}' ({date_from} → {date_to}), {len(matches)} matches returned")

    for match in matches:
        api_home = match["homeTeam"]["name"]
        api_away = match["awayTeam"]["name"]
        api_home_short = match["homeTeam"].get("shortName", "")
        api_away_short = match["awayTeam"].get("shortName", "")
        api_home_tla = match["homeTeam"].get("tla", "")
        api_away_tla = match["awayTeam"].get("tla", "")

        home_match = _team_matches(home_team, api_home, api_home_short, api_home_tla)
        away_match = _team_matches(away_team, api_away, api_away_short, api_away_tla)

        if home_match and away_match:
            print(f"[find_fixture_id] MATCHED: '{home_team}' → '{api_home}' | '{away_team}' → '{api_away}' (id={match['id']})")
            return match["id"]

    print(f"[find_fixture_id] NOT FOUND: '{home_team}' vs '{away_team}'")
    print(f"[find_fixture_id] Available teams: {[m['homeTeam']['name'] + ' vs ' + m['awayTeam']['name'] for m in matches[:10]]}")
    return None


def get_live_match(fixture_id: int) -> dict | None:
    """
    Fetches full live data for a single fixture.
    Returns a structured dict with score, stats, and events.
    """
    try:
        resp = requests.get(
            f"{BASE_URL}/matches/{fixture_id}",
            headers={
                **_headers(),
                "X-Unfold-Goals": "true",
                "X-Unfold-Bookings": "true",
                "X-Unfold-Subs": "true",
                "X-Unfold-Lineups": "true",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        return None

    score = data.get("score", {})
    full_time = score.get("fullTime", {})
    home_score = full_time.get("home") or 0
    away_score = full_time.get("away") or 0

    # Parse statistics (requires Stats-Package on football-data.org)
    home_stats = {}
    away_stats = {}
    home_team_data = data.get("homeTeam", {})
    away_team_data = data.get("awayTeam", {})

    def _parse_team_stats(team_data, group_label, top_level):
        raw = team_data.get("statistics", [])
        if isinstance(raw, list) and raw:
            result = {s["type"]: s.get("value") for s in raw if isinstance(s, dict) and s.get("type")}
            if result:
                return result
        if top_level:
            result = {}
            for s in top_level:
                if isinstance(s, dict) and s.get("group", "").upper() == group_label:
                    key = (s.get("type") or s.get("name", "")).upper().replace(" ", "_")
                    result[key] = s.get("value")
            return result
        return {}

    def _stat(d, *keys):
        for key in keys:
            v = d.get(key)
            if v is not None:
                try:
                    return float(str(v).replace("%", ""))
                except (ValueError, TypeError):
                    continue
        return None

    top_stats = data.get("statistics", []) if isinstance(data.get("statistics"), list) else []
    home_stats = _parse_team_stats(home_team_data, "HOME", top_stats)
    away_stats = _parse_team_stats(away_team_data, "AWAY", top_stats)

    # Parse events
    events = []
    for goal in data.get("goals", []):
        events.append({
            "type": "GOAL",
            "minute": goal.get("minute"),
            "team": goal.get("team", {}).get("name"),
            "scorer": goal.get("scorer", {}).get("name"),
            "goal_type": goal.get("type"),
        })
    for booking in data.get("bookings", []):
        events.append({
            "type": booking.get("card"),
            "minute": booking.get("minute"),
            "team": booking.get("team", {}).get("name"),
            "player": booking.get("player", {}).get("name"),
        })
    for sub in data.get("substitutions", []):
        events.append({
            "type": "SUBSTITUTION",
            "minute": sub.get("minute"),
            "team": sub.get("team", {}).get("name"),
            "player_out": sub.get("playerOut", {}).get("name"),
            "player_in": sub.get("playerIn", {}).get("name"),
        })

    events.sort(key=lambda e: e.get("minute") or 0)

    # Count red cards from bookings
    red_cards_home = sum(
        1 for e in events
        if e["type"] in ("RED", "YELLOW_RED") and e.get("team") == home_team_data.get("name")
    )
    red_cards_away = sum(
        1 for e in events
        if e["type"] in ("RED", "YELLOW_RED") and e.get("team") == away_team_data.get("name")
    )


    _home_sot = _stat(home_stats, "SHOTS_ON_GOAL", "ON_TARGET", "SHOTS_ON_TARGET")
    _away_sot = _stat(away_stats, "SHOTS_ON_GOAL", "ON_TARGET", "SHOTS_ON_TARGET")

    _home_saves = _stat(home_stats, "GOALKEEPER_SAVES", "SAVES")
    _away_saves = _stat(away_stats, "GOALKEEPER_SAVES", "SAVES")
    if _home_saves is None and _away_saves is None and (_away_sot is not None or _home_sot is not None):
        _home_saves = max(0, int(_away_sot or 0) - int(away_score))
        _away_saves = max(0, int(_home_sot or 0) - int(home_score))

    return {
        "fixture_id": fixture_id,
        "match_status": data.get("status"),
        "match_minute": data.get("minute"),
        "score_home": home_score,
        "score_away": away_score,
        "possession_home": _stat(home_stats, "BALL_POSSESSION", "POSSESSION"),
        "possession_away": _stat(away_stats, "BALL_POSSESSION", "POSSESSION"),
        "shots_on_target_home": _home_sot,
        "shots_on_target_away": _away_sot,
        "shots_total_home": _stat(home_stats, "TOTAL_SHOTS", "SHOTS_TOTAL", "SHOTS"),
        "shots_total_away": _stat(away_stats, "TOTAL_SHOTS", "SHOTS_TOTAL", "SHOTS"),
        "corners_home": _stat(home_stats, "CORNER_KICKS", "CORNERS"),
        "corners_away": _stat(away_stats, "CORNER_KICKS", "CORNERS"),
        "fouls_home": _stat(home_stats, "FOULS", "TOTAL_FOULS"),
        "fouls_away": _stat(away_stats, "FOULS", "TOTAL_FOULS"),
        "offsides_home": _stat(home_stats, "OFFSIDES"),
        "offsides_away": _stat(away_stats, "OFFSIDES"),
        "saves_home": _home_saves,
        "saves_away": _away_saves,
        "red_cards_home": red_cards_home,
        "red_cards_away": red_cards_away,
        "events": events,
    }


def _parse_match_row(m: dict) -> dict:
    """Shared parser for a match object from football-data.org."""
    score = m.get("score", {})
    ft    = score.get("fullTime", {})
    ht    = score.get("halfTime", {})

    # fullTime is null for some competitions (e.g. early-season Scandinavian
    # leagues with limited coverage). Fall back to regularTime, then halfTime.
    score_home = ft.get("home")
    score_away = ft.get("away")
    if score_home is None or score_away is None:
        rt = score.get("regularTime") or {}
        if rt.get("home") is not None:
            score_home = rt["home"]
            score_away = rt["away"]

    return {
        "id":           m["id"],
        "competition":  m.get("competition", {}).get("name", "Unknown"),
        "competition_code": m.get("competition", {}).get("code", ""),
        "area":         m.get("area", {}).get("name", ""),
        "home_team":    m.get("homeTeam", {}).get("name", ""),
        "home_short":   m.get("homeTeam", {}).get("shortName") or m.get("homeTeam", {}).get("name", ""),
        "home_team_id": m.get("homeTeam", {}).get("id"),
        "away_team":    m.get("awayTeam", {}).get("name", ""),
        "away_short":   m.get("awayTeam", {}).get("shortName") or m.get("awayTeam", {}).get("name", ""),
        "away_team_id": m.get("awayTeam", {}).get("id"),
        "status":       m.get("status", "SCHEDULED"),
        "minute":       m.get("minute"),
        "kickoff":      m.get("utcDate"),
        "score_home":   score_home,
        "score_away":   score_away,
        "ht_home":      ht.get("home"),
        "ht_away":      ht.get("away"),
    }


def get_all_live_matches() -> list[dict]:
    """
    Fetches all currently live matches (IN_PLAY + PAUSED + EXTRA_TIME + PENALTIES).
    Used to power the live feed.
    """
    try:
        resp = requests.get(
            f"{BASE_URL}/matches",
            headers=_headers(),
            params={"status": "LIVE"},
            timeout=10,
        )
        resp.raise_for_status()
        matches = resp.json().get("matches", [])
    except requests.RequestException:
        return []

    return [_parse_match_row(m) for m in matches]


# ── Competition list cache ────────────────────────────────────────────────────
# Fetched once from /v4/competitions so we always query exactly what the
# API key has access to — never silently miss a competition.
_comp_codes_cache: tuple | None = None  # (timestamp, [code, ...])
_COMP_CODES_TTL = 24 * 3600

_FALLBACK_COMP_CODES = [
    "PL", "ELC", "FL1", "FL2", "BL1", "BL2", "SA", "PD", "DED", "PPL",
    "CL", "EL", "UECL", "FAC", "EFL", "CDR", "DFB", "CIT",
    "MLS", "BSA", "CLI", "CSA", "ASL",
]


def _get_competition_codes() -> list[str]:
    """
    Returns every competition code the current API key can access.
    Cached 24 h. Falls back to a known list if the request fails.
    """
    global _comp_codes_cache
    now = _time.time()
    if _comp_codes_cache:
        ts, codes = _comp_codes_cache
        if now - ts < _COMP_CODES_TTL:
            return codes
    try:
        resp = requests.get(
            f"{BASE_URL}/competitions",
            headers=_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        comps = resp.json().get("competitions", [])
        codes = [c["code"] for c in comps if c.get("code")]
        if codes:
            _comp_codes_cache = (now, codes)
            return codes
    except requests.RequestException:
        pass
    return _FALLBACK_COMP_CODES


# ── Date fixtures cache ───────────────────────────────────────────────────────
_fixtures_date_cache: dict = {}
_FIXTURES_DATE_TTL = 5 * 60  # 5 minutes — keeps live scores fresh


def _fetch_competition_for_date(code: str, date_str: str) -> tuple[list[dict], bool, bool]:
    """
    Fetch matches for one competition on one date.
    Returns (results, had_network_error, was_rate_limited).
    had_network_error=True  → request never completed (timeout/DNS/etc)
    was_rate_limited=True   → API returned 429 (quota exhausted)
    """
    try:
        resp = requests.get(
            f"{BASE_URL}/competitions/{code}/matches",
            headers=_headers(),
            params={"dateFrom": date_str, "dateTo": date_str},
            timeout=10,
        )
        if resp.status_code == 429:
            return [], False, True  # Rate limited — not "no data"
        if resp.status_code not in (200, 206):
            return [], False, False  # Expected empty (404 / 403)
        body = resp.json()
        comp_obj = body.get("competition") or {}
        area_obj = body.get("area") or {}
        results = []
        for m in body.get("matches", []):
            if "competition" not in m:
                m["competition"] = comp_obj
            if "area" not in m:
                m["area"] = area_obj
            results.append(_parse_match_row(m))
        return results, False, False
    except requests.RequestException:
        return [], True, False  # Network failure


def _get_fixtures_from_db(date_str: str) -> list[dict] | None:
    """
    Fetch fixtures for a given date from the local HistoricalFixture table.
    Returns a list in the same format as get_fixtures_by_date(), or None if
    no records exist for that date (fall through to live API).
    """
    try:
        from bets.models import HistoricalFixture
        rows = list(HistoricalFixture.objects.filter(
            match_date=date_str,
        ).values(
            "home_team", "away_team", "home_team_id", "away_team_id",
            "league_code", "league_name", "kickoff", "home_score", "away_score",
        ))
        if not rows:
            return None
        result = []
        for r in rows:
            result.append({
                "home_team":      r["home_team"],
                "away_team":      r["away_team"],
                "home_team_id":   r["home_team_id"],
                "away_team_id":   r["away_team_id"],
                "competition_code": r["league_code"],
                "competition":    r["league_name"],
                "area":           "",
                "kickoff":        r["kickoff"].isoformat() if r["kickoff"] else "",
                "status":         "FINISHED",
                "home_score":     r["home_score"],
                "away_score":     r["away_score"],
            })
        return result
    except Exception:
        return None


def get_fixtures_by_date(date_str: str) -> list[dict]:
    """
    Fetches every match on a specific date (YYYY-MM-DD) by querying each
    competition the API key has access to in parallel, then merging results.
    This is the only reliable approach — the general /v4/matches endpoint
    silently drops competitions regardless of plan tier.
    Results cached 5 minutes per date.
    """
    now = _time.time()
    cached = _fixtures_date_cache.get(date_str)
    if cached:
        ts, data = cached
        if now - ts < _FIXTURES_DATE_TTL:
            return data

    # ── Try local DB first (historical dates) ────────────────────────────────
    from datetime import date as _date_cls
    try:
        _pred_date = _date_cls.fromisoformat(date_str)
        if _pred_date <= _date_cls.today():
            db_fixtures = _get_fixtures_from_db(date_str)
            if db_fixtures is not None:
                _fixtures_date_cache[date_str] = (now, db_fixtures)
                return db_fixtures
    except Exception:
        pass

    codes = _get_competition_codes()
    all_matches: list[dict] = []
    seen_ids: set = set()
    error_count = 0
    rate_limit_count = 0
    success_count = 0

    # Fetch all competitions concurrently — advanced tier handles the load fine.
    # max_workers=12 keeps parallel requests reasonable without hammering the API.
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(_fetch_competition_for_date, code, date_str): code
                   for code in codes}
        for future in concurrent.futures.as_completed(futures):
            matches, had_error, was_rate_limited = future.result()
            if had_error:
                error_count += 1
            elif was_rate_limited:
                rate_limit_count += 1
            else:
                success_count += 1
            for m in matches:
                mid = m.get("id")
                if mid and mid not in seen_ids:
                    seen_ids.add(mid)
                    all_matches.append(m)

    # If every single request failed with a network error, raise so callers
    # can tell the difference between "API down" and "genuinely no matches".
    if error_count > 0 and success_count == 0:
        raise ConnectionError(
            f"api.football-data.org unreachable — all {error_count} requests timed out. "
            "Check your internet connection or VPN."
        )

    # If all (or most) requests were rate-limited with no successes, raise so
    # callers get a clear error instead of silently treating it as "no matches".
    if rate_limit_count > 0 and success_count == 0:
        raise ConnectionError(
            f"api.football-data.org rate limit hit — {rate_limit_count}/{len(codes)} "
            "competition requests returned 429. Wait ~60 seconds and retry."
        )

    # Partial rate-limit: some competitions returned data, some were rate-limited.
    # Don't raise — return what we have, but print a warning so the caller knows
    # results may be incomplete for the rate-limited competitions.
    if rate_limit_count > 0 and success_count > 0:
        import warnings
        warnings.warn(
            f"[Rate limit] {rate_limit_count}/{len(codes)} competition requests returned 429 "
            f"— results for those leagues may be missing. Got data from {success_count} league(s).",
            RuntimeWarning, stacklevel=2,
        )

    _fixtures_date_cache[date_str] = (now, all_matches)
    return all_matches


def get_todays_matches() -> list[dict]:
    """
    Fetches all of today's matches (any status) so the Fixtures page
    can show upcoming, live, and finished games grouped by competition.
    """
    today = datetime.utcnow().strftime("%Y-%m-%d")
    return get_fixtures_by_date(today)


def generate_match_insights(
    home_name, away_name, score_home, score_away,
    ht_home, ht_away, home_stats, away_stats,
    goals, bookings, substitutions,
    home_saves, away_saves, status
) -> list[dict]:
    """
    Generates a list of natural-language insight cards from match data.
    Each insight: {"icon": str, "title": str, "body": str}
    """
    insights = []

    def stat(d, key):
        v = d.get(key)
        if v is None: return None
        try: return float(str(v).replace("%",""))
        except: return None

    home_poss = stat(home_stats, "BALL_POSSESSION")
    away_poss = stat(away_stats, "BALL_POSSESSION")
    home_shots = stat(home_stats, "TOTAL_SHOTS") or 0
    away_shots = stat(away_stats, "TOTAL_SHOTS") or 0
    home_sot = stat(home_stats, "SHOTS_ON_GOAL") or 0
    away_sot = stat(away_stats, "SHOTS_ON_GOAL") or 0
    home_corners = stat(home_stats, "CORNER_KICKS") or 0
    away_corners = stat(away_stats, "CORNER_KICKS") or 0
    home_fouls = stat(home_stats, "FOULS") or 0
    away_fouls = stat(away_stats, "FOULS") or 0

    finished = status in ("FINISHED", "AWARDED")

    # ── Pattern of play ──────────────────────────────────────────────────────
    if home_poss is not None:
        dominant = home_name if home_poss >= 55 else (away_name if away_poss and away_poss >= 55 else None)
        if dominant:
            other = away_name if dominant == home_name else home_name
            poss_val = home_poss if dominant == home_name else away_poss
            winner = home_name if score_home > score_away else (away_name if score_away > score_home else None)
            if finished and winner and winner != dominant:
                insights.append({
                    "icon": "⚡",
                    "title": "Counter-attack masterclass",
                    "body": f"{dominant} dominated possession ({poss_val:.0f}%) but {other} won with a disciplined defensive shape and clinical counter-attacks."
                })
            else:
                insights.append({
                    "icon": "🎯",
                    "title": "Pattern of play",
                    "body": f"{dominant} controlled the game with {poss_val:.0f}% possession, dictating the tempo and pressing {other} back into their own half."
                })
        else:
            insights.append({
                "icon": "⚖️",
                "title": "Evenly contested",
                "body": f"Both sides shared possession almost equally — this was a tight, hard-fought contest with neither team imposing a clear style."
            })

    # ── Lethal attack ────────────────────────────────────────────────────────
    home_conversion = (score_home / home_sot * 100) if home_sot > 0 else 0
    away_conversion = (score_away / away_sot * 100) if away_sot > 0 else 0

    if home_sot > 0 or away_sot > 0:
        if home_conversion >= 50 and home_sot >= 2:
            insights.append({
                "icon": "🔥",
                "title": f"{home_name} clinical in front of goal",
                "body": f"Converted {score_home} of {int(home_sot)} shots on target ({home_conversion:.0f}% conversion rate). Every chance counted."
            })
        elif away_conversion >= 50 and away_sot >= 2:
            insights.append({
                "icon": "🔥",
                "title": f"{away_name} clinical in front of goal",
                "body": f"Converted {score_away} of {int(away_sot)} shots on target ({away_conversion:.0f}% conversion rate). Every chance counted."
            })
        elif home_sot >= 6 and score_home <= 1:
            insights.append({
                "icon": "😤",
                "title": f"{home_name} wasteful attack",
                "body": f"Created plenty with {int(home_sot)} shots on target but converted just {score_home}. Poor finishing cost them."
            })
        elif away_sot >= 6 and score_away <= 1:
            insights.append({
                "icon": "😤",
                "title": f"{away_name} wasteful attack",
                "body": f"Created plenty with {int(away_sot)} shots on target but converted just {score_away}. Poor finishing cost them."
            })

    # ── Goalscorers ──────────────────────────────────────────────────────────
    if goals:
        scorers = {}
        for g in goals:
            name = g.get("scorer") or "Unknown"
            scorers[name] = scorers.get(name, 0) + 1
        top_scorer = max(scorers, key=scorers.get)
        top_count = scorers[top_scorer]
        if top_count >= 2:
            insights.append({
                "icon": "⭐",
                "title": f"{top_scorer} — standout performer",
                "body": f"Scored {top_count} goals today. A match-winning individual performance that could be a turning point for the squad."
            })
        # Assist leaders
        assisters = {}
        for g in goals:
            assist = g.get("assist")
            if assist:
                assisters[assist] = assisters.get(assist, 0) + 1
        if assisters:
            top_assist = max(assisters, key=assisters.get)
            insights.append({
                "icon": "🤴",
                "title": f"{top_assist} — key creator",
                "body": f"Provided {assisters[top_assist]} assist{'s' if assisters[top_assist] > 1 else ''} today. A vital creative force linking midfield to attack."
            })

    # ── Goalkeeper ───────────────────────────────────────────────────────────
    if home_saves is not None and home_saves >= 4:
        insights.append({
            "icon": "🧤",
            "title": f"{home_name} goalkeeper saved the day",
            "body": f"Made {home_saves} saves to keep {home_name} in the game. Without those stops this result would have been very different."
        })
    elif away_saves is not None and away_saves >= 4:
        insights.append({
            "icon": "🧤",
            "title": f"{away_name} goalkeeper saved the day",
            "body": f"Made {away_saves} saves — a commanding display between the posts that kept {away_name} alive."
        })

    # ── Set piece threat ─────────────────────────────────────────────────────
    if home_corners >= 8 or away_corners >= 8:
        corner_team = home_name if home_corners >= away_corners else away_name
        corner_count = max(home_corners, away_corners)
        insights.append({
            "icon": "📐",
            "title": f"{corner_team} — set piece dominance",
            "body": f"Earned {int(corner_count)} corners, creating constant danger from dead-ball situations. Their delivery and aerial threat was a consistent problem."
        })

    # ── Discipline ───────────────────────────────────────────────────────────
    reds = [b for b in bookings if b.get("card") in ("RED_CARD","YELLOW_RED_CARD")]
    if reds:
        red_names = [f"{b.get('player')} ({b.get('team')})" for b in reds]
        insights.append({
            "icon": "🟥",
            "title": "Red card changes the game",
            "body": f"{', '.join(red_names)} saw red. Playing with a numerical disadvantage reshuffled the tactical battle entirely."
        })

    total_fouls = (home_fouls or 0) + (away_fouls or 0)
    if total_fouls >= 28:
        rougher = home_name if home_fouls >= away_fouls else away_name
        insights.append({
            "icon": "⚠️",
            "title": "Physical, feisty contest",
            "body": f"{int(total_fouls)} fouls committed across 90 minutes. {rougher} in particular used physicality as a tactical tool to disrupt play."
        })

    # ── Second half surge ────────────────────────────────────────────────────
    if ht_home is not None and ht_away is not None and finished:
        sh_home = score_home - ht_home
        sh_away = score_away - ht_away
        if sh_home >= 2:
            insights.append({
                "icon": "📈",
                "title": f"{home_name} dominant second half",
                "body": f"Scored {sh_home} goals after the break having gone in {ht_home}-{ht_away} at half time. A superb tactical response from the manager."
            })
        elif sh_away >= 2:
            insights.append({
                "icon": "📈",
                "title": f"{away_name} dominant second half",
                "body": f"Scored {sh_away} goals after the break having gone in {ht_home}-{ht_away} at half time. A superb tactical response from the manager."
            })

    # ── Late subs chasing ────────────────────────────────────────────────────
    late_subs = [s for s in substitutions if (s.get("minute") or 0) >= 70]
    if late_subs:
        teams_chasing = set(s["team"] for s in late_subs)
        if len(teams_chasing) == 1:
            team = list(teams_chasing)[0]
            insights.append({
                "icon": "🔄",
                "title": f"{team} chasing the game",
                "body": f"Made {len(late_subs)} substitutions in the final 20 minutes, throwing on fresh legs in search of a decisive moment."
            })

    return insights


def get_match_detail(fixture_id: int) -> dict | None:
    try:
        resp = requests.get(
            f"{BASE_URL}/matches/{fixture_id}",
            headers={
                **_headers(),
                "X-Unfold-Goals": "true",
                "X-Unfold-Bookings": "true",
                "X-Unfold-Subs": "true",
                "X-Unfold-Lineups": "true",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        return None

    score = data.get("score", {})
    ft = score.get("fullTime", {})
    ht = score.get("halfTime", {})
    home = data.get("homeTeam", {})
    away = data.get("awayTeam", {})

    def parse_stats(team_data, group_label=None, top_level_stats=None):
        """
        Parse stats from team object OR from top-level statistics array.
        football-data.org returns stats in two possible places depending on
        the competition / package level.
        """
        # Primary: stats nested under homeTeam / awayTeam
        raw = team_data.get("statistics", [])
        if isinstance(raw, list) and raw:
            result = {}
            for s in raw:
                if isinstance(s, dict) and s.get("type"):
                    result[s["type"]] = s.get("value")
            if result:
                return result

        # Fallback: top-level "statistics" array grouped by HOME / AWAY
        if top_level_stats and group_label:
            result = {}
            for s in top_level_stats:
                if not isinstance(s, dict):
                    continue
                if s.get("group", "").upper() == group_label:
                    key = (s.get("type") or s.get("name", "")).upper().replace(" ", "_")
                    result[key] = s.get("value")
            return result

        return {}

    top_stats = data.get("statistics", []) if isinstance(data.get("statistics"), list) else []
    home_stats = parse_stats(home, "HOME", top_stats)
    away_stats = parse_stats(away, "AWAY", top_stats)

    def stat(d, *keys):
        """Try multiple stat key names; return float or None."""
        for key in keys:
            v = d.get(key)
            if v is not None:
                try:
                    return float(str(v).replace("%", ""))
                except (ValueError, TypeError):
                    continue
        return None

    goals = []
    for g in data.get("goals", []):
        goals.append({
            "minute": g.get("minute"),
            "team": g.get("team", {}).get("name"),
            "scorer": g.get("scorer", {}).get("name"),
            "assist": (g.get("assist") or {}).get("name"),
            "type": g.get("type"),  # REGULAR, OWN_GOAL, PENALTY
        })

    bookings = []
    for b in data.get("bookings", []):
        bookings.append({
            "minute": b.get("minute"),
            "team": b.get("team", {}).get("name"),
            "player": b.get("player", {}).get("name"),
            "card": b.get("card"),
        })

    substitutions = []
    for s in data.get("substitutions", []):
        substitutions.append({
            "minute": s.get("minute"),
            "team": s.get("team", {}).get("name"),
            "player_out": s.get("playerOut", {}).get("name"),
            "player_in": s.get("playerIn", {}).get("name"),
        })

    def parse_lineup(team_data):
        return [
            {"name": p.get("name"), "position": p.get("position"), "shirt": p.get("shirtNumber")}
            for p in team_data.get("lineup", [])
        ]

    home_goals_scored = ft.get("home") or 0
    away_goals_scored = ft.get("away") or 0

    # Shots on target (multiple possible key names used by the API)
    home_shots_ot = stat(home_stats, "SHOTS_ON_GOAL", "ON_TARGET", "SHOTS_ON_TARGET")
    away_shots_ot = stat(away_stats, "SHOTS_ON_GOAL", "ON_TARGET", "SHOTS_ON_TARGET")

    # Goalkeeper saves — prefer the direct stat; fall back to shots-on-target minus goals
    home_saves = stat(home_stats, "GOALKEEPER_SAVES", "SAVES")
    away_saves = stat(away_stats, "GOALKEEPER_SAVES", "SAVES")
    if home_saves is None and away_saves is None:
        # Only calculate when we actually have shot data, otherwise leave as None
        if away_shots_ot is not None or home_shots_ot is not None:
            _away_sot = int(away_shots_ot or 0)
            _home_sot = int(home_shots_ot or 0)
            home_saves = max(0, _away_sot - int(away_goals_scored))
            away_saves = max(0, _home_sot - int(home_goals_scored))

    # ── Understat xG (supported leagues only) ──────────────────────────────
    competition_code = data.get("competition", {}).get("code", "")
    match_date = (data.get("utcDate") or "")[:10]
    xg_data = get_match_xg(
        home_team=home.get("name", ""),
        away_team=away.get("name", ""),
        match_date=match_date,
        competition_code=competition_code,
    )

    # Combine standard insights with xG insights
    standard_insights = generate_match_insights(
        home_name=home.get("name",""), away_name=away.get("name",""),
        score_home=ft.get("home") or 0, score_away=ft.get("away") or 0,
        ht_home=ht.get("home"), ht_away=ht.get("away"),
        home_stats=home_stats, away_stats=away_stats,
        goals=goals, bookings=bookings, substitutions=substitutions,
        home_saves=home_saves, away_saves=away_saves,
        status=data.get("status",""),
    )
    xg_insights = generate_xg_insights(home.get("name",""), away.get("name",""), xg_data) if xg_data else []
    all_insights = xg_insights + standard_insights  # xG insights first

    return {
        "fixture_id": fixture_id,
        "status": data.get("status"),
        "minute": data.get("minute"),
        "competition": data.get("competition", {}).get("name"),
        "area": data.get("area", {}).get("name"),
        "kickoff": data.get("utcDate"),
        "home_team": home.get("name"),
        "home_short": home.get("shortName") or home.get("name"),
        "home_formation": home.get("formation"),
        "home_lineup": parse_lineup(home),
        "away_team": away.get("name"),
        "away_short": away.get("shortName") or away.get("name"),
        "away_formation": away.get("formation"),
        "away_lineup": parse_lineup(away),
        "score_home": ft.get("home"),
        "score_away": ft.get("away"),
        "ht_home": ht.get("home"),
        "ht_away": ht.get("away"),
        "home_saves": home_saves,
        "away_saves": away_saves,
        "stats": {
            "possession_home": stat(home_stats, "BALL_POSSESSION", "POSSESSION"),
            "possession_away": stat(away_stats, "BALL_POSSESSION", "POSSESSION"),
            "shots_total_home": stat(home_stats, "TOTAL_SHOTS", "SHOTS_TOTAL", "SHOTS"),
            "shots_total_away": stat(away_stats, "TOTAL_SHOTS", "SHOTS_TOTAL", "SHOTS"),
            "shots_on_target_home": home_shots_ot,
            "shots_on_target_away": away_shots_ot,
            "corners_home": stat(home_stats, "CORNER_KICKS", "CORNERS"),
            "corners_away": stat(away_stats, "CORNER_KICKS", "CORNERS"),
            "fouls_home": stat(home_stats, "FOULS", "TOTAL_FOULS"),
            "fouls_away": stat(away_stats, "FOULS", "TOTAL_FOULS"),
            "offsides_home": stat(home_stats, "OFFSIDES"),
            "offsides_away": stat(away_stats, "OFFSIDES"),
            "yellow_home": stat(home_stats, "YELLOW_CARDS"),
            "yellow_away": stat(away_stats, "YELLOW_CARDS"),
            "saves_home": home_saves,
            "saves_away": away_saves,
            # xG from Understat
            "xg_home": xg_data["home"]["total_xg"] if xg_data else None,
            "xg_away": xg_data["away"]["total_xg"] if xg_data else None,
            "big_chances_home": xg_data["home"]["big_chances"] if xg_data else None,
            "big_chances_away": xg_data["away"]["big_chances"] if xg_data else None,
            "crosses_to_shot_home": xg_data["home"]["crosses_to_shot"] if xg_data else None,
            "crosses_to_shot_away": xg_data["away"]["crosses_to_shot"] if xg_data else None,
            "through_balls_home": xg_data["home"]["through_balls"] if xg_data else None,
            "through_balls_away": xg_data["away"]["through_balls"] if xg_data else None,
        },
        "goals": goals,
        "bookings": bookings,
        "substitutions": substitutions,
        "xg_data": xg_data,
        "insights": all_insights,
    }


def get_match_preview(fixture_id: int) -> dict | None:
    """
    Fetches head-to-head history, recent team form, and lineups (when available)
    for an upcoming fixture. Makes 3 API calls: fixture + H2H + home-form + away-form.
    """
    # ── 1. Fixture info ─────────────────────────────────────────────────────
    try:
        resp = requests.get(
            f"{BASE_URL}/matches/{fixture_id}",
            headers={**_headers(), "X-Unfold-Lineups": "true"},
            timeout=10,
        )
        resp.raise_for_status()
        fixture = resp.json()
    except requests.RequestException:
        return None

    home = fixture.get("homeTeam", {})
    away = fixture.get("awayTeam", {})
    home_id = home.get("id")
    away_id = away.get("id")

    def parse_lineup(team_data):
        return [
            {"name": p.get("name"), "position": p.get("position"), "shirt": p.get("shirtNumber")}
            for p in team_data.get("lineup", [])
        ]

    # ── 2. Head-to-head ─────────────────────────────────────────────────────
    h2h_matches = []
    h2h_summary = {}
    try:
        h2h_resp = requests.get(
            f"{BASE_URL}/matches/{fixture_id}/head2head",
            headers=_headers(),
            params={"limit": 8},
            timeout=10,
        )
        h2h_resp.raise_for_status()
        h2h_data = h2h_resp.json()

        rs = h2h_data.get("resultSet", {})
        home_h2h = h2h_data.get("homeTeam", {})
        away_h2h = h2h_data.get("awayTeam", {})
        h2h_summary = {
            "played": rs.get("played", 0),
            "home_wins": home_h2h.get("wins", 0),
            "draws": rs.get("draws", 0),
            "away_wins": away_h2h.get("wins", 0),
        }

        for m in h2h_data.get("matches", []):
            ft = m.get("score", {}).get("fullTime", {})
            mh = m.get("homeTeam", {}).get("name", "")
            ma = m.get("awayTeam", {}).get("name", "")
            h2h_matches.append({
                "date": (m.get("utcDate") or "")[:10],
                "home_team": m.get("homeTeam", {}).get("shortName") or mh,
                "away_team": m.get("awayTeam", {}).get("shortName") or ma,
                "home_score": ft.get("home"),
                "away_score": ft.get("away"),
                "competition": m.get("competition", {}).get("name", ""),
            })
    except requests.RequestException:
        pass

    # ── 3. Recent form (last 5 finished for each team) ──────────────────────
    def get_form(team_id):
        if not team_id:
            return []
        try:
            r = requests.get(
                f"{BASE_URL}/teams/{team_id}/matches",
                headers=_headers(),
                params={"status": "FINISHED", "limit": 6},
                timeout=10,
            )
            r.raise_for_status()
            recent = r.json().get("matches", [])
        except requests.RequestException:
            return []

        form = []
        for m in recent[-5:]:
            ft = m.get("score", {}).get("fullTime", {})
            h_score = ft.get("home") or 0
            a_score = ft.get("away") or 0
            is_home = m.get("homeTeam", {}).get("id") == team_id
            gf = h_score if is_home else a_score
            ga = a_score if is_home else h_score
            opp_data = m.get("awayTeam" if is_home else "homeTeam", {})
            opp = opp_data.get("shortName") or opp_data.get("name", "?")
            if gf > ga:
                result = "W"
            elif gf == ga:
                result = "D"
            else:
                result = "L"
            form.append({
                "result": result,
                "score": f"{gf}–{ga}",
                "opponent": opp,
                "venue": "H" if is_home else "A",
                "date": (m.get("utcDate") or "")[:10],
            })
        return form

    home_form = get_form(home_id)
    away_form = get_form(away_id)

    return {
        "fixture_id": fixture_id,
        "status": fixture.get("status"),
        "kickoff": fixture.get("utcDate"),
        "competition": fixture.get("competition", {}).get("name"),
        "home_team": home.get("name"),
        "home_short": home.get("shortName") or home.get("name"),
        "home_formation": home.get("formation"),
        "home_lineup": parse_lineup(home),
        "away_team": away.get("name"),
        "away_short": away.get("shortName") or away.get("name"),
        "away_formation": away.get("formation"),
        "away_lineup": parse_lineup(away),
        "h2h": {**h2h_summary, "matches": h2h_matches},
        "home_form": home_form,
        "away_form": away_form,
    }


# ── Standings + team context cache ───────────────────────────────────────────
# comp_code → (timestamp, {team_id: standing_dict})
_standings_cache: dict = {}
_STANDINGS_TTL = 24 * 3600   # standings change once per gameweek

# ── xG league discount factors ────────────────────────────────────────────────
# Lower-scoring / more defensive leagues where API-Football xG overestimates
# actual goal output vs the Big 5 baseline the model was calibrated on.
# Derived from loss analysis: SSL 31% loss rate, BL2 25%, DED/BJL ~15%.
_XG_LEAGUE_FACTORS: dict[str, float] = {
    # Calibrated from 2025/26 season actual results (250+ games per league)
    # Factor < 1.0 means API-Football xG overestimates and we discount it.
    # Factor > 1.0 means the league is higher-scoring than API-Football xG suggests.
    "BJL": 0.90,  # Belgian Jupiler — 46.9% Over 2.5, 2.62 avg goals — genuinely low-scoring
    "SA":  0.95,  # Serie A — 46.7% Over 2.5, 2.44 avg goals — Big 5 lowest scorer
    "BL2": 0.95,  # 2. Bundesliga — 59.3% Over 2.5, below BL1 pace
    "ABL": 0.93,  # Austrian Bundesliga — similar defensive profile to BL2
    # Leagues previously discounted but data shows they ARE high-scoring — removed:
    # "SSL" removed: 66.7% Over 2.5, 3.27 avg goals — highest in dataset, no discount warranted
    # "DED" removed: 62.7% Over 2.5, 3.20 avg goals — Dutch football is genuinely high-scoring
}

# team_id → (timestamp, [result_str, ...])   "W"/"D"/"L" most-recent first
_form_cache: dict = {}
_FORM_TTL = 6 * 3600


def _get_standings(competition_code: str) -> dict:
    """
    Returns {team_id: {position, total_teams, points, played, won, drawn, lost,
                       goals_for, goals_against, gap_to_safety, gap_to_top4, gap_to_title}}
    Cached 24 h.
    """
    now = _time.time()
    cached = _standings_cache.get(competition_code)
    if cached:
        ts, data = cached
        if now - ts < _STANDINGS_TTL:
            return data

    try:
        resp = requests.get(
            f"{BASE_URL}/competitions/{competition_code}/standings",
            headers=_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException:
        return {}

    # Parse all three table types
    tables = body.get("standings", [])
    total_table = next((t for t in tables if t.get("type") == "TOTAL"), None)
    home_table  = next((t for t in tables if t.get("type") == "HOME"),  None)
    away_table  = next((t for t in tables if t.get("type") == "AWAY"),  None)
    if not total_table:
        total_table = tables[0] if tables else None
    if not total_table:
        return {}

    rows = total_table.get("table", [])
    total_teams = len(rows)
    relegation_cutoff = total_teams - 3  # bottom 3 go down (most leagues)

    # Find points of key positions
    title_points  = rows[0]["points"]           if rows else 0
    top4_points   = rows[3]["points"]           if len(rows) >= 4 else 0
    safety_points = rows[relegation_cutoff]["points"] if len(rows) > relegation_cutoff else 0

    # Index home/away rows by team_id for O(1) lookup
    def _idx(table):
        if not table:
            return {}
        return {r.get("team", {}).get("id"): r for r in table.get("table", [])}

    home_rows = _idx(home_table)
    away_rows = _idx(away_table)

    def _venue_record(row):
        if not row:
            return {}
        played = row.get("playedGames", 0)
        gf     = row.get("goalsFor", 0)
        ga     = row.get("goalsAgainst", 0)
        won    = row.get("won", 0)
        return {
            "played":       played,
            "won":          won,
            "drawn":        row.get("draw", 0),
            "lost":         row.get("lost", 0),
            "goals_for":    gf,
            "goals_against":ga,
            "goal_diff":    row.get("goalDifference", 0),
            "points":       row.get("points", 0),
            "win_rate":     round(won / played, 3) if played else 0.0,
            "goals_for_pg": round(gf / played, 2)  if played else 0.0,
            "goals_against_pg": round(ga / played, 2) if played else 0.0,
        }

    result = {}
    for row in rows:
        team_id = row.get("team", {}).get("id")
        if not team_id:
            continue
        pts = row["points"]
        pos = row["position"]
        result[team_id] = {
            "position":       pos,
            "total_teams":    total_teams,
            "points":         pts,
            "played":         row.get("playedGames", 0),
            "won":            row.get("won", 0),
            "drawn":          row.get("draw", 0),
            "lost":           row.get("lost", 0),
            "goals_for":      row.get("goalsFor", 0),
            "goals_against":  row.get("goalsAgainst", 0),
            "goal_diff":      row.get("goalDifference", 0),
            "gap_to_title":   title_points  - pts,
            "gap_to_top4":    top4_points   - pts,
            "gap_to_safety":  pts - safety_points,   # positive = safe, negative = in danger
            "relegation_zone": pos > relegation_cutoff,
            # Venue-split records
            "home": _venue_record(home_rows.get(team_id)),
            "away": _venue_record(away_rows.get(team_id)),
        }

    _standings_cache[competition_code] = (now, result)
    return result


def _get_team_recent_results(team_id: int, limit: int = 10) -> list[str]:
    """
    Returns the last `limit` results for a team as ["W","D","L",...] most-recent first.
    Cached 6 h.
    """
    import time as _time
    now = _time.time()
    cache_key = f"{team_id}_{limit}"
    cached = _form_cache.get(cache_key)
    if cached:
        ts, data = cached
        if now - ts < _FORM_TTL:
            return data

    # Try DB first
    try:
        from bets.models import HistoricalFixture
        from django.db.models import Q
        db_matches = list(HistoricalFixture.objects.filter(
            Q(home_team_id=team_id) | Q(away_team_id=team_id),
            home_score__isnull=False,
        ).order_by("-match_date").values(
            "home_team_id", "home_score", "away_score"
        )[:limit])
        if db_matches:
            results = []
            for m in db_matches:
                is_home = m["home_team_id"] == team_id
                gf = m["home_score"] if is_home else m["away_score"]
                ga = m["away_score"] if is_home else m["home_score"]
                results.append("W" if gf > ga else ("D" if gf == ga else "L"))
            _form_cache[cache_key] = (now, results)
            return results
    except Exception:
        pass

    try:
        resp = requests.get(
            f"{BASE_URL}/teams/{team_id}/matches",
            headers=_headers(),
            params={"status": "FINISHED", "limit": limit},
            timeout=10,
        )
        resp.raise_for_status()
        matches = resp.json().get("matches", [])
    except requests.RequestException:
        _form_cache[cache_key] = (now, [])
        return []

    results = []
    for m in reversed(matches):   # API returns oldest first; reverse → newest first
        home_id  = m.get("homeTeam", {}).get("id")
        ft       = m.get("score", {}).get("fullTime", {})
        h_goals  = ft.get("home") or 0
        a_goals  = ft.get("away") or 0
        if home_id == team_id:
            results.append("W" if h_goals > a_goals else ("D" if h_goals == a_goals else "L"))
        else:
            results.append("W" if a_goals > h_goals else ("D" if h_goals == a_goals else "L"))

    _form_cache[cache_key] = (now, results)
    return results


def get_team_context(team_id: int, competition_code: str, match_date: str | None = None) -> dict:
    """
    Returns full context for a team:
      - League standings position, points, gaps
      - Winning/losing streak
      - Motivation tier
    Used by the player assessment engine.
    Pass match_date to use DB standings (no API call) for historical dates.
    """
    if match_date:
        standings = _get_league_standings_from_db(competition_code, 2025, match_date)
    else:
        standings = _get_standings(competition_code)
    standing  = standings.get(team_id, {})

    recent    = _get_team_recent_results(team_id, limit=20)
    results10 = recent[:10]

    # ── Streak calculation ────────────────────────────────────────────────────
    streak_type, streak_count = None, 0
    if recent:
        streak_type = recent[0]
        for r in recent:
            if r == streak_type:
                streak_count += 1
            else:
                break

    # ── Motivation tier ───────────────────────────────────────────────────────
    pos           = standing.get("position")
    total         = standing.get("total_teams", 20)
    gap_safety    = standing.get("gap_to_safety", 10)    # positive = safe
    gap_top4      = standing.get("gap_to_top4", 0)
    gap_title     = standing.get("gap_to_title", 0)
    in_relegation = standing.get("relegation_zone", False)

    if in_relegation or gap_safety <= 1:
        motivation = "relegation_fight"
        motivation_label = "Fighting relegation — every point vital"
        motivation_factor = 1.28
    elif gap_safety <= 4:
        motivation = "relegation_threat"
        motivation_label = "Under relegation threat — danger zone"
        motivation_factor = 1.15
    elif gap_top4 <= 2 and pos and pos <= 6:
        motivation = "top4_race"
        motivation_label = "Top 4 race — Champions League on the line"
        motivation_factor = 1.12
    elif gap_title <= 3 and pos and pos <= 3:
        motivation = "title_race"
        motivation_label = "Title race — every match decisive"
        motivation_factor = 1.18
    elif pos and 6 <= pos <= (total - 5) and gap_safety > 8 and gap_top4 > 8:
        motivation = "nothing_to_play_for"
        motivation_label = "Mid-table comfort — limited pressure"
        motivation_factor = 0.88
    else:
        motivation = "standard"
        motivation_label = "Standard motivation"
        motivation_factor = 1.0

    wins_last10  = results10.count("W")
    draws_last10 = results10.count("D")
    losses_last10 = results10.count("L")

    return {
        "position":         pos,
        "total_teams":      total,
        "points":           standing.get("points"),
        "played":           standing.get("played"),
        "goals_for":        standing.get("goals_for"),
        "goals_against":    standing.get("goals_against"),
        "goal_diff":        standing.get("goal_diff"),
        "gap_to_safety":    gap_safety,
        "gap_to_top4":      gap_top4,
        "gap_to_title":     gap_title,
        "relegation_zone":  in_relegation,
        "streak_type":      streak_type,
        "streak_count":     streak_count,
        "streak_label":     (
            f"{streak_count}-game {'winning' if streak_type == 'W' else 'losing' if streak_type == 'L' else 'unbeaten'} streak"
            if streak_count >= 2 else ""
        ),
        "last_10":          results10,
        "wins_last10":      wins_last10,
        "losses_last10":    losses_last10,
        "form_string":      "".join(results10),
        "motivation":       motivation,
        "motivation_label": motivation_label,
        "motivation_factor":motivation_factor,
    }


def get_fixture_lineups(fixture_id: int) -> dict | None:
    """
    Returns lineup data for the player assessment engine.
    Includes home/away lineups + bench, team names, competition code, match date.
    For finished matches also fetches Understat xG data for GK scoring.
    """
    try:
        resp = requests.get(
            f"{BASE_URL}/matches/{fixture_id}",
            headers={**_headers(), "X-Unfold-Lineups": "true"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        return None

    home = data.get("homeTeam", {})
    away = data.get("awayTeam", {})

    def _players(team_data, key):
        return [
            {"name": p.get("name"), "position": p.get("position"), "shirt": p.get("shirtNumber")}
            for p in team_data.get(key, [])
            if p.get("position")
        ]

    match_date    = (data.get("utcDate") or "")[:10]
    comp_code     = data.get("competition", {}).get("code", "")
    match_status  = data.get("status", "")

    home_id   = home.get("id")
    away_id   = away.get("id")

    result = {
        "home_team":         home.get("name", ""),
        "away_team":         away.get("name", ""),
        "home_id":           home_id,
        "away_id":           away_id,
        "home_lineup":       _players(home, "lineup"),
        "away_lineup":       _players(away, "lineup"),
        "home_bench":        _players(home, "bench"),
        "away_bench":        _players(away, "bench"),
        "home_formation":    home.get("formation"),
        "away_formation":    away.get("formation"),
        "competition_code":  comp_code,
        "match_date":        match_date,
        "match_status":      match_status,
        "xg_data":           None,
        "home_context":      None,
        "away_context":      None,
    }

    # Enrich with team context (standings + streak + motivation)
    if comp_code and home_id:
        try:
            result["home_context"] = get_team_context(home_id, comp_code)
        except Exception:
            pass
    if comp_code and away_id:
        try:
            result["away_context"] = get_team_context(away_id, comp_code)
        except Exception:
            pass

    # For finished matches, enrich GK scoring with Understat xG
    if match_status in ("FINISHED",) and comp_code:
        try:
            xg = get_match_xg(
                home_team=home.get("name", ""),
                away_team=away.get("name", ""),
                match_date=match_date,
                competition_code=comp_code,
            )
            result["xg_data"] = xg
        except Exception:
            pass

    return result


# ── Yellow card history cache ─────────────────────────────────────────────────
_cards_cache: dict = {}
_CARDS_TTL = 12 * 3600   # 12 h


def _get_team_yellow_cards_per_game(team_id: int, limit: int = 15) -> float | None:
    """
    Returns average yellow cards per game for a team from their last `limit` matches.
    Uses a two-step fetch:
      1. GET /teams/{id}/matches → collect match IDs
      2. GET /matches?ids=...&X-Unfold-Bookings=true → count yellow cards per match
    Cached 12 h.
    """
    import time as _time
    now = _time.time()
    cached = _cards_cache.get(team_id)
    if cached:
        ts, val = cached
        if now - ts < _CARDS_TTL:
            return val

    # Step 1: recent match IDs
    try:
        resp = requests.get(
            f"{BASE_URL}/teams/{team_id}/matches",
            headers=_headers(),
            params={"status": "FINISHED", "limit": limit},
            timeout=10,
        )
        resp.raise_for_status()
        matches = resp.json().get("matches", [])
    except requests.RequestException:
        _cards_cache[team_id] = (now, None)
        return None

    if not matches:
        _cards_cache[team_id] = (now, None)
        return None

    # Build a name→team_id map from these matches so we can filter bookings by team
    team_names: set[str] = set()
    for m in matches:
        ht = m.get("homeTeam", {})
        at = m.get("awayTeam", {})
        if ht.get("id") == team_id:
            team_names.add(ht.get("name", ""))
        elif at.get("id") == team_id:
            team_names.add(at.get("name", ""))

    match_ids = [str(m["id"]) for m in matches if m.get("id")]

    # Step 2: batch fetch with bookings unfolded
    try:
        batch = requests.get(
            f"{BASE_URL}/matches",
            headers={**_headers(), "X-Unfold-Bookings": "true"},
            params={"ids": ",".join(match_ids)},
            timeout=15,
        )
        batch.raise_for_status()
        batch_matches = batch.json().get("matches", [])
    except requests.RequestException:
        _cards_cache[team_id] = (now, None)
        return None

    total_yellows = 0
    games_counted = 0

    for m in batch_matches:
        # Identify this team's name for this match
        h_data = m.get("homeTeam", {})
        a_data = m.get("awayTeam", {})
        if h_data.get("id") == team_id:
            this_team_name = h_data.get("name", "")
        elif a_data.get("id") == team_id:
            this_team_name = a_data.get("name", "")
        else:
            # Fall back to any known name
            this_team_name = next(iter(team_names), "")

        yellows = sum(
            1 for b in (m.get("bookings") or [])
            if b.get("card") == "YELLOW_CARD"
            and b.get("team", {}).get("name") == this_team_name
        )
        total_yellows += yellows
        games_counted += 1

    if games_counted == 0:
        _cards_cache[team_id] = (now, None)
        return None

    avg = round(total_yellows / games_counted, 2)
    _cards_cache[team_id] = (now, avg)
    return avg


# ── H2H scoring check ────────────────────────────────────────────────────────
_h2h_cache: dict = {}
_H2H_TTL = 12 * 3600


def get_h2h_scored(home_team_id: int, away_team_id: int) -> dict:
    """
    Fetches the last 5 H2H meetings between these two teams.
    Returns:
      {
        "home_scored_last_h2h": bool,    # did home team score in most recent H2H?
        "home_blanked_h2h": int,         # how many of last 5 H2H did home team fail to score?
        "away_scored_last_h2h": bool,
        "away_blanked_h2h": int,
        "h2h_avg_goals": float | None,   # average total goals across last 5 H2H
        "h2h_low_scoring_count": int,    # number of H2H games with ≤1 total goal
        "h2h_count": int,                # how many H2H games found
        "last_h2h_date": str,
        "last_h2h_score": str,           # e.g. "0-1"
      }
    """
    import time as _t
    now = _t.time()
    key = (min(home_team_id, away_team_id), max(home_team_id, away_team_id))
    cached = _h2h_cache.get(key)
    if cached:
        ts, data = cached
        if now - ts < _H2H_TTL:
            return data

    empty = {
        "home_scored_last_h2h": True,
        "home_blanked_h2h": 0,
        "away_scored_last_h2h": True,
        "away_blanked_h2h": 0,
        "h2h_avg_goals": None,
        "h2h_low_scoring_count": 0,
        "h2h_count": 0,
        "last_h2h_date": None,
        "last_h2h_score": None,
    }

    if not home_team_id or not away_team_id:
        return empty

    # Try DB first — avoids a live API call per fixture pair (major speed-up for backtesting)
    try:
        from bets.models import HistoricalFixture
        from django.db.models import Q
        db_h2h = list(HistoricalFixture.objects.filter(
            Q(home_team_id=home_team_id, away_team_id=away_team_id) |
            Q(home_team_id=away_team_id, away_team_id=home_team_id),
            home_score__isnull=False,
        ).order_by("-match_date").values(
            "home_team_id", "home_score", "away_score", "match_date"
        )[:5])
        if db_h2h:
            home_blanks = away_blanks = 0
            total_goals_list = []
            last_date = last_score = None
            for i, m in enumerate(db_h2h):
                is_home = m["home_team_id"] == home_team_id
                hg = m["home_score"] if is_home else m["away_score"]
                ag = m["away_score"] if is_home else m["home_score"]
                total_goals_list.append(hg + ag)
                if hg == 0: home_blanks += 1
                if ag == 0: away_blanks += 1
                if i == 0:
                    last_date = str(m["match_date"])
                    last_score = f"{hg}-{ag}"
            m0 = db_h2h[0]
            is_home0 = m0["home_team_id"] == home_team_id
            h_last = m0["home_score"] if is_home0 else m0["away_score"]
            n = len(db_h2h)
            result = {
                "home_scored_last_h2h": h_last > 0,
                "home_blanked_h2h": home_blanks,
                "away_scored_last_h2h": True,
                "away_blanked_h2h": away_blanks,
                "h2h_avg_goals": round(sum(total_goals_list) / n, 2),
                "h2h_low_scoring_count": sum(1 for g in total_goals_list if g <= 1),
                "h2h_count": n,
                "last_h2h_date": last_date,
                "last_h2h_score": last_score,
            }
            _h2h_cache[key] = (now, result)
            return result
    except Exception:
        pass

    try:
        # Fetch more matches and filter client-side to get up to 5 true H2H meetings
        resp = requests.get(
            f"{BASE_URL}/matches",
            headers=_headers(),
            params={"team": home_team_id, "limit": 20, "status": "FINISHED"},
            timeout=10,
        )
        resp.raise_for_status()
        matches = [
            m for m in resp.json().get("matches", [])
            if {m.get("homeTeam", {}).get("id"), m.get("awayTeam", {}).get("id")}
               == {home_team_id, away_team_id}
        ][:5]  # take last 5 H2H meetings
    except requests.RequestException:
        _h2h_cache[key] = (now, empty)
        return empty

    if not matches:
        _h2h_cache[key] = (now, empty)
        return empty

    home_blanks = away_blanks = 0
    last_date = last_score = None
    total_goals_list = []

    for i, m in enumerate(matches):
        ft = m.get("score", {}).get("fullTime", {})
        h_id = m.get("homeTeam", {}).get("id")
        hg = ft.get("home", 0) or 0
        ag = ft.get("away", 0) or 0
        home_gf = hg if h_id == home_team_id else ag
        away_gf = ag if h_id == home_team_id else hg
        total = home_gf + away_gf

        total_goals_list.append(total)
        if home_gf == 0:
            home_blanks += 1
        if away_gf == 0:
            away_blanks += 1

        if i == 0:
            last_date = m.get("utcDate", "")[:10]
            last_score = f"{home_gf}-{away_gf}"

    n = len(matches)
    h2h_avg = round(sum(total_goals_list) / n, 2) if n else None
    low_scoring = sum(1 for g in total_goals_list if g <= 1)

    ft0 = matches[0].get("score", {}).get("fullTime", {})
    h_id0 = matches[0].get("homeTeam", {}).get("id")
    home_scored_last = (ft0.get("home", 0) or 0) > 0 if h_id0 == home_team_id else (ft0.get("away", 0) or 0) > 0

    result = {
        "home_scored_last_h2h": home_scored_last,
        "home_blanked_h2h": home_blanks,
        "away_scored_last_h2h": True,
        "away_blanked_h2h": away_blanks,
        "h2h_avg_goals": h2h_avg,
        "h2h_low_scoring_count": low_scoring,
        "h2h_count": n,
        "last_h2h_date": last_date,
        "last_h2h_score": last_score,
    }

    _h2h_cache[key] = (now, result)
    return result


# ── Team match-history rates ──────────────────────────────────────────────────
_rates_cache: dict = {}
_RATES_TTL = 6 * 3600


def _get_team_match_rates(team_id: int, limit: int = 20) -> dict:
    """
    Fetches the team's last `limit` FINISHED matches and computes:
      btts_rate, over15_rate, over25_rate, over35_rate,
      clean_sheet_rate, scored_per_game, missed_per_game,
      home_cs_rate, away_cs_rate, home_scored_pg, away_scored_pg,
      xg_per_game (proxy = goals scored pg, adjusted by shot conversion estimates),
      xga_per_game (proxy = goals conceded pg)
    Returns {} on failure.
    """
    import time as _t
    now = _t.time()
    if not team_id:
        return {}
    cached = _rates_cache.get(team_id)
    if cached:
        ts, data = cached
        if now - ts < _RATES_TTL:
            return data

    try:
        resp = requests.get(
            f"{BASE_URL}/teams/{team_id}/matches",
            headers=_headers(),
            params={"status": "FINISHED", "limit": limit},
            timeout=10,
        )
        resp.raise_for_status()
        matches = resp.json().get("matches", [])
    except requests.RequestException:
        return {}

    if not matches:
        return {}

    btts = over15 = over25 = over35 = cs = 0
    h_cs = h_games = h_blanks = 0
    a_cs = a_games = 0
    h_scored = h_conceded = 0
    a_scored = a_conceded = 0
    total_gf = total_ga = 0

    # Track goals scored/conceded per game in order (most-recent first from API)
    gf_per_game = []
    ga_per_game = []   # goals conceded (clean sheet = 0)

    for m in matches:
        ft = m.get("score", {}).get("fullTime", {})
        home_goals = ft.get("home")
        away_goals = ft.get("away")
        if home_goals is None or away_goals is None:
            continue

        is_home = m.get("homeTeam", {}).get("id") == team_id
        gf = home_goals if is_home else away_goals
        ga = away_goals if is_home else home_goals
        total_goals = home_goals + away_goals

        gf_per_game.append(gf)
        ga_per_game.append(ga)
        total_gf += gf
        total_ga += ga

        if gf > 0 and ga > 0:
            btts += 1
        if total_goals >= 2:
            over15 += 1
        if total_goals >= 3:
            over25 += 1
        if total_goals >= 4:
            over35 += 1
        if ga == 0:
            cs += 1

        if is_home:
            h_games += 1
            h_scored += gf
            h_conceded += ga
            if ga == 0:
                h_cs += 1
            if gf == 0:
                h_blanks += 1
        else:
            a_games += 1
            a_scored += gf
            a_conceded += ga
            if ga == 0:
                a_cs += 1

    n = len(matches)
    if n == 0:
        return {}

    def _r(x): return round(x / n, 3)
    def _hv(x, g): return round(x / g, 3) if g else None
    def _pg(x): return round(x / n, 2)

    # xG proxy: goals scored per game adjusted +15% for missed chances
    xg_proxy  = round(total_gf / n * 1.15, 2)
    xga_proxy = round(total_ga / n * 1.15, 2)

    # Recent scoring form: how many of the last 3 / last 5 games did the team score in?
    scored_last3 = sum(1 for g in gf_per_game[:3] if g > 0)
    scored_last5 = sum(1 for g in gf_per_game[:5] if g > 0)

    # Recent defensive form: how many clean sheets in last 3 / last 5 games?
    cs_last3 = sum(1 for g in ga_per_game[:3] if g == 0)
    cs_last5 = sum(1 for g in ga_per_game[:5] if g == 0)

    result = {
        "btts_rate":       _r(btts),
        "over15_rate":     _r(over15),
        "over25_rate":     _r(over25),
        "over35_rate":     _r(over35),
        "clean_sheet_rate":_r(cs),
        "scored_per_game": _pg(total_gf),
        "missed_per_game": _pg(total_ga),
        "xg_per_game":     xg_proxy,
        "xga_per_game":    xga_proxy,
        # venue splits
        "home_cs_rate":    _hv(h_cs, h_games),
        "away_cs_rate":    _hv(a_cs, a_games),
        "home_xg_pg":      round(h_scored / h_games * 1.15, 2) if h_games else None,
        "away_xg_pg":      round(a_scored / a_games * 1.15, 2) if a_games else None,
        "home_xga_pg":     round(h_conceded / h_games * 1.15, 2) if h_games else None,
        "away_xga_pg":     round(a_conceded / a_games * 1.15, 2) if a_games else None,
        "matches_sampled": n,
        # recent attacking form
        "scored_last3":    scored_last3,   # 0-3: how many of last 3 games team scored
        "scored_last5":    scored_last5,   # 0-5: how many of last 5 games team scored
        # recent defensive form
        "cs_last3":        cs_last3,       # 0-3: clean sheets in last 3 games
        "cs_last5":        cs_last5,       # 0-5: clean sheets in last 5 games
        # home blank rate — how often they fail to score at home
        "home_blank_rate": round(h_blanks / h_games, 3) if h_games else None,
    }
    _rates_cache[team_id] = (now, result)
    return result


# ── DB-backed team stats (used when historical data is available) ─────────────

_db_standings_cache: dict = {}

def _get_league_standings_from_db(competition_code: str, season: int, before_date: str) -> dict:
    """
    Compute league standings from HistoricalFixture for all teams in a league,
    using only matches played before before_date.
    Returns {team_id: {"position": int, "points": int, "played": int,
                        "goals_for": int, "goals_against": int,
                        "home": {...}, "away": {...}}}
    Results are cached per (competition_code, season, before_date) — safe because
    historical match results are immutable.
    """
    cache_key = (competition_code, season, before_date)
    if cache_key in _db_standings_cache:
        return _db_standings_cache[cache_key]

    from bets.models import HistoricalFixture

    fixtures = list(HistoricalFixture.objects.filter(
        league_code=competition_code,
        season=season,
        match_date__lt=before_date,
        home_score__isnull=False,
        away_score__isnull=False,
    ).values("home_team_id", "away_team_id", "home_score", "away_score"))

    if not fixtures:
        return {}

    teams: dict = {}

    def _team(tid):
        if tid not in teams:
            teams[tid] = {
                "points": 0, "played": 0, "won": 0, "drawn": 0, "lost": 0,
                "goals_for": 0, "goals_against": 0,
                "home": {"played": 0, "won": 0, "drawn": 0, "lost": 0,
                         "goals_for": 0, "goals_against": 0},
                "away": {"played": 0, "won": 0, "drawn": 0, "lost": 0,
                         "goals_for": 0, "goals_against": 0},
            }
        return teams[tid]

    for f in fixtures:
        htid = f["home_team_id"]
        atid = f["away_team_id"]
        hs   = f["home_score"]
        as_  = f["away_score"]
        if not htid or not atid:
            continue

        ht = _team(htid)
        at = _team(atid)

        # Overall
        ht["played"] += 1; at["played"] += 1
        ht["goals_for"] += hs; ht["goals_against"] += as_
        at["goals_for"] += as_; at["goals_against"] += hs

        # Home record
        ht["home"]["played"] += 1
        ht["home"]["goals_for"] += hs; ht["home"]["goals_against"] += as_

        # Away record
        at["away"]["played"] += 1
        at["away"]["goals_for"] += as_; at["away"]["goals_against"] += hs

        # Points
        if hs > as_:
            ht["points"] += 3; ht["won"] += 1
            ht["home"]["won"] += 1
            at["lost"] += 1; at["away"]["lost"] += 1
        elif hs < as_:
            at["points"] += 3; at["won"] += 1
            at["away"]["won"] += 1
            ht["lost"] += 1; ht["home"]["lost"] += 1
        else:
            ht["points"] += 1; ht["drawn"] += 1; ht["home"]["drawn"] += 1
            at["points"] += 1; at["drawn"] += 1; at["away"]["drawn"] += 1

    # Sort by points (then goal diff) to assign positions
    ranked = sorted(teams.items(),
                    key=lambda x: (x[1]["points"],
                                   x[1]["goals_for"] - x[1]["goals_against"]),
                    reverse=True)
    for pos, (tid, data) in enumerate(ranked, 1):
        data["position"] = pos

    _db_standings_cache[cache_key] = teams
    return teams


_standings_cache: dict = {}   # (comp_code, season, match_date) → {team: pts}
_h2h_cache: dict = {}         # (team_a, team_b, match_date) → avg_goals


def _contextual_o15_adjustment(home_team: str, away_team: str,
                               comp_code: str, match_date_str: str,
                               p_o15: float) -> tuple:
    """
    Adjusts Over 1.5 probability downward based on two contextual signals
    that season-average xG cannot capture:

      1. H2H goal history  — some matchups are structurally tight regardless
         of each team's season average (tactical familiarity, rivalry intensity,
         one team always parks the bus against the other).

      2. Table position gap — when a dominant leader plays a relegation side,
         the leader typically scores early and manages the game down to 1-0.
         Season xG doesn't know the leader only needed one goal.

    Returns (adjusted_p_o15: float, reason: str | None)
    Never inflates — only reduces.
    """
    try:
        from bets.models import HistoricalFixture
        from django.db.models import Q
        from collections import defaultdict

        factor  = 1.0
        reasons = []

        # ── 1. HEAD-TO-HEAD GOAL HISTORY ─────────────────────────────────────
        # Last 6 meetings between these two teams (any venue, any season).
        # Cached per pair+date so repeated calls on the same day are free.
        h2h_key = (min(home_team, away_team), max(home_team, away_team), match_date_str)
        if h2h_key not in _h2h_cache:
            h2h = list(
                HistoricalFixture.objects
                .filter(
                    Q(home_team=home_team, away_team=away_team) |
                    Q(home_team=away_team, away_team=home_team),
                    home_score__isnull=False,
                    match_date__lt=match_date_str,
                )
                .order_by("-match_date")
                .values("home_score", "away_score")[:6]
            )
            if len(h2h) >= 3:
                _h2h_cache[h2h_key] = sum(r["home_score"] + r["away_score"] for r in h2h) / len(h2h)
            else:
                _h2h_cache[h2h_key] = None  # not enough history

        avg_goals = _h2h_cache[h2h_key]
        if avg_goals is not None:
            if avg_goals < 1.6:
                factor *= 0.80
                reasons.append(f"H2H tight ({avg_goals:.1f}g avg)")
            elif avg_goals < 2.0:
                factor *= 0.91
                reasons.append(f"H2H low-scoring ({avg_goals:.1f}g avg)")

        # ── 2. TABLE POSITION GAP ────────────────────────────────────────────
        # Build season standings once per (league, date) — cached across all
        # fixtures on the same day to avoid repeating the same query 20× per date.
        std_key = (comp_code, match_date_str)
        if std_key not in _standings_cache:
            prior = list(
                HistoricalFixture.objects
                .filter(
                    league_code=comp_code,
                    season=2025,
                    match_date__lt=match_date_str,
                    home_score__isnull=False,
                )
                .values("home_team", "away_team", "winner")
            )
            pts: dict = defaultdict(int)
            for fx in prior:
                if fx["winner"] == "HOME":
                    pts[fx["home_team"]] += 3
                elif fx["winner"] == "AWAY":
                    pts[fx["away_team"]] += 3
                else:
                    pts[fx["home_team"]] += 1
                    pts[fx["away_team"]] += 1
            _standings_cache[std_key] = dict(pts)

        pts = _standings_cache[std_key]
        if pts and home_team in pts and away_team in pts:
            ranked = sorted(pts.keys(), key=lambda t: -pts[t])
            n = len(ranked)
            if n >= 6:
                home_pos = ranked.index(home_team) + 1
                away_pos = ranked.index(away_team) + 1
                pts_gap  = abs(pts[home_team] - pts[away_team])
                top_cut  = max(1, n // 4)
                bot_cut  = n - max(1, n // 4)

                one_top = min(home_pos, away_pos) <= top_cut
                one_bot = max(home_pos, away_pos) >= bot_cut

                if pts_gap >= 20 and one_top and one_bot:
                    factor *= 0.85
                    reasons.append(
                        f"Table gap large (Δ{pts_gap}pts, pos {home_pos} vs {away_pos})"
                    )
                elif pts_gap >= 12 and one_top and one_bot:
                    factor *= 0.92
                    reasons.append(
                        f"Table gap moderate (Δ{pts_gap}pts, pos {home_pos} vs {away_pos})"
                    )

        adjusted = round(p_o15 * factor, 4)
        reason   = " | ".join(reasons) if reasons else None
        return adjusted, reason

    except Exception:
        return p_o15, None


def _should_block_o15_booster(home_team: str, away_team: str,
                              comp_code: str, match_date_str: str) -> tuple:
    """
    Hard-block an Over 1.5 booster for this fixture based on strong contextual signals
    that season-average xG cannot capture.

    Uses the same caches as _contextual_o15_adjustment so no extra DB cost when
    both functions are called on the same day.

    Returns (block: bool, reason: str | None)

    Block conditions:
      • H2H avg goals < 2.2 across last 6 meetings — any pair with below-average H2H scoring.
        Threshold raised from 1.6: losses at 79% (Atalanta/Inter), 90% (Athletic/Barcelona)
        showed that pairs averaging even 1.8–2.1 goals still regularly produce 0-1/1-0 results.
      • Table gap ≥ 20 pts, one team top-quarter, other bottom-quarter — leader parks at 1-0
    """
    try:
        from bets.models import HistoricalFixture
        from django.db.models import Q
        from collections import defaultdict

        # ── 1. H2H TIGHT CHECK ───────────────────────────────────────────────
        h2h_key = (min(home_team, away_team), max(home_team, away_team), match_date_str)
        if h2h_key not in _h2h_cache:
            h2h = list(
                HistoricalFixture.objects
                .filter(
                    Q(home_team=home_team, away_team=away_team) |
                    Q(home_team=away_team, away_team=home_team),
                    home_score__isnull=False,
                    match_date__lt=match_date_str,
                )
                .order_by("-match_date")
                .values("home_score", "away_score")[:6]
            )
            if len(h2h) >= 2:  # require only 2 prior meetings — less strict
                _h2h_cache[h2h_key] = sum(r["home_score"] + r["away_score"] for r in h2h) / len(h2h)
            else:
                _h2h_cache[h2h_key] = None

        avg_goals = _h2h_cache[h2h_key]
        if avg_goals is not None and avg_goals < 1.8:
            return True, f"H2H structurally tight ({avg_goals:.1f}g avg)"

        # ── 2. TABLE DOMINATION CHECK ────────────────────────────────────────
        std_key = (comp_code, match_date_str)
        if std_key not in _standings_cache:
            prior = list(
                HistoricalFixture.objects
                .filter(
                    league_code=comp_code,
                    season=2025,
                    match_date__lt=match_date_str,
                    home_score__isnull=False,
                )
                .values("home_team", "away_team", "winner")
            )
            pts: dict = defaultdict(int)
            for fx in prior:
                if fx["winner"] == "HOME":
                    pts[fx["home_team"]] += 3
                elif fx["winner"] == "AWAY":
                    pts[fx["away_team"]] += 3
                else:
                    pts[fx["home_team"]] += 1
                    pts[fx["away_team"]] += 1
            _standings_cache[std_key] = dict(pts)

        pts = _standings_cache[std_key]
        if pts and home_team in pts and away_team in pts:
            ranked = sorted(pts.keys(), key=lambda t: -pts[t])
            n = len(ranked)
            if n >= 6:
                home_pos = ranked.index(home_team) + 1
                away_pos = ranked.index(away_team) + 1
                pts_gap  = abs(pts[home_team] - pts[away_team])
                top_cut  = max(1, n // 4)
                bot_cut  = n - max(1, n // 4)
                one_top  = min(home_pos, away_pos) <= top_cut
                one_bot  = max(home_pos, away_pos) >= bot_cut
                if pts_gap >= 20 and one_top and one_bot:
                    return True, f"Table domination (Δ{pts_gap}pts, pos {home_pos} vs {away_pos})"

        return False, None

    except Exception:
        return False, None  # fail open — never block a booster due to a query error


def key_player_absence_factor(team_name: str, match_date_str: str,
                              n_recent: int = 3, min_goals: int = 5):
    """
    Check whether a team's top scorer has been absent from recent fixtures.

    Returns (factor: float, warning: str | None)
      factor = 0.82 if the player missed 2+ of the last n_recent games
               1.0  otherwise
    Calibrated against Galatasaray/Osimhen data: ~19% goal drop when absent.
    """
    try:
        from bets.models import PlayerMatchRating, HistoricalFixture
        from django.db.models import Sum, Q

        # ── Find top scorer for this team this season ─────────────────────────
        top = (
            PlayerMatchRating.objects
            .filter(team_name=team_name)
            .values("player_name", "player_id")
            .annotate(total_goals=Sum("goals"))
            .order_by("-total_goals")
            .first()
        )
        if not top or (top["total_goals"] or 0) < min_goals:
            return 1.0, None  # no dominant scorer — no adjustment needed

        player_name = top["player_name"]
        player_id   = top["player_id"]

        # ── Last n_recent completed fixtures for this team before match_date ──
        recent = list(
            HistoricalFixture.objects
            .filter(
                Q(home_team=team_name) | Q(away_team=team_name),
                home_score__isnull=False,
                match_date__lt=match_date_str,
            )
            .order_by("-match_date")[:n_recent]
        )
        if len(recent) < 2:
            return 1.0, None

        absent = sum(
            1 for fx in recent
            if not PlayerMatchRating.objects.filter(
                fixture=fx, player_id=player_id, minutes_played__gt=0
            ).exists()
        )

        if absent >= 2:
            return 0.82, f"⚠ {player_name} absent {absent}/{len(recent)} recent games"

        return 1.0, None
    except Exception:
        return 1.0, None


def _get_team_stats_from_db(
    team_id: int,
    team_name: str,
    competition_code: str,
    match_date_str: str,
    season: int = 2025,
) -> dict | None:
    """
    Compute the same stats dict as get_team_season_stats() but entirely from
    the local HistoricalFixture + MatchStats + MatchUnderstatStats tables.
    Returns None if there are fewer than 4 completed matches in the DB for
    this team/league (not enough to compute meaningful stats).
    """
    from bets.models import HistoricalFixture, MatchStats, MatchUnderstatStats
    from django.db.models import Q, Avg, Sum

    # All completed fixtures for this team in this league before the match date
    # Query current season first; if fewer than 5 matches, also pull previous
    # season to give the model data at the very start of a new campaign.
    qs = HistoricalFixture.objects.filter(
        Q(home_team_id=team_id) | Q(away_team_id=team_id),
        league_code=competition_code,
        season=season,
        match_date__lt=match_date_str,
        home_score__isnull=False,
        away_score__isnull=False,
    ).order_by("-match_date")

    matches = list(qs.values(
        "id", "home_team_id", "away_team_id", "home_score", "away_score", "match_date"
    ))

    # Not enough current-season data — supplement with previous season
    if len(matches) < 5:
        prev_qs = HistoricalFixture.objects.filter(
            Q(home_team_id=team_id) | Q(away_team_id=team_id),
            league_code=competition_code,
            season=season - 1,
            home_score__isnull=False,
            away_score__isnull=False,
        ).order_by("-match_date")[:30]  # last 30 matches of previous season
        prev_matches = list(prev_qs.values(
            "id", "home_team_id", "away_team_id", "home_score", "away_score", "match_date"
        ))
        matches = matches + prev_matches

    if len(matches) < 1:
        return None

    # ── Compute match-level rates ─────────────────────────────────────────────
    btts = over15 = over25 = over35 = cs = 0
    h_cs = h_games = h_blanks = 0
    a_cs = a_games = 0
    h_scored = h_conceded = 0
    a_scored = a_conceded = 0
    total_gf = total_ga = 0
    gf_series = []   # ordered most-recent first
    ga_series = []

    for m in matches:
        is_home = m["home_team_id"] == team_id
        gf = m["home_score"] if is_home else m["away_score"]
        ga = m["away_score"] if is_home else m["home_score"]
        tg = m["home_score"] + m["away_score"]

        gf_series.append(gf); ga_series.append(ga)
        total_gf += gf; total_ga += ga

        if gf > 0 and ga > 0: btts += 1
        if tg >= 2: over15 += 1
        if tg >= 3: over25 += 1
        if tg >= 4: over35 += 1
        if ga == 0: cs += 1

        if is_home:
            h_games += 1; h_scored += gf; h_conceded += ga
            if ga == 0: h_cs += 1
            if gf == 0: h_blanks += 1
        else:
            a_games += 1; a_scored += gf; a_conceded += ga
            if ga == 0: a_cs += 1

    n = len(matches)
    _r  = lambda x: round(x / n, 3)
    _hv = lambda x, g: round(x / g, 3) if g else None
    _pg = lambda x: round(x / n, 2)

    # ── xG from MatchStats (API-Football) ────────────────────────────────────
    fixture_ids = [m["id"] for m in matches]
    ms_rows = list(MatchStats.objects.filter(
        fixture_id__in=fixture_ids,
        xg_home__isnull=False,
    ).values("fixture_id", "xg_home", "xg_away", "yellow_home", "yellow_away"))

    ms_by_fid = {r["fixture_id"]: r for r in ms_rows}

    xg_total = xga_total = 0.0
    h_xg_total = h_xga_total = 0.0
    a_xg_total = a_xga_total = 0.0
    h_xg_games = a_xg_games = 0
    yellow_total = 0; yellow_games = 0

    for m in matches:
        ms = ms_by_fid.get(m["id"])
        if ms and ms["xg_home"] is not None:
            is_home = m["home_team_id"] == team_id
            xg  = ms["xg_home"] if is_home else ms["xg_away"]
            xga = ms["xg_away"] if is_home else ms["xg_home"]
            xg_total += xg; xga_total += xga
            if is_home:
                h_xg_total += xg; h_xga_total += xga; h_xg_games += 1
            else:
                a_xg_total += xg; a_xga_total += xga; a_xg_games += 1
        if ms:
            yel = ms["yellow_home"] if m["home_team_id"] == team_id else ms["yellow_away"]
            if yel is not None:
                yellow_total += yel; yellow_games += 1

    xg_games = h_xg_games + a_xg_games
    xg_per_game  = round(xg_total  / xg_games, 2) if xg_games else round(total_gf / n * 1.15, 2)
    xga_per_game = round(xga_total / xg_games, 2) if xg_games else round(total_ga / n * 1.15, 2)
    home_xg_pg   = round(h_xg_total  / h_xg_games, 2) if h_xg_games else None
    away_xg_pg   = round(a_xg_total  / a_xg_games, 2) if a_xg_games else None
    home_xga_pg  = round(h_xga_total / h_xg_games, 2) if h_xg_games else None
    away_xga_pg  = round(a_xga_total / a_xg_games, 2) if a_xg_games else None

    yellow_cards_pg = round(yellow_total / yellow_games, 2) if yellow_games else None

    # ── Understat xG override for Big 5 leagues ───────────────────────────────
    _UNDERSTAT_CODES = {"PL", "PD", "BL1", "SA", "FL1"}
    if competition_code in _UNDERSTAT_CODES:
        us_rows = list(MatchUnderstatStats.objects.filter(
            fixture_id__in=fixture_ids,
        ).values("fixture_id", "xg_open_play_home", "xg_open_play_away",
                 "xg_set_piece_home", "xg_set_piece_away",
                 "xg_penalty_home", "xg_penalty_away"))

        us_by_fid = {}
        for r in us_rows:
            # Sum all xG components
            def _s(*keys): return sum((r.get(k) or 0) for k in keys)
            us_by_fid[r["fixture_id"]] = {
                "xg_h": _s("xg_open_play_home","xg_set_piece_home","xg_penalty_home"),
                "xg_a": _s("xg_open_play_away","xg_set_piece_away","xg_penalty_away"),
            }

        u_xg = u_xga = 0.0; u_n = 0
        u_h_xg = u_h_xga = u_h_n = 0.0
        u_a_xg = u_a_xga = u_a_n = 0.0
        for m in matches:
            us = us_by_fid.get(m["id"])
            if not us:
                continue
            is_home = m["home_team_id"] == team_id
            xg  = us["xg_h"] if is_home else us["xg_a"]
            xga = us["xg_a"] if is_home else us["xg_h"]
            u_xg += xg; u_xga += xga; u_n += 1
            if is_home: u_h_xg += xg; u_h_xga += xga; u_h_n += 1
            else:       u_a_xg += xg; u_a_xga += xga; u_a_n += 1

        if u_n >= 3:
            xg_per_game  = round(u_xg  / u_n, 2)
            xga_per_game = round(u_xga / u_n, 2)
            if u_h_n:
                home_xg_pg  = round(u_h_xg  / u_h_n, 2)
                home_xga_pg = round(u_h_xga / u_h_n, 2)
            if u_a_n:
                away_xg_pg  = round(u_a_xg  / u_a_n, 2)
                away_xga_pg = round(u_a_xga / u_a_n, 2)

    # ── Recent xG conversion (last 5 games with xG data) ─────────────────────
    # Tracks whether the team is actually converting their expected goals lately.
    # A ratio < 0.65 means they're scoring well below what their xG predicts —
    # the scoring-probability discount in _calc_team_goal_lines uses this.
    _xg_by_fid: dict = {}
    _UNDERSTAT_CODES_LOCAL = {"PL", "PD", "BL1", "SA", "FL1"}
    for m in matches:
        is_home_m = m["home_team_id"] == team_id
        fid = m["id"]
        if competition_code in _UNDERSTAT_CODES_LOCAL and fid in us_by_fid:
            us = us_by_fid[fid]
            _xg_by_fid[fid] = us["xg_h"] if is_home_m else us["xg_a"]
        elif fid in ms_by_fid:
            ms_r = ms_by_fid[fid]
            if ms_r.get("xg_home") is not None:
                _xg_by_fid[fid] = ms_r["xg_home"] if is_home_m else ms_r["xg_away"]

    _recent_gf, _recent_xg = [], []
    for m in matches[:5]:
        xg_v = _xg_by_fid.get(m["id"])
        if xg_v is not None:
            is_home_m = m["home_team_id"] == team_id
            _recent_gf.append(m["home_score"] if is_home_m else m["away_score"])
            _recent_xg.append(xg_v)

    xg_conversion_last5 = (
        round(sum(_recent_gf) / sum(_recent_xg), 3)
        if _recent_xg and sum(_recent_xg) > 0.5
        else None
    )

    # ── Standings ─────────────────────────────────────────────────────────────
    all_standings = _get_league_standings_from_db(competition_code, season, match_date_str)
    standing = all_standings.get(team_id, {})
    home_rec = standing.get("home", {})
    away_rec = standing.get("away", {})

    # ── Assemble result (same shape as get_team_season_stats) ─────────────────
    gf_total = standing.get("goals_for", total_gf)
    ga_total = standing.get("goals_against", total_ga)
    played   = standing.get("played", n) or 1

    result = {
        "team_id":   team_id,
        "team_name": team_name,

        "position":         standing.get("position"),
        "points":           standing.get("points"),
        "played":           played,
        "goals_for_pg":     round(gf_total / played, 2),
        "goals_against_pg": round(ga_total / played, 2),

        "home": home_rec,
        "away": away_rec,

        "xg_per_game":      xg_per_game,
        "xga_per_game":     xga_per_game,
        "ppda_avg":         None,   # not stored in DB yet
        "scored_per_game":  _pg(total_gf),
        "missed_per_game":  _pg(total_ga),
        "clean_sheet_rate": _r(cs),
        "btts_rate":        _r(btts),
        "over25_rate":      _r(over25),
        "over15_rate":      _r(over15),
        "over35_rate":      _r(over35),

        "home_xg_pg":   home_xg_pg,
        "home_xga_pg":  home_xga_pg,
        "home_cs_rate": _hv(h_cs, h_games),
        "away_xg_pg":   away_xg_pg,
        "away_xga_pg":  away_xga_pg,
        "away_cs_rate": _hv(a_cs, a_games),

        "scored_last3": sum(1 for g in gf_series[:3] if g > 0),
        "scored_last5": sum(1 for g in gf_series[:5] if g > 0),
        "cs_last3":     sum(1 for g in ga_series[:3] if g == 0),
        "cs_last5":     sum(1 for g in ga_series[:5] if g == 0),
        "home_blank_rate": _hv(h_blanks, h_games),

        "yellow_cards_pg": yellow_cards_pg,
        "competition_code": competition_code,
        "xg_conversion_last5": xg_conversion_last5,
    }

    # xG overperformance ratio
    _gf_pg = result.get("goals_for_pg") or 0
    _xg_pg = result.get("xg_per_game") or 0
    if _xg_pg > 0.3 and _gf_pg > 0:
        result["xg_overperform_ratio"] = round(_gf_pg / _xg_pg, 3)
    else:
        result["xg_overperform_ratio"] = None

    # Apply xG discount for known inflationary leagues
    xg_factor = _XG_LEAGUE_FACTORS.get(competition_code, 1.0)
    if xg_factor < 1.0:
        for key in ("xg_per_game","home_xg_pg","away_xg_pg","scored_per_game","goals_for_pg"):
            if result.get(key) is not None:
                result[key] = round(result[key] * xg_factor, 3)
        for key in ("btts_rate","over25_rate","over35_rate","over15_rate"):
            if result.get(key) is not None:
                result[key] = round(result[key] * xg_factor, 3)
        result["_xg_discount"] = xg_factor

    return result


# ── Team season stats (combined aggregator) ───────────────────────────────────
_season_stats_cache: dict = {}
_SEASON_STATS_TTL = 6 * 3600


def get_team_season_stats(
    team_id: int,
    team_name: str,
    competition_code: str,
    match_date: str,
) -> dict:
    """
    Returns a comprehensive stats object for a team, combining:
      - Standings total + home/away records (from football-data.org)
      - Season xG, PPDA, BTTS/over rates (from Understat)
      - Average yellow cards per game (from match bookings)

    Used by the daily predictions engine.
    """
    import time as _time
    now = _time.time()
    cache_key = (team_id, competition_code)
    cached = _season_stats_cache.get(cache_key)
    if cached:
        ts, data = cached
        if now - ts < _SEASON_STATS_TTL:
            return data

    # ── Try DB-backed stats first (historical dates / backtest mode) ──────────
    if team_id:
        try:
            db_result = _get_team_stats_from_db(
                team_id=team_id,
                team_name=team_name,
                competition_code=competition_code,
                match_date_str=match_date,
                season=2025,
            )
            if db_result is not None:
                _season_stats_cache[cache_key] = (now, db_result)
                return db_result
        except Exception:
            pass  # fall through to live API

    # ── 1. Standings ──────────────────────────────────────────────────────────
    standings = _get_standings(competition_code)
    standing  = standings.get(team_id, {})
    home_rec  = standing.get("home", {})
    away_rec  = standing.get("away", {})

    total_played = standing.get("played", 0) or 1   # avoid div/0

    # ── 2. Understat league team data (with fallback to match-history rates) ───
    from .understat import get_league_teams_data
    from difflib import SequenceMatcher

    teams_data = get_league_teams_data(competition_code, match_date)
    ustat: dict = {}
    if teams_data:
        best_title, best_sim = None, 0.0
        norm_name = team_name.lower().strip()
        for title in teams_data:
            sim = SequenceMatcher(None, norm_name, title.lower().strip()).ratio()
            if sim > best_sim:
                best_sim = sim
                best_title = title
        if best_title and best_sim >= 0.55:
            ustat = teams_data[best_title]

    # Always compute match-history rates — used as fallback for fields
    # Understat doesn't provide (BTTS, over-2.5, clean-sheet, venue splits)
    rates: dict = {}
    if team_id:
        rates = _get_team_match_rates(team_id, limit=20)

    # ── API-Football xG fallback for leagues not covered by Understat ──────────
    # Understat only covers: PL, PD, BL1, SA, FL1
    # For all other leagues (DED, PPL, CL, EL, UECL, etc.) pull xG from
    # API-Football fixtures/statistics so the prediction engine still has
    # shot-quality data rather than falling back to raw goal rates.
    if not ustat and team_id:
        try:
            from .api_football import get_team_xg_stats
            apif_xg = get_team_xg_stats(team_id, competition_code)
            if apif_xg:
                # Only fill in keys that rates doesn't already have
                for k, v in apif_xg.items():
                    if v is not None and rates.get(k) is None:
                        rates[k] = v
        except Exception:
            pass

    def _u(key):
        """Understat first (real xG/PPDA), fall back to match-history rates."""
        val = ustat.get(key) if ustat else None
        return val if val is not None else rates.get(key)

    # ── 3. Yellow cards ───────────────────────────────────────────────────────
    yellow_cards_pg = _get_team_yellow_cards_per_game(team_id)

    # ── Build result ──────────────────────────────────────────────────────────
    result = {
        "team_id":   team_id,
        "team_name": team_name,

        # Season totals
        "position":          standing.get("position"),
        "points":            standing.get("points"),
        "played":            standing.get("played", 0),
        "goals_for_pg":      round(standing.get("goals_for", 0) / total_played, 2),
        "goals_against_pg":  round(standing.get("goals_against", 0) / total_played, 2),

        # Home record
        "home": home_rec,

        # Away record
        "away": away_rec,

        # xG / rates — from Understat if available, else computed from match history
        "xg_per_game":        _u("xg_per_game"),
        "xga_per_game":       _u("xga_per_game"),
        "ppda_avg":           _u("ppda_avg"),
        "scored_per_game":    _u("scored_per_game"),
        "missed_per_game":    _u("missed_per_game"),
        "clean_sheet_rate":   _u("clean_sheet_rate"),
        "btts_rate":          _u("btts_rate"),
        "over25_rate":        _u("over25_rate"),
        "over35_rate":        _u("over35_rate"),

        # Venue-specific xG (more precise for home/away predictions)
        "home_xg_pg":         _u("home_xg_pg"),
        "home_xga_pg":        _u("home_xga_pg"),
        "home_cs_rate":       _u("home_cs_rate"),
        "away_xg_pg":         ustat.get("away_xg_pg"),
        "away_xga_pg":        ustat.get("away_xga_pg"),
        "away_cs_rate":       ustat.get("away_cs_rate"),

        # Recent scoring form (last 3 / last 5 finished matches)
        "scored_last3":       rates.get("scored_last3"),
        "scored_last5":       rates.get("scored_last5"),

        # Recent defensive form — clean sheets in last 3 / last 5
        "cs_last3":           rates.get("cs_last3"),
        "cs_last5":           rates.get("cs_last5"),

        # Home blank rate — how often they fail to score at home
        "home_blank_rate":    rates.get("home_blank_rate"),

        # Discipline
        "yellow_cards_pg":    yellow_cards_pg,

        # League context — passed through to prediction calculators
        "competition_code":   competition_code,
    }

    # ── xG overperformance ratio ─────────────────────────────────────────────
    # goals_for_pg / xg_per_game  >1 = scoring more than xG predicts (likely to regress)
    _gf_pg = result.get("goals_for_pg") or 0
    _xg_pg = result.get("xg_per_game") or 0
    if _xg_pg > 0.3 and _gf_pg > 0:
        result["xg_overperform_ratio"] = round(_gf_pg / _xg_pg, 3)
    else:
        result["xg_overperform_ratio"] = None

    # ── Apply xG discount for leagues where API-Football xG overestimates ────
    xg_factor = _XG_LEAGUE_FACTORS.get(competition_code, 1.0)
    if xg_factor < 1.0:
        _XG_KEYS = (
            "xg_per_game", "home_xg_pg", "away_xg_pg",
            "scored_per_game", "goals_for_pg",
        )
        for key in _XG_KEYS:
            if result.get(key) is not None:
                result[key] = round(result[key] * xg_factor, 3)
        # Also scale derived rates so BTTS/Over signals reflect the discount
        for key in ("btts_rate", "over25_rate", "over35_rate", "over15_rate"):
            if result.get(key) is not None:
                # Compress toward a lower probability: p → p * factor
                result[key] = round(result[key] * xg_factor, 3)
        result["_xg_discount"] = xg_factor  # visible in evidence for transparency

    _season_stats_cache[cache_key] = (now, result)
    return result


def get_live_matches_batch(fixture_ids: list[int]) -> list[dict]:
    """
    Fetches multiple fixtures in one request using the ids filter.
    Counts as 1 API call — important for rate limit management.
    """
    if not fixture_ids:
        return []

    ids_param = ",".join(str(i) for i in fixture_ids)
    try:
        resp = requests.get(
            f"{BASE_URL}/matches",
            headers=_headers(),
            params={"ids": ids_param},
            timeout=15,
        )
        resp.raise_for_status()
        matches = resp.json().get("matches", [])
    except requests.RequestException:
        return []

    results = []
    for data in matches:
        score = data.get("score", {})
        full_time = score.get("fullTime", {})
        results.append({
            "fixture_id": data["id"],
            "match_status": data.get("status"),
            "match_minute": data.get("minute"),
            "score_home": full_time.get("home") or 0,
            "score_away": full_time.get("away") or 0,
            "home_team": data.get("homeTeam", {}).get("name"),
            "away_team": data.get("awayTeam", {}).get("name"),
        })

    return results
