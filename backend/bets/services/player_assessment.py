"""
Player Assessment Engine — position-specific form analysis for bet prediction.

Data flow:
  1. football-data.org  → lineup (player names + detailed positions)
  2. Understat getPlayersStats → team-keyed {player_name: understat_id} map
  3. Understat getPlayerMatches  → per-player last-N match stats
  4. Per-90 metrics → position-specific score (0-100)
  5. "Bad game" flag → auto-substitute with backup player
  6. Aggregate team efficiency → bet signals with evidence

Positions assessed:
  Goalkeeper  — distribution quality, save index (from match-page xG)
  Centre-Back — xGBuildup (ball-playing), xGChain (involvement)
  Fullback    — xA/KP (crossing threat) + xGBuildup (defensive quality)
  Def. Mid    — xGBuildup (progression) + xGChain (screening)
  Cen. Mid    — key_passes + xGChain + xGBuildup
  Att. Mid    — xA + key_passes + xG goal threat
  Winger      — xA + key_passes + shot volume
  Striker     — npxG + shots + finishing ratio (goals/xG)
"""

import time
import requests
from difflib import SequenceMatcher

from .understat import _get_player_matches, BASE, HEADERS, LEAGUE_CONFIG, _season_label, get_player_shot_history

# ── Caches ────────────────────────────────────────────────────────────────────
_league_players_cache: dict = {}   # "{slug}_{season}" → (ts, team_map)
_player_form_cache: dict    = {}   # "{player_id}_{n}" → (ts, form_dict)

LEAGUE_PLAYERS_TTL = 6 * 3600     # 6 h — squad doesn't change match-to-match
FORM_TTL           = 3 * 3600     # 3 h — form is stable within a day

# ── football-data.org position → (group, is_attacking, is_fullback) ──────────
FD_POSITION_MAP = {
    "Goalkeeper":         ("GK", False, False),
    "Centre-Back":        ("D",  False, False),
    "Left-Back":          ("D",  False, True),
    "Right-Back":         ("D",  False, True),
    "Defensive Midfield": ("M",  False, False),
    "Central Midfield":   ("M",  False, False),
    "Attacking Midfield": ("M",  True,  False),
    "Left Midfield":      ("M",  True,  False),
    "Right Midfield":     ("M",  True,  False),
    "Left Winger":        ("M",  True,  False),
    "Right Winger":       ("M",  True,  False),
    "Centre-Forward":     ("F",  False, False),
    "Second Striker":     ("F",  False, False),
    # Generic fallbacks football-data.org uses when specific position is unknown
    "Offence":            ("F",  False, False),
    "Midfield":           ("M",  False, False),
    "Defence":            ("D",  False, False),
    "Goalkeeper":         ("GK", False, False),
}

# League-average per-90 benchmarks (EPL 2024-25 estimates)
# Used to normalise scores — values above these are "good"
BENCHMARKS = {
    "npxg_elite":    0.40,   # striker elite npxG/90
    "npxg_avg":      0.15,   # striker average npxG/90
    "shots_elite":   4.0,    # striker shots/90 elite
    "xa_elite_m":    0.35,   # midfielder xA/90 elite
    "xa_elite_fb":   0.20,   # fullback xA/90 elite
    "kp_elite":      2.5,    # key passes/90 elite
    "buildup_elite": 0.40,   # xGBuildup/90 elite (mids)
    "buildup_def":   0.30,   # xGBuildup/90 elite (defenders)
    "chain_elite":   0.60,   # xGChain/90 elite
    "gk_buildup":    0.20,   # GK xGBuildup/90 elite (distribution)
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _per90(value: float, minutes: float) -> float:
    if not value or not minutes or minutes < 15:
        return 0.0
    return float(value) * 90.0 / float(minutes)


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


# ── Understat player roster fetch ─────────────────────────────────────────────

def _get_league_players(league_slug: str, season: str) -> dict:
    """
    Returns {team_name: {player_name: player_id_str}} for an entire league/season.
    Fetched from Understat main/getPlayersStats.
    """
    cache_key = f"{league_slug}_{season}"
    now = time.time()
    cached = _league_players_cache.get(cache_key)
    if cached:
        ts, data = cached
        if now - ts < LEAGUE_PLAYERS_TTL:
            return data

    try:
        r = requests.post(
            f"{BASE}/main/getPlayersStats/",
            data={"league": league_slug, "season": season},
            headers={**HEADERS, "X-Requested-With": "XMLHttpRequest",
                     "Referer": "https://understat.com/"},
            timeout=20,
        )
        body = r.json()
        players = body.get("players", [])
    except Exception as exc:
        print(f"[Assessment] Could not fetch league players: {exc}")
        players = []

    team_map: dict = {}
    for p in players:
        team = p.get("team_title", "")
        pid  = p.get("id", "")
        name = p.get("player_name", "")
        pos  = p.get("position", "")
        if team and pid and name:
            team_map.setdefault(team, {})[name] = {"id": pid, "position": pos}

    _league_players_cache[cache_key] = (now, team_map)
    return team_map


def _find_understat_id(player_name: str, team_name: str, team_map: dict) -> str | None:
    """
    Fuzzy-match player_name + team_name against the Understat roster.
    Returns string player_id or None.
    """
    # Find the best-matching team first
    best_team, best_team_score = None, 0.0
    for t in team_map:
        s = _sim(team_name, t)
        if s > best_team_score:
            best_team_score, best_team = s, t

    if not best_team or best_team_score < 0.45:
        return None

    players = team_map[best_team]

    # Exact lookup
    if player_name in players:
        return players[player_name]["id"]

    # Fuzzy name match within the team
    best_pid, best_score = None, 0.0
    for name, info in players.items():
        s = _sim(player_name, name)
        if s > best_score:
            best_score, best_pid = s, info["id"]

    return best_pid if best_score >= 0.60 else None


# ── Per-player form computation ───────────────────────────────────────────────

def _get_player_form(player_id: str, n_games: int = 5, player_name: str = "") -> dict | None:
    """
    Aggregate the last n_games matches for a player into per-90 metrics.
    Returns a form dict or None if no data.
    """
    cache_key = f"{player_id}_{n_games}"
    now = time.time()
    cached = _player_form_cache.get(cache_key)
    if cached:
        ts, data = cached
        if now - ts < FORM_TTL:
            return data

    all_matches = _get_player_matches(int(player_id))
    if not all_matches:
        return None

    recent = sorted(all_matches, key=lambda m: m.get("date", ""), reverse=True)[:n_games]
    if not recent:
        return None

    total_mins = sum(float(m.get("time", 0)) for m in recent)

    def _sum(field):
        return sum(float(m.get(field, 0) or 0) for m in recent)

    raw = {
        "goals":      _sum("goals"),
        "shots":      _sum("shots"),
        "xG":         _sum("xG"),
        "npxG":       _sum("npxG"),
        "xA":         _sum("xA"),
        "assists":    _sum("assists"),
        "key_passes": _sum("key_passes"),
        "xGChain":    _sum("xGChain"),
        "xGBuildup":  _sum("xGBuildup"),
        "minutes":    total_mins,
        "games":      len(recent),
    }

    p90 = {
        "xg":         _per90(raw["xG"],         total_mins),
        "npxg":       _per90(raw["npxG"],        total_mins),
        "xa":         _per90(raw["xA"],          total_mins),
        "shots":      _per90(raw["shots"],       total_mins),
        "key_passes": _per90(raw["key_passes"],  total_mins),
        "xg_chain":   _per90(raw["xGChain"],     total_mins),
        "xg_buildup": _per90(raw["xGBuildup"],   total_mins),
        "goals":      _per90(raw["goals"],       total_mins),
        "assists":    _per90(raw["assists"],     total_mins),
    }

    # Finishing quality: goals ÷ xG (only meaningful with enough chances)
    finishing_ratio = (raw["goals"] / raw["xG"]) if raw["xG"] >= 0.5 else 1.0

    # Shot detail stats (crosses, outside-box shots, box key passes)
    shot_history = None
    if player_name:
        try:
            shot_history = get_player_shot_history(int(player_id), player_name, n_games)
        except Exception:
            pass

    # Merge shot detail per90 into main per90 dict
    shot_p90 = (shot_history or {}).get("per90", {})
    p90["crosses_into_box"]      = shot_p90.get("crosses_into_box", 0.0)
    p90["box_key_passes_detail"] = shot_p90.get("box_key_passes", 0.0)
    p90["shots_outside_box"]     = shot_p90.get("shots_outside_box", 0.0)
    p90["big_chances_created"]   = shot_p90.get("big_chances_created", 0.0)
    p90["shots_inside_box"]      = shot_p90.get("shots_inside_box", 0.0)
    p90["headers_created"]       = shot_p90.get("headers_created", 0.0)

    form = {
        "raw":             raw,
        "per90":           p90,
        "finishing_ratio": finishing_ratio,
        "last_match":      recent[0] if recent else None,
        "matches":         recent,
        "shot_history":    shot_history,
    }

    _player_form_cache[cache_key] = (now, form)
    return form


# ── Bad-game detection ────────────────────────────────────────────────────────

def _is_bad_game(last_match: dict, group: str) -> bool:
    """
    Returns True if the player had a notably poor last game and should be
    flagged for potential replacement by their backup.
    Thresholds are intentionally conservative — we only flag clear blanks.
    """
    if not last_match:
        return False

    mins      = float(last_match.get("time", 0) or 0)
    xg        = float(last_match.get("xG", 0) or 0)
    xa        = float(last_match.get("xA", 0) or 0)
    kp        = float(last_match.get("key_passes", 0) or 0)
    shots     = float(last_match.get("shots", 0) or 0)
    xgchain   = float(last_match.get("xGChain", 0) or 0)
    xgbuildup = float(last_match.get("xGBuildup", 0) or 0)

    # Didn't play enough to form a view
    if mins < 45:
        return True

    if group == "F":
        # Striker completely anonymous: no shots AND tiny xG
        return shots == 0 and xg < 0.08

    if group == "M":
        # Midfielder: zero creativity AND very low chain involvement
        return kp == 0 and xgchain < 0.12

    if group == "D":
        # Defender: barely touched the ball in build-up
        return xgbuildup < 0.02 and xgchain < 0.04

    # GK: don't auto-flag — conceding goals isn't always the GK's fault
    return False


# ── Position scorers (0-100) ──────────────────────────────────────────────────

def _score_forward(form: dict) -> dict:
    p90 = form["per90"]
    B   = BENCHMARKS

    xg_score      = _clamp(p90["npxg"]              / B["npxg_elite"]  * 100)
    shot_score    = _clamp(p90["shots"]              / B["shots_elite"] * 100)
    finish_score  = _clamp(form["finishing_ratio"]   / 1.5              * 100)
    chain_score   = _clamp(p90["xg_chain"]           / B["chain_elite"] * 100)
    # Shots inside box preferred over range shots for strikers
    inside_score  = _clamp(p90.get("shots_inside_box", 0) / 3.0        * 100)
    # Outside-box shots = range threat (secondary bonus)
    outside_score = _clamp(p90.get("shots_outside_box", 0) / 1.5       * 100)

    # Weight inside-box shots if we have data, else fall back to plain shot volume
    has_detail = p90.get("shots_inside_box", 0) > 0 or p90.get("shots_outside_box", 0) > 0
    if has_detail:
        total = (
            xg_score     * 0.35 +
            inside_score * 0.22 +
            finish_score * 0.20 +
            chain_score  * 0.13 +
            outside_score * 0.10
        )
    else:
        total = (
            xg_score     * 0.35 +
            shot_score   * 0.25 +
            finish_score * 0.20 +
            chain_score  * 0.12 +
            _clamp(p90["key_passes"] / 1.0 * 100) * 0.08
        )

    metrics = {
        "npxG/90":            round(p90["npxg"], 3),
        "shots/90":           round(p90["shots"], 2),
        "shots_inside_box/90":round(p90.get("shots_inside_box", 0), 2),
        "shots_outside_box/90":round(p90.get("shots_outside_box", 0), 2),
        "finishing_ratio":    round(form["finishing_ratio"], 2),
    }
    return {
        "total":   round(total, 1),
        "label":   "Striker",
        "metrics": metrics,
        "breakdown": {
            "Chance quality (npxG)": round(xg_score, 1),
            "Inside-box shots":      round(inside_score, 1),
            "Finishing":             round(finish_score, 1),
            "Outside-box threat":    round(outside_score, 1),
            "Attack involvement":    round(chain_score, 1),
        },
    }


def _score_midfielder(form: dict, is_attacking: bool) -> dict:
    p90 = form["per90"]
    B   = BENCHMARKS

    kp_score      = _clamp(p90["key_passes"]  / B["kp_elite"]      * 100)
    xa_score      = _clamp(p90["xa"]          / B["xa_elite_m"]    * 100)
    buildup_score = _clamp(p90["xg_buildup"]  / B["buildup_elite"] * 100)
    chain_score   = _clamp(p90["xg_chain"]    / B["chain_elite"]   * 100)
    xg_score      = _clamp(p90["xg"]          / 0.25               * 100)
    # Box key passes: how many times does this player create shots inside the box?
    box_kp_score  = _clamp(p90.get("box_key_passes_detail", 0) / 1.2 * 100)
    # Crosses for wide mids / wingers
    cross_score   = _clamp(p90.get("crosses_into_box", 0) / 2.0    * 100)
    # Big chances created
    bcc_score     = _clamp(p90.get("big_chances_created", 0) / 0.5  * 100)

    has_detail = p90.get("box_key_passes_detail", 0) > 0 or p90.get("crosses_into_box", 0) > 0

    if is_attacking:
        if has_detail:
            total = (
                xa_score     * 0.22 +
                kp_score     * 0.18 +
                box_kp_score * 0.20 +
                cross_score  * 0.15 +
                bcc_score    * 0.15 +
                xg_score     * 0.10
            )
        else:
            total = (
                kp_score   * 0.30 +
                xa_score   * 0.30 +
                xg_score   * 0.20 +
                chain_score* 0.20
            )
        label = "Attacking Mid / Winger"
    else:
        total = (
            buildup_score * 0.35 +
            chain_score   * 0.25 +
            kp_score      * 0.25 +
            xa_score      * 0.15
        )
        label = "Central / Def. Mid"

    metrics = {
        "xA/90":              round(p90["xa"], 3),
        "key_passes/90":      round(p90["key_passes"], 2),
        "crosses_into_box/90":round(p90.get("crosses_into_box", 0), 3),
        "box_key_passes/90":  round(p90.get("box_key_passes_detail", 0), 3),
        "big_chances/90":     round(p90.get("big_chances_created", 0), 3),
        "xGBuildup/90":       round(p90["xg_buildup"], 3),
    }
    breakdown = {
        "Key passes":        round(kp_score, 1),
        "Chance creation":   round(xa_score, 1),
        "Box key passes":    round(box_kp_score, 1),
        "Crosses into box":  round(cross_score, 1),
        "Big chances":       round(bcc_score, 1),
        "Buildup play":      round(buildup_score, 1),
    }
    return {"total": round(total, 1), "label": label, "metrics": metrics, "breakdown": breakdown}


def _score_defender(form: dict, is_fullback: bool) -> dict:
    p90 = form["per90"]
    B   = BENCHMARKS

    buildup_score = _clamp(p90["xg_buildup"] / B["buildup_def"] * 100)
    chain_score   = _clamp(p90["xg_chain"]   / 0.35             * 100)

    if is_fullback:
        xa_score    = _clamp(p90["xa"]                           / B["xa_elite_fb"] * 100)
        kp_score    = _clamp(p90["key_passes"]                   / 1.5              * 100)
        cross_score = _clamp(p90.get("crosses_into_box", 0)      / 3.0              * 100)
        bcc_score   = _clamp(p90.get("big_chances_created", 0)   / 0.4              * 100)
        has_detail  = p90.get("crosses_into_box", 0) > 0

        if has_detail:
            total = (
                cross_score   * 0.35 +   # crosses into box is the #1 fullback offensive metric
                xa_score      * 0.25 +
                kp_score      * 0.15 +
                bcc_score     * 0.10 +
                buildup_score * 0.10 +
                chain_score   * 0.05
            )
        else:
            total = (
                xa_score      * 0.30 +
                kp_score      * 0.25 +
                buildup_score * 0.25 +
                chain_score   * 0.20
            )
        label = "Fullback"
        metrics = {
            "xA/90":              round(p90["xa"], 3),
            "crosses_into_box/90":round(p90.get("crosses_into_box", 0), 3),
            "key_passes/90":      round(p90["key_passes"], 2),
            "big_chances/90":     round(p90.get("big_chances_created", 0), 3),
            "xGBuildup/90":       round(p90["xg_buildup"], 3),
        }
    else:
        total = (
            buildup_score * 0.55 +
            chain_score   * 0.45
        )
        label = "Centre-Back"
        metrics = {
            "xGBuildup/90": round(p90["xg_buildup"], 3),
            "xGChain/90":   round(p90["xg_chain"], 3),
        }

    return {
        "total":   round(total, 1),
        "label":   label,
        "metrics": metrics,
        "breakdown": {
            "Buildup quality": round(buildup_score, 1),
            "Involvement":     round(chain_score, 1),
        },
    }


def _score_goalkeeper(
    form: dict,
    opponent_xg: float   = None,
    opponent_sot: float  = None,
    goals_conceded: float = None,
) -> dict:
    """
    GK score built from two components:
      1. Distribution quality  → xGBuildup per 90 (how well they start attacks)
      2. Save index            → simplified PSxG proxy using match-page xG data
    """
    p90 = form["per90"]
    distribution_score = _clamp(p90["xg_buildup"] / BENCHMARKS["gk_buildup"] * 100)

    save_score = 50.0  # neutral default when no shot data
    if opponent_xg is not None and opponent_sot is not None and opponent_sot > 0:
        # PSxG proxy: xG per shot-on-target × shots-on-target = expected goals saved difficulty
        # A GK facing 0.20 xG/SOT across 5 SOT (xG=1.0) and conceding 0 = +1.0 above expectation
        if goals_conceded is not None:
            saves_above_expected = opponent_xg - float(goals_conceded)
            # Scale: -2 to +2 saves above expected maps to 0-100
            save_score = _clamp((saves_above_expected + 2.0) / 4.0 * 100)

    total = distribution_score * 0.35 + save_score * 0.65

    return {
        "total":   round(total, 1),
        "label":   "Goalkeeper",
        "metrics": {
            "xGBuildup/90":       round(p90["xg_buildup"], 3),
            "opp_xg_faced":       round(opponent_xg, 2) if opponent_xg else None,
            "opp_sot_faced":      int(opponent_sot) if opponent_sot else None,
            "goals_conceded":     int(goals_conceded) if goals_conceded is not None else None,
        },
        "breakdown": {
            "Save index":     round(save_score, 1),
            "Distribution":   round(distribution_score, 1),
        },
    }


# ── Team lineup assessment ────────────────────────────────────────────────────

def assess_lineup(
    lineup: list[dict],        # [{name, position, ...}] from football-data.org
    bench: list[dict],         # [{name, position, ...}] — bench players for substitution
    team_name: str,
    competition_code: str,     # e.g. "PL"
    match_date: str,           # "YYYY-MM-DD"
    n_games: int = 5,
    # Optional: match-page xG data for GK scoring (from understat.get_match_xg)
    opponent_xg: float   = None,
    opponent_sot: float  = None,
    goals_conceded: float = None,
) -> dict:
    """
    Assess all players in a lineup using their last n_games form.
    Returns player-level scores + team composite efficiency.
    """
    config = LEAGUE_CONFIG.get(competition_code)
    if not config:
        return {"error": f"League {competition_code} not supported"}

    league_slug = config["slug"]
    season      = _season_label(match_date, config["end_year"])
    team_map    = _get_league_players(league_slug, season)

    # Build bench index for substitution lookup (position → list of bench players)
    bench_by_pos: dict[str, list] = {}
    for bp in (bench or []):
        pos = bp.get("position", "")
        bench_by_pos.setdefault(pos, []).append(bp)

    player_results  = []
    attack_scores   = []
    midfield_scores = []
    defense_scores  = []
    gk_score_val    = None

    for player in lineup:
        name     = player.get("name", "")
        position = player.get("position", "")
        pos_info = FD_POSITION_MAP.get(position)

        if not pos_info:
            continue

        group, is_attacking, is_fullback = pos_info

        # --- Understat lookup ---
        pid = _find_understat_id(name, team_name, team_map)

        if not pid:
            print(f"[Assessment] No Understat ID for {name} ({team_name})")
            continue

        form = _get_player_form(pid, n_games, player_name=name)
        if not form:
            continue

        # --- Bad game check → try bench substitute ---
        bad_game = _is_bad_game(form["last_match"], group)
        used_sub = None

        if bad_game:
            # Find first bench player of same position with Understat data
            for bp in bench_by_pos.get(position, []):
                sub_name = bp.get("name", "")
                sub_pid  = _find_understat_id(sub_name, team_name, team_map)
                if sub_pid:
                    sub_form = _get_player_form(sub_pid, n_games, player_name=sub_name)
                    if sub_form:
                        used_sub = sub_name
                        form     = sub_form
                        break

        # --- Score the player ---
        if group == "GK":
            score = _score_goalkeeper(form, opponent_xg, opponent_sot, goals_conceded)
            gk_score_val = score["total"]
        elif group == "F":
            score = _score_forward(form)
            attack_scores.append(score["total"])
        elif group == "M":
            score = _score_midfielder(form, is_attacking)
            if is_attacking:
                attack_scores.append(score["total"] * 0.65)
                midfield_scores.append(score["total"] * 0.35)
            else:
                midfield_scores.append(score["total"])
        elif group == "D":
            score = _score_defender(form, is_fullback)
            defense_scores.append(score["total"])

        player_results.append({
            "name":          name,
            "position":      position,
            "group":         group,
            "understat_id":  pid,
            "score":         score,
            "bad_last_game": bad_game,
            "sub_used":      used_sub,
            "per90":         form["per90"],
            "last_match_date": (form["last_match"] or {}).get("date"),
        })

    # ── Team composite scores ─────────────────────────────────────────────────
    def _avg(lst):
        return round(sum(lst) / len(lst), 1) if lst else 50.0

    attack_score   = _avg(attack_scores)
    midfield_score = _avg(midfield_scores)
    defense_score  = _avg(defense_scores)
    gk_score       = round(gk_score_val, 1) if gk_score_val is not None else 50.0

    team_efficiency = round(
        attack_score   * 0.35 +
        midfield_score * 0.25 +
        defense_score  * 0.25 +
        gk_score       * 0.15,
        1
    )

    bad_flags = [p["name"] for p in player_results if p["bad_last_game"]]

    return {
        "team":     team_name,
        "players":  player_results,
        "scores": {
            "attack":          attack_score,
            "midfield":        midfield_score,
            "defense":         defense_score,
            "goalkeeper":      gk_score,
            "team_efficiency": team_efficiency,
        },
        "bad_game_flags":   bad_flags,
        "players_assessed": len(player_results),
    }


# ── Formation profiler ────────────────────────────────────────────────────────

# Preset profiles for common formations
# attack_adj: score added to attack (positive = attacking intent)
# defense_adj: score added to defense
# wide_threat: high = team attacks down the wings with width
# intent: "attacking" | "balanced" | "defensive"
_FORMATION_PRESETS = {
    "3-4-3":   {"attack_adj":  14, "defense_adj": -10, "wide_threat": True,  "intent": "attacking",  "label": "Ultra-attacking — 3 forwards, wing-backs provide width"},
    "3-5-2":   {"attack_adj":   8, "defense_adj":   0, "wide_threat": True,  "intent": "attacking",  "label": "Attacking — 5-man midfield with two up top"},
    "3-4-2-1": {"attack_adj":   6, "defense_adj":   2, "wide_threat": True,  "intent": "attacking",  "label": "Wing-heavy 3-back — two 10s behind striker"},
    "4-3-3":   {"attack_adj":  10, "defense_adj":  -4, "wide_threat": True,  "intent": "attacking",  "label": "High press, wide attack — 3 forwards"},
    "4-2-3-1": {"attack_adj":   5, "defense_adj":   4, "wide_threat": False, "intent": "balanced",   "label": "Classic balanced — double pivot protects, #10 creates"},
    "4-3-2-1": {"attack_adj":   4, "defense_adj":   5, "wide_threat": False, "intent": "balanced",   "label": "Christmas tree — central overload, pocket players"},
    "4-4-2":   {"attack_adj":   2, "defense_adj":   2, "wide_threat": False, "intent": "balanced",   "label": "Classic direct — two strikers, compact midfield block"},
    "4-4-1-1": {"attack_adj":   0, "defense_adj":   6, "wide_threat": False, "intent": "balanced",   "label": "Compact shape — #10 links play, narrow"},
    "4-1-4-1": {"attack_adj":  -2, "defense_adj":   8, "wide_threat": False, "intent": "defensive",  "label": "Single pivot shield — lone striker, wide mids"},
    "4-5-1":   {"attack_adj": -10, "defense_adj":  14, "wide_threat": False, "intent": "defensive",  "label": "Defensive — packed midfield, lone striker on counter"},
    "5-3-2":   {"attack_adj":  -4, "defense_adj":  14, "wide_threat": True,  "intent": "defensive",  "label": "Back 5 with two up — wing-backs are the only width"},
    "5-4-1":   {"attack_adj": -14, "defense_adj":  18, "wide_threat": False, "intent": "defensive",  "label": "Park the bus — five defenders, one striker to hold"},
    "5-2-3":   {"attack_adj":   8, "defense_adj":   8, "wide_threat": True,  "intent": "balanced",   "label": "Wing-back system — solid base, wide forwards"},
    "4-1-2-3": {"attack_adj":  10, "defense_adj":   0, "wide_threat": True,  "intent": "attacking",  "label": "Attacking 4-1-2-3 variant — forward trio"},
    "3-6-1":   {"attack_adj":  -5, "defense_adj":  10, "wide_threat": False, "intent": "defensive",  "label": "Midfield overload — defensive emphasis"},
}

_DEFAULT_PROFILE = {"attack_adj": 0, "defense_adj": 0, "wide_threat": False, "intent": "balanced", "label": "Unknown formation"}


def profile_formation(formation: str | None) -> dict:
    """
    Parse a formation string (e.g. '4-3-3') into a tactical profile.
    Falls back to a generic parse if not in presets.
    """
    if not formation:
        return {**_DEFAULT_PROFILE, "formation": None}

    # Exact preset match
    if formation in _FORMATION_PRESETS:
        return {**_FORMATION_PRESETS[formation], "formation": formation}

    # Generic parse: count backs and forwards
    try:
        parts = [int(x) for x in formation.split("-") if x.isdigit()]
        if len(parts) < 2:
            return {**_DEFAULT_PROFILE, "formation": formation}

        backs    = parts[0]
        forwards = parts[-1]
        total    = sum(parts)

        if backs >= 5:
            adj_attack, adj_def, intent = -12, 16, "defensive"
            label = f"Back {backs} — ultra-defensive block"
        elif forwards >= 3 and backs <= 4:
            adj_attack, adj_def, intent = 10, -4, "attacking"
            label = f"{forwards} forwards — high attacking intent"
        elif forwards == 1 and backs == 4:
            adj_attack, adj_def, intent = -8, 12, "defensive"
            label = "Single striker — defensive, looking to counter"
        elif forwards == 2:
            adj_attack, adj_def, intent = 2, 2, "balanced"
            label = "Two up top — classic direct play"
        else:
            adj_attack, adj_def, intent = 0, 0, "balanced"
            label = "Balanced shape"

        return {
            "attack_adj":   adj_attack,
            "defense_adj":  adj_def,
            "wide_threat":  backs == 3,
            "intent":       intent,
            "label":        label,
            "formation":    formation,
        }
    except Exception:
        return {**_DEFAULT_PROFILE, "formation": formation}


def _formation_matchup_insight(
    home: str, away: str,
    home_profile: dict, away_profile: dict,
) -> list[str]:
    """
    Returns insight bullets about the tactical matchup between formations.
    """
    notes = []
    hi = home_profile.get("intent", "balanced")
    ai = away_profile.get("intent", "balanced")
    hw = home_profile.get("wide_threat", False)
    aw = away_profile.get("wide_threat", False)

    if hi == "attacking" and ai == "defensive":
        notes.append(
            f"{home} ({home_profile['formation']}) set up to attack — {away} ({away_profile['formation']}) "
            f"will look to absorb pressure and counter. Expect {home} to dominate possession but face a low block."
        )
    elif hi == "defensive" and ai == "attacking":
        notes.append(
            f"{away} ({away_profile['formation']}) will control the game — {home} ({home_profile['formation']}) "
            f"sitting deep invites pressure. Could be a one-sided attacking display if {away}'s quality is there."
        )
    elif hi == "attacking" and ai == "attacking":
        notes.append(
            f"Both sides set up to attack ({home_profile['formation']} vs {away_profile['formation']}) — "
            f"open game expected. Both defences could be exposed."
        )
    elif hi == "defensive" and ai == "defensive":
        notes.append(
            f"Tactical battle — {home} ({home_profile['formation']}) vs {away} ({away_profile['formation']}). "
            f"Both teams cautious, set pieces and individual moments could decide this."
        )

    if hw and not aw:
        notes.append(
            f"{home}'s {home_profile['formation']} uses width — wing-backs/wingers will exploit "
            f"{away}'s narrow {away_profile['formation']} shape."
        )
    elif aw and not hw:
        notes.append(
            f"{away}'s {away_profile['formation']} creates width — {home}'s narrow {home_profile['formation']} "
            f"could be pulled wide and leave space in behind."
        )

    return notes


# ── Streak + motivation adjusters ─────────────────────────────────────────────

def _streak_adj(context: dict | None) -> tuple[float, str]:
    """
    Returns (score_multiplier, label) based on winning/losing streak.
    """
    if not context:
        return 1.0, ""

    stype  = context.get("streak_type")
    scount = context.get("streak_count", 0)

    if stype == "W":
        if scount >= 15:
            return 1.30, f"Extraordinary {scount}-game winning streak — unstoppable momentum"
        if scount >= 10:
            return 1.20, f"Dominant {scount}-game winning run — confidence at peak"
        if scount >= 5:
            return 1.12, f"{scount}-game winning streak — strong momentum"
        if scount >= 3:
            return 1.06, f"{scount}-game winning run — building confidence"
    elif stype == "L":
        if scount >= 5:
            return 0.82, f"{scount}-game losing streak — confidence shattered"
        if scount >= 3:
            return 0.90, f"{scount}-game losing run — struggling for form"
    elif stype == "D":
        if scount >= 4:
            return 0.94, f"{scount} draws in a row — lacking winning mentality"

    return 1.0, ""


def _motivation_context_notes(
    home: str, away: str,
    home_ctx: dict | None, away_ctx: dict | None,
) -> list[str]:
    """
    Generate narrative bullets about motivation contrast.
    """
    notes = []
    if not home_ctx or not away_ctx:
        return notes

    hm = home_ctx.get("motivation", "standard")
    am = away_ctx.get("motivation", "standard")

    # Relegation fighter vs comfortable team
    if hm in ("relegation_fight", "relegation_threat") and am == "nothing_to_play_for":
        notes.append(
            f"{home} are fighting for survival (pos {home_ctx.get('position')}, "
            f"gap to safety: {home_ctx.get('gap_to_safety', '?')} pts) — "
            f"expect maximum intensity, every tackle like a cup final."
        )
        notes.append(
            f"{away} (pos {away_ctx.get('position')}) have little to play for — "
            f"motivation gap could be decisive."
        )
    elif am in ("relegation_fight", "relegation_threat") and hm == "nothing_to_play_for":
        notes.append(
            f"{away} are in a relegation battle (pos {away_ctx.get('position')}) — "
            f"desperation could produce an upset. Don't underestimate the 'fight for life' factor."
        )
        notes.append(
            f"{home} (pos {home_ctx.get('position')}) mid-table with nothing at stake — "
            f"could be flat and easy to beat."
        )
    elif hm in ("relegation_fight", "relegation_threat") and am in ("title_race", "top4_race"):
        notes.append(
            f"Classic 'big six vs bottom' matchup: {away} chasing the title/top 4 vs "
            f"{home} desperate for points. {home} will make it physical and direct — "
            f"classic lower-table setup to frustrate."
        )

    # Top-half team vs relegation fighter (general case)
    if am in ("relegation_fight", "relegation_threat") and hm not in ("relegation_fight", "relegation_threat", "nothing_to_play_for"):
        gap = away_ctx.get("gap_to_safety", "?")
        notes.append(
            f"{away} (pos {away_ctx.get('position')}) are in a relegation battle — "
            f"only {gap} pt(s) from the drop zone. Expect maximum effort, physicality and direct play."
        )
    if hm in ("relegation_fight", "relegation_threat") and am not in ("relegation_fight", "relegation_threat", "nothing_to_play_for"):
        gap = home_ctx.get("gap_to_safety", "?")
        notes.append(
            f"{home} (pos {home_ctx.get('position')}) are fighting for survival — "
            f"{gap} pt(s) from danger. Home crowd and desperation will make this a battle."
        )

    # Streak vs motivation mismatch
    h_streak = home_ctx.get("streak_label", "")
    a_streak = away_ctx.get("streak_label", "")
    if h_streak:
        notes.append(f"{home}: {h_streak}")
    if a_streak:
        notes.append(f"{away}: {a_streak}")

    return notes


# ── Match prediction ──────────────────────────────────────────────────────────

def _tier(score: float) -> str:
    if score >= 75:
        return "Elite"
    if score >= 60:
        return "Strong"
    if score >= 45:
        return "Average"
    return "Weak"


def predict_match(
    home_assessment: dict,
    away_assessment: dict,
    home_formation: str | None = None,
    away_formation: str | None = None,
    home_context: dict | None  = None,
    away_context: dict | None  = None,
) -> dict:
    """
    Generate bet signals from two team assessments, enriched with:
      - Formation profiles (attacking intent, defensive solidity, width)
      - Winning/losing streak multipliers
      - Motivation tier (relegation fighters, nothing-to-play-for, title race)
      - Formation matchup tactical insights
    """
    h_raw = home_assessment.get("scores", {})
    a_raw = away_assessment.get("scores", {})
    home  = home_assessment.get("team", "Home")
    away  = away_assessment.get("team", "Away")

    # ── Step 1: Formation adjustments ─────────────────────────────────────────
    h_form_profile = profile_formation(home_formation)
    a_form_profile = profile_formation(away_formation)

    h_atk = _clamp(h_raw.get("attack",   50) + h_form_profile["attack_adj"])
    h_def = _clamp(h_raw.get("defense",  50) + h_form_profile["defense_adj"])
    a_atk = _clamp(a_raw.get("attack",   50) + a_form_profile["attack_adj"])
    a_def = _clamp(a_raw.get("defense",  50) + a_form_profile["defense_adj"])
    h_mid = h_raw.get("midfield",  50)
    a_mid = a_raw.get("midfield",  50)
    h_gk  = h_raw.get("goalkeeper", 50)
    a_gk  = a_raw.get("goalkeeper", 50)

    # ── Step 2: Streak multiplier ──────────────────────────────────────────────
    h_streak_mult, h_streak_note = _streak_adj(home_context)
    a_streak_mult, a_streak_note = _streak_adj(away_context)

    # Apply streak to attack score (confidence + momentum)
    h_atk = _clamp(h_atk * h_streak_mult)
    a_atk = _clamp(a_atk * a_streak_mult)

    # ── Step 3: Motivation multiplier ─────────────────────────────────────────
    h_mot = (home_context or {}).get("motivation_factor", 1.0)
    a_mot = (away_context or {}).get("motivation_factor", 1.0)

    # Motivation primarily boosts pressing + defensive intensity (defense score)
    # and lowers it when a team has nothing to play for
    h_def = _clamp(h_def * h_mot)
    a_def = _clamp(a_def * a_mot)

    # Relegation teams also fight harder in attack (desperation → direct pressing attacks)
    h_mot_atk = min(h_mot, 1.15)  # cap attack motivation boost to avoid overinflation
    a_mot_atk = min(a_mot, 1.15)
    h_atk = _clamp(h_atk * h_mot_atk)
    a_atk = _clamp(a_atk * a_mot_atk)

    # ── Step 4: Re-compute effective team efficiency ───────────────────────────
    h_eff = round(h_atk * 0.35 + h_mid * 0.25 + h_def * 0.25 + h_gk * 0.15, 1)
    a_eff = round(a_atk * 0.35 + a_mid * 0.25 + a_def * 0.25 + a_gk * 0.15, 1)

    # ── Step 5: Contextual narrative notes ────────────────────────────────────
    tactical_notes = _formation_matchup_insight(home, away, h_form_profile, a_form_profile)
    motivation_notes = _motivation_context_notes(home, away, home_context, away_context)

    # Winning streak vs weak opposition amplifier
    h_pos = (home_context or {}).get("position")
    a_pos = (away_context or {}).get("position")
    h_motivation = (home_context or {}).get("motivation", "standard")
    a_motivation = (away_context or {}).get("motivation", "standard")

    # ── Step 6: Bet signals (using adjusted scores) ────────────────────────────
    signals = []
    combined_attack  = (h_atk + a_atk) / 2
    combined_defense = (h_def + a_def) / 2

    # Defensive formation penalty on goal signals
    h_defensive = h_form_profile.get("intent") == "defensive"
    a_defensive = a_form_profile.get("intent") == "defensive"

    # ── Home to score 2+ ──────────────────────────────────────────────────────
    h_score2_threshold = 68 if not a_defensive else 74  # harder vs parked bus
    if h_atk >= h_score2_threshold and a_def <= 50:
        streak_ctx  = h_streak_note or ""
        motivation_extra = ""
        if h_motivation in ("relegation_fight", "title_race"):
            motivation_extra = f" | {(home_context or {}).get('motivation_label','')}"
        if a_motivation == "nothing_to_play_for":
            motivation_extra += f" | {away} have nothing to fight for"

        conf = "High" if h_atk >= 80 and a_def <= 40 else "Medium"
        reason = [
            f"{home} attack {h_atk:.0f}/100 (formation-adjusted) — {_tier(h_atk)}",
            f"{away} defence {a_def:.0f}/100 — {_tier(a_def)}",
            f"Formation: {home} {h_form_profile['label']}",
        ]
        if streak_ctx:
            reason.append(streak_ctx)
        if motivation_extra:
            reason.append(motivation_extra.strip(" |"))
        signals.append({"bet": f"{home} to score 2+", "confidence": conf, "reason": reason})

    # ── Away to score 2+ ──────────────────────────────────────────────────────
    a_score2_threshold = 68 if not h_defensive else 74
    if a_atk >= a_score2_threshold and h_def <= 50:
        conf = "High" if a_atk >= 80 and h_def <= 40 else "Medium"
        reason = [
            f"{away} attack {a_atk:.0f}/100 (adjusted) — {_tier(a_atk)}",
            f"{home} defence {h_def:.0f}/100 — {_tier(h_def)}",
            f"Formation: {away} {a_form_profile['label']}",
        ]
        if a_streak_note:
            reason.append(a_streak_note)
        if h_motivation == "nothing_to_play_for":
            reason.append(f"{home} have nothing to fight for — low resistance expected")
        signals.append({"bet": f"{away} to score 2+", "confidence": conf, "reason": reason})

    # ── Both Teams to Score ───────────────────────────────────────────────────
    # Suppress if either team is ultra-defensive and the other is dominant
    btts_threshold = 55
    if h_atk >= btts_threshold and a_atk >= btts_threshold:
        # Reduce if either team is in full defensive mode
        if h_defensive or a_defensive:
            btts_threshold = 62  # harder to BTTS if one side parks the bus

        if h_atk >= btts_threshold and a_atk >= btts_threshold:
            conf = "High" if h_atk >= 65 and a_atk >= 65 else "Medium"
            reason = [
                f"{home} attack {h_atk:.0f}/100, {away} attack {a_atk:.0f}/100 — both in form",
                f"Formations: {home} {h_form_profile['intent']} vs {away} {a_form_profile['intent']}",
            ]
            if tactical_notes:
                reason.append(tactical_notes[0])
            signals.append({"bet": "Both Teams to Score", "confidence": conf, "reason": reason})

    # ── Over 2.5 goals ────────────────────────────────────────────────────────
    over25_penalty = 5 if (h_defensive or a_defensive) else 0
    if combined_attack >= (65 + over25_penalty) and combined_defense <= 52:
        conf = "High" if combined_attack >= 75 else "Medium"
        reason = [
            f"Adjusted attack average {combined_attack:.0f}/100",
            f"Adjusted defence average {combined_defense:.0f}/100",
        ]
        if h_form_profile["intent"] == "attacking" and a_form_profile["intent"] == "attacking":
            reason.append("Both teams set up to attack — open game expected")
        signals.append({"bet": "Over 2.5 Goals", "confidence": conf, "reason": reason})

    # ── Over 3.5 goals ────────────────────────────────────────────────────────
    if combined_attack >= 76 and combined_defense <= 40 and not h_defensive and not a_defensive:
        signals.append({
            "bet": "Over 3.5 Goals",
            "confidence": "Medium",
            "reason": [
                f"Elite combined attack {combined_attack:.0f}/100 — both teams attacking",
                f"Both defences exposed {combined_defense:.0f}/100",
                "Neither side in defensive formation",
            ],
        })

    # ── Clean sheet ───────────────────────────────────────────────────────────
    if h_def >= 72 and h_gk >= 62 and a_atk <= 42:
        boost_note = ""
        if h_motivation in ("relegation_fight", "top4_race", "title_race"):
            boost_note = f"{home} fighting for {(home_context or {}).get('motivation_label','their season')} — defensive resilience elevated"
        reason = [f"{home} defence {h_def:.0f}/100, GK {h_gk}/100", f"{away} attack only {a_atk:.0f}/100"]
        if h_form_profile["intent"] == "defensive":
            reason.append(f"Formation {h_form_profile['formation']} — set up to be hard to break down")
        if boost_note:
            reason.append(boost_note)
        signals.append({"bet": f"{home} Clean Sheet", "confidence": "Medium", "reason": reason})

    if a_def >= 72 and a_gk >= 62 and h_atk <= 42:
        reason = [f"{away} defence {a_def:.0f}/100, GK {a_gk}/100", f"{home} attack only {h_atk:.0f}/100"]
        if a_form_profile["intent"] == "defensive":
            reason.append(f"Formation {a_form_profile['formation']} — organised low block")
        signals.append({"bet": f"{away} Clean Sheet", "confidence": "Medium", "reason": reason})

    # ── Under 1.5 goals ───────────────────────────────────────────────────────
    under_boost = (h_defensive and a_defensive)
    under_threshold_atk = 35 if not under_boost else 45
    under_threshold_def = 68 if not under_boost else 60
    if combined_attack <= under_threshold_atk and combined_defense >= under_threshold_def:
        reason = [
            f"Both attacks misfiring: {combined_attack:.0f}/100 average",
            f"Solid defences on both sides: {combined_defense:.0f}/100",
        ]
        if h_defensive and a_defensive:
            reason.append(
                f"Formations confirm caution: {home} {h_form_profile['formation']} vs {away} {a_form_profile['formation']}"
            )
        signals.append({"bet": "Under 1.5 Goals", "confidence": "Medium" if under_boost else "Low", "reason": reason})

    # ── Relegation upset warning ───────────────────────────────────────────────
    # Flag when a mid-table/top team might be caught cold by a desperate relegation side
    if (a_motivation == "relegation_fight" and h_motivation == "nothing_to_play_for"
            and a_atk >= 50 and h_def <= 55):
        signals.append({
            "bet": f"Warning: {away} upset possible",
            "confidence": "Low",
            "reason": [
                f"{away} (pos {a_pos}) are fighting relegation — expect maximum effort",
                f"{home} (pos {h_pos}) have little at stake — risk of flat performance",
                "Relegation six-pointers are historically unpredictable",
            ],
        })
    if (h_motivation == "relegation_fight" and a_motivation == "nothing_to_play_for"
            and h_atk >= 50 and a_def <= 55):
        signals.append({
            "bet": f"Warning: {home} comeback/upset possible",
            "confidence": "Low",
            "reason": [
                f"{home} (pos {h_pos}) desperate for points — intensity will be high",
                f"{away} (pos {a_pos}) comfort zone — motivation gap is real",
                "Never underestimate a team fighting for survival at home",
            ],
        })

    return {
        "signals":     signals,
        "tactical":    tactical_notes + motivation_notes,
        "formations": {
            "home": h_form_profile,
            "away": a_form_profile,
        },
        "adjusted_scores": {
            "home_attack":    round(h_atk, 1),
            "away_attack":    round(a_atk, 1),
            "home_defense":   round(h_def, 1),
            "away_defense":   round(a_def, 1),
            "home_efficiency": h_eff,
            "away_efficiency": a_eff,
        },
        "context": {
            "home_motivation":  (home_context or {}).get("motivation_label"),
            "away_motivation":  (away_context or {}).get("motivation_label"),
            "home_streak":      (home_context or {}).get("streak_label"),
            "away_streak":      (away_context or {}).get("streak_label"),
            "home_position":    h_pos,
            "away_position":    a_pos,
        },
    }
