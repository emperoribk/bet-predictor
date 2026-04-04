import requests

# SportyBet regional codes — tried in order
REGIONS = ['ng', 'ke', 'gh', 'tz', 'zm', 'cm', 'ug', 'et']

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.sportybet.com/",
}


def _try_region(region: str, booking_code: str):
    """
    Fetches a booking code from a specific region.
    Returns (data, None) on success or (None, error_str) on failure.
    """
    url = f"https://www.sportybet.com/api/{region}/orders/share/{booking_code}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
    except requests.RequestException as e:
        return None, str(e)

    if not resp.ok:
        return None, f"http_{resp.status_code}"

    try:
        body = resp.json()
    except Exception:
        return None, "invalid_json"

    if body.get("bizCode") != 10000 or not body.get("isAvailable"):
        return None, "not_found"

    data = body.get("data", {})
    if not data.get("outcomes"):
        return None, "not_found"

    return data, None


def _build_pick_label(pick_desc: str, market_name: str, home: str, away: str) -> str:
    """
    Converts raw SportyBet pick fields into a clear human-readable label.

    Examples:
      "Home", "1X2"            → "Aston Villa"
      "Away", "1X2"            → "Eintracht Frankfurt"
      "Yes", "Home To Score"   → "Aston Villa to score"
      "Yes", "Away To Score"   → "Frankfurt to score"
      "Yes", "Both Teams Score"→ "Both teams to score"
      "No",  "Home To Score"   → "Aston Villa NOT to score"
      "Over 2.5", "Total Goals"→ "Over 2.5 goals"
      "Draw"                   → "Draw"
    """
    pd = pick_desc.strip()
    mn = market_name.strip()
    pd_lower = pd.lower()
    mn_lower = mn.lower()

    # ── 1X2 / match result ───────────────────────────────────
    if pd_lower == "home":
        return home
    if pd_lower == "away":
        return away
    if pd_lower in ("draw", "x"):
        return "Draw"

    # ── Team to Score ────────────────────────────────────────
    # Matches: "Home To Score", "Home Team To Score", "Home Score",
    #          "Away To Score", "Away Team To Score", "Away Score", etc.
    _is_home_score_market = (
        "to score" in mn_lower
        or "score" in mn_lower
    ) and "home" in mn_lower and "away" not in mn_lower and "both" not in mn_lower

    _is_away_score_market = (
        "to score" in mn_lower
        or "score" in mn_lower
    ) and "away" in mn_lower and "home" not in mn_lower and "both" not in mn_lower

    if _is_home_score_market or _is_away_score_market or "to score" in mn_lower:
        if pd_lower == "yes":
            if _is_home_score_market:
                return f"{home} to score"
            if _is_away_score_market:
                return f"{away} to score"
            if "home" in mn_lower:
                return f"{home} to score"
            if "away" in mn_lower:
                return f"{away} to score"
            return f"{mn} ✓"
        if pd_lower == "no":
            if _is_home_score_market:
                return f"{home} NOT to score"
            if _is_away_score_market:
                return f"{away} NOT to score"
            if "home" in mn_lower:
                return f"{home} NOT to score"
            if "away" in mn_lower:
                return f"{away} NOT to score"
            return f"{mn} ✗"

    # ── Both Teams to Score ──────────────────────────────────
    if any(k in mn_lower for k in ("both teams", "btts", "gg/ng")):
        if pd_lower == "yes":
            return "Both teams to score"
        if pd_lower == "no":
            return "Both teams NOT to score"

    # ── Team total goals (e.g. "Home Total Goals", "Aston Villa Goals", "Away Goals Over/Under") ──
    # "Over 0.5" on a team-specific market means "that team to score"
    import re as _re

    home_lower = home.lower()
    away_lower = away.lower()

    # Check by home/away keyword OR by actual team name in market name
    _has_home = (
        ("home" in mn_lower and "away" not in mn_lower and "both" not in mn_lower)
        or (home_lower and home_lower in mn_lower and away_lower not in mn_lower)
    )
    _has_away = (
        ("away" in mn_lower and "home" not in mn_lower and "both" not in mn_lower)
        or (away_lower and away_lower in mn_lower and home_lower not in mn_lower)
    )
    _team_goals_market = (
        _has_home or _has_away
    ) and any(k in mn_lower for k in ("total", "goal", "over", "under", "score", "o/u", "ou"))

    if _team_goals_market and (pd_lower.startswith("over") or pd_lower.startswith("under")):
        m2 = _re.match(r'(over|under)\s*(\d+\.?\d*)', pd_lower)
        threshold = float(m2.group(2)) if m2 else 0.0
        team = home if _has_home else away
        direction = m2.group(1) if m2 else "over"
        if direction == "over":
            if threshold < 1:
                return f"{team} to score"
            else:
                needed = int(threshold) + 1
                return f"{team} to score {needed}+"
        else:
            # Under 0.5 = team must not score (cleaner phrasing)
            if threshold <= 0.5:
                return f"{team} NOT to score"
            return f"{team} to score {int(threshold)} or less"

    # ── Over / Under total goals ─────────────────────────────
    if pd_lower.startswith("over") or pd_lower.startswith("under"):
        return f"{pd} goals"

    # ── Clean sheet ──────────────────────────────────────────
    if "clean sheet" in mn_lower:
        if pd_lower == "yes":
            team = home if "home" in mn_lower else away
            return f"{team} clean sheet"
        if pd_lower == "no":
            team = home if "home" in mn_lower else away
            return f"No clean sheet — {team}"

    # ── Half-time / Full-time / Double chance ────────────────
    if mn_lower and pd:
        # If the pick itself is already descriptive, just return it
        if mn_lower not in pd_lower:
            return f"{mn}: {pd}"
        return pd

    return pd or mn or "Unknown"


def decode_booking_code(booking_code: str) -> list[dict]:
    """
    Tries all SportyBet regions until the booking code is found.
    Returns a list of match dicts parsed from the outcomes array.
    """
    for region in REGIONS:
        data, err = _try_region(region, booking_code)

        if err:
            continue

        outcomes = data.get("outcomes", [])
        matches = []

        for outcome in outcomes:
            home = outcome.get("homeTeamName", "")
            away = outcome.get("awayTeamName", "")
            kickoff = outcome.get("estimateStartTime")  # milliseconds timestamp

            markets = outcome.get("markets", [])
            if not markets:
                continue

            market = markets[0]
            market_name = market.get("name", "")
            market_outcomes = market.get("outcomes", [])

            if not market_outcomes:
                continue

            selected = market_outcomes[0]
            pick_desc = selected.get("desc", "")   # "Home", "Away", "Yes", "Over 2.5", etc.
            odds = selected.get("odds")

            # Use desc field as fallback when name is empty — SportyBet sometimes
            # puts the full description (e.g. "Aston Villa Over/Under") in desc only
            if not market_name:
                market_name = market.get("desc", "")

            user_pick = _build_pick_label(pick_desc, market_name, home, away)

            matches.append({
                "home_team": home,
                "away_team": away,
                "kickoff_time": kickoff,
                "user_pick": user_pick,
                "odds": float(odds) if odds else None,
                "market": market_name,
            })

        if matches:
            return matches

    raise ValueError(
        "Booking code not found across all regions. "
        "Make sure you are using a valid SportyBet share code."
    )
