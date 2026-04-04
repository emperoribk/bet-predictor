"""
Daily best-bets engine.

Architecture
============
1. get_team_season_stats()   (football_data.py) — standings + Understat + yellow cards
2. _calc_*_confidence()      — one function per bet type, returns a confidence dict
3. get_all_bet_signals()     — runs all calculators for one fixture
4. get_todays_best_bets()    — fetches today's SCHEDULED fixtures, scores every bet type,
                               returns the top predictions sorted by confidence

Confidence model
================
Each calculator starts from a baseline (50 = coin flip) and adds/subtracts evidence
points.  Evidence is sourced from:
  - Understat season xG per game (attack vs defence quality)
  - Historical BTTS / over-2.5 / clean-sheet rates
  - Home/away venue splits
  - Yellow-card averages
  - Motivation & form tier from get_team_context()

All scores are clamped to [35, 93].
"""

import math
import requests
from datetime import datetime
from django.conf import settings

from .football_data import get_team_season_stats, get_team_context, _get_standings, get_h2h_scored
from .bookie_map import enrich_signal
from .grading import grade_all_signals
from . import api_football as _api_football_module

# Leagues covered by Understat (real xG data — reliable even without API-Football)
_UNDERSTAT_LEAGUES = {"PL", "PD", "BL1", "SA", "FL1"}

# ── League-specific baselines ─────────────────────────────────────────────────
# Derived from 2025/26 actual results (200-467 games per league).
# Used as the neutral point when computing Over/BTTS signals — a team AT the
# league average should produce a score near 50 (coin-flip), not a false lean.

# Over 2.5 baseline — what fraction of games go Over 2.5 in this league
_LEAGUE_OVER25_BASE: dict[str, float] = {
    "CL":   0.60,   # Champions League — 60.4% (268 games)
    "DED":  0.63,   # Eredivisie — 62.7% (252 games)  high-scoring Dutch football
    "SSL":  0.67,   # Swiss Super League — 66.7% (186 games)  highest in dataset
    "BL1":  0.62,   # Bundesliga — 62.1% (243 games)
    "BL2":  0.59,   # 2. Bundesliga — 59.3% (243 games)
    "PPL":  0.56,   # Primeira Liga — 55.6% (241 games)
    "UECL": 0.54,   # UEFA Conference League — 53.8% (396 games)
    "PL":   0.53,   # Premier League — 53.4% (309 games)
    "EL":   0.52,   # UEFA Europa League — 51.9% (258 games)
    "PD":   0.51,   # La Liga — 51.0% (290 games)
    "FL1":  0.51,   # Ligue 1 — 50.8% (242 games)
    "ELC":  0.50,   # Championship — 50.1% (467 games)
    "TSL":  0.50,   # Turkish Süper Lig — 50.2% (241 games)
    "SPL":  0.50,   # Scottish Premiership — 49.5% (186 games)
    "BJL":  0.47,   # Belgian First A — 46.9% (241 games)  low-scoring
    "SA":   0.47,   # Serie A — 46.7% (300 games)  lowest Big 5
}

# Over 1.5 baseline
_LEAGUE_OVER15_BASE: dict[str, float] = {
    "CL":   0.79,
    "DED":  0.86,
    "SSL":  0.85,
    "BL1":  0.82,
    "BL2":  0.80,
    "PPL":  0.76,
    "PL":   0.79,
    "UECL": 0.76,
    "EL":   0.72,
    "PD":   0.79,
    "FL1":  0.70,   # Ligue 1 — 69.8% surprisingly low
    "ELC":  0.75,
    "TSL":  0.74,
    "SPL":  0.76,
    "BJL":  0.74,
    "SA":   0.71,   # Serie A — 70.7%
}

# BTTS baseline
_LEAGUE_BTTS_BASE: dict[str, float] = {
    "CL":   0.52,
    "DED":  0.64,
    "SSL":  0.68,
    "BL1":  0.58,
    "BL2":  0.60,
    "PPL":  0.47,
    "PL":   0.56,
    "UECL": 0.49,
    "EL":   0.49,
    "PD":   0.57,
    "FL1":  0.48,
    "ELC":  0.56,
    "TSL":  0.54,
    "SPL":  0.50,
    "BJL":  0.54,
    "SA":   0.47,   # Serie A — lowest BTTS rate in Big 5
}

_DEFAULT_OVER25_BASE = 0.51   # fallback for unlisted leagues
_DEFAULT_OVER15_BASE = 0.75
_DEFAULT_BTTS_BASE   = 0.52


# ─────────────────────────────────────────────────────────────────────────────
# Poisson utilities
# ─────────────────────────────────────────────────────────────────────────────

def _poisson(lam: float, k: int) -> float:
    """P(X = k) for Poisson(λ)."""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    try:
        return math.exp(-lam) * (lam ** k) / math.factorial(k)
    except (OverflowError, ValueError):
        return 0.0


def _match_probs(home_xg: float, away_xg: float, max_goals: int = 9) -> tuple[float, float, float]:
    """
    Returns (p_home_win, p_draw, p_away_win) using independent Poisson model.
    home_xg / away_xg are the expected goals for each side in this specific match.
    """
    p_home = p_draw = p_away = 0.0
    for h in range(max_goals):
        for a in range(max_goals):
            p = _poisson(home_xg, h) * _poisson(away_xg, a)
            if h > a:
                p_home += p
            elif h == a:
                p_draw += p
            else:
                p_away += p
    return p_home, p_draw, p_away


def _score_probs(home_xg: float, away_xg: float, max_goals: int = 6) -> dict[tuple, float]:
    """Returns {(h_goals, a_goals): probability} for all scorelines up to max_goals."""
    scores = {}
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            scores[(h, a)] = _poisson(home_xg, h) * _poisson(away_xg, a)
    return scores


def _expected_xg_for_match(home_stats: dict, away_stats: dict) -> tuple[float, float]:
    """
    Returns (home_expected_xg, away_expected_xg) for this specific fixture.
    Blends each team's own attack xG (60%) with the opponent's defensive xGA (40%)
    so strong defences dampen attacking xG estimates — not just raw offensive output.
    """
    h_xg = home_stats.get("home_xg_pg") or home_stats.get("xg_per_game") or 0.0
    a_xg = away_stats.get("away_xg_pg") or away_stats.get("xg_per_game") or 0.0

    # Opponent defensive quality — how many xG they concede at the relevant venue
    away_def = away_stats.get("away_xga_pg") or away_stats.get("xga_per_game") or 0.0
    home_def = home_stats.get("home_xga_pg") or home_stats.get("xga_per_game") or 0.0

    # Blend: 60% team attack, 40% opponent defensive leakiness
    if away_def > 0:
        h_xg = h_xg * 0.60 + away_def * 0.40
    if home_def > 0:
        a_xg = a_xg * 0.60 + home_def * 0.40

    # If we only have season averages (no venue split), apply small home boost
    if not home_stats.get("home_xg_pg") and h_xg:
        h_xg *= 1.05
    if not away_stats.get("away_xg_pg") and a_xg:
        a_xg *= 0.95

    return h_xg, a_xg

BASE_URL = "https://api.football-data.org/v4"


def _headers():
    return {"X-Auth-Token": settings.FOOTBALL_DATA_API_KEY}


# ─────────────────────────────────────────────────────────────────────────────
# Confidence calculators
# Each returns:
#   {
#     "type":       str,      # e.g. "BTTS_YES"
#     "label":      str,      # human-readable label
#     "confidence": int,      # 0-100
#     "signal":     str,      # "yes" | "no" | "lean_yes" | "lean_no"
#     "evidence":   [str],    # bullet-point reasons
#   }
# ─────────────────────────────────────────────────────────────────────────────

def _clamp(v: float) -> int:
    return max(35, min(93, int(round(v))))


def _calc_btts(home: dict, away: dict) -> dict:
    """Both Teams to Score — YES."""
    evidence = []
    score = 50.0

    # League-specific neutral point — a team AT the league average for BTTS should
    # score near 50. Without this, low-BTTS leagues (SA, FL1) read as slightly negative
    # even when both teams are perfectly average for their environment.
    _league = home.get("competition_code") or away.get("competition_code") or ""
    _btts_base = _LEAGUE_BTTS_BASE.get(_league, _DEFAULT_BTTS_BASE)

    # Each team's historical BTTS rate (most reliable single predictor)
    # Use geometric mean — punishes low rates on either side much harder than a simple average.
    # e.g. 80% + 40% → geometric mean 57% vs arithmetic 60%; 65% + 45% → 54% vs 55%.
    # Also apply a hard penalty when either team's rate is below 50% (weak link rule).
    h_btts = home.get("btts_rate") or 0.0
    a_btts = away.get("btts_rate") or 0.0

    if h_btts and a_btts:
        import math
        geo_btts = math.sqrt(h_btts * a_btts)
        score += (geo_btts - _btts_base) * 60
        # Hard penalty: if either team rarely participates in BTTS games
        weak_link = min(h_btts, a_btts)
        if weak_link < _btts_base - 0.05:
            score -= 12
        elif weak_link < _btts_base:
            score -= 6
        evidence.append(
            f"Historical BTTS rates: {home['team_name']} {h_btts:.0%}, "
            f"{away['team_name']} {a_btts:.0%} (combined {geo_btts:.0%})"
        )
    elif h_btts:
        score += (h_btts - _btts_base) * 30
    elif a_btts:
        score += (a_btts - _btts_base) * 30

    # Away team's ability to score (their attack away from home)
    a_away_xg = away.get("away_xg_pg") or away.get("xg_per_game") or 0.0
    if a_away_xg >= 1.2:
        score += 8
        evidence.append(f"{away['team_name']} create {a_away_xg:.2f} xG/game away — dangerous attack")
    elif a_away_xg >= 0.9:
        score += 4
    elif a_away_xg < 0.6 and a_away_xg > 0:
        score -= 6
        evidence.append(f"{away['team_name']} average only {a_away_xg:.2f} xG/game away — low threat")

    # Home team's defensive solidity
    h_home_xga = home.get("home_xga_pg") or home.get("xga_per_game") or 0.0
    if h_home_xga >= 1.2:
        score += 6
        evidence.append(f"{home['team_name']} concede {h_home_xga:.2f} xGA/game at home — leaky defence")
    elif h_home_xga < 0.7 and h_home_xga > 0:
        score -= 6
        evidence.append(f"{home['team_name']} concede only {h_home_xga:.2f} xGA/game at home — strong defence")

    # Home team's clean sheet rate (high CS rate → BTTS unlikely)
    h_cs = home.get("home_cs_rate") or home.get("clean_sheet_rate") or 0.0
    if h_cs >= 0.45:
        score -= 8
        evidence.append(f"{home['team_name']} keep {h_cs:.0%} clean sheets at home")
    elif h_cs >= 0.35:
        score -= 3

    # Home team attack
    h_home_xg = home.get("home_xg_pg") or home.get("xg_per_game") or 0.0
    if h_home_xg >= 1.5:
        score += 5
        evidence.append(f"{home['team_name']} generate {h_home_xg:.2f} xG/game at home")
    elif h_home_xg < 0.8 and h_home_xg > 0:
        score -= 5
        evidence.append(f"{home['team_name']} create only {h_home_xg:.2f} xG/game at home — quiet attack")

    # xG overperformance regression penalty (same logic as _calc_over25)
    for _stats in [home, away]:
        _ratio = _stats.get("xg_overperform_ratio")
        if _ratio and _ratio > 1.25:
            _pen = min(7, round((_ratio - 1.25) * 28))
            score -= _pen
            evidence.append(
                f"{_stats['team_name']} scoring {_ratio:.2f}x their xG — BTTS less reliable, "
                f"goals likely to drop (-{_pen})"
            )

    # ── Opponent-adjusted scoring probability ─────────────────────────────
    # Core of Fix 3: asks "given THIS opponent's defence, can each team
    # realistically score?" rather than looking at attack xG in isolation.
    #
    # adj_xg = team_attack_xg × (opponent_xga_conceded / league_baseline)
    #
    # Example: Wolfsburg score 1.2 xG/game but face Bayern who concede 0.7 xGA
    # → adj_xg = 1.2 × (0.7 / 1.35) = 0.62 → significant clean-sheet risk
    # Example: Bayern vs Barcelona (1.1 xGA) → adj = 1.2 × (1.1/1.35) = 0.98 → fine
    _LEAGUE_XG_BASELINE = 1.35

    a_away_xga = away.get("away_xga_pg") or away.get("xga_per_game") or 0.0

    adj_home_xg = h_home_xg * (a_away_xga / _LEAGUE_XG_BASELINE) if h_home_xg and a_away_xga else h_home_xg
    adj_away_xg = a_away_xg * (h_home_xga / _LEAGUE_XG_BASELINE) if a_away_xg and h_home_xga else a_away_xg

    if adj_home_xg > 0 and adj_away_xg > 0:
        weaker_adj = min(adj_home_xg, adj_away_xg)
        weaker_name = home["team_name"] if adj_home_xg <= adj_away_xg else away["team_name"]
        if weaker_adj < 0.65:
            penalty = min(20, round((0.65 - weaker_adj) / 0.65 * 22))
            score -= penalty
            evidence.append(
                f"Quality gap: {weaker_name} only {weaker_adj:.2f} adj-xG vs this defence "
                f"— clean sheet risk (-{penalty})"
            )
        elif weaker_adj < 0.85:
            score -= 6
            evidence.append(
                f"Quality gap: {weaker_name} only {weaker_adj:.2f} adj-xG vs this defence "
                f"— scoring concern (-6)"
            )

    # Opponent recent defensive form — if either side is on a clean-sheet run,
    # BTTS becomes much less likely regardless of the attacker's overall stats.
    away_cs3 = away.get("cs_last3")   # away team defending against home scorer
    home_cs3 = home.get("cs_last3")   # home team defending against away scorer

    if away_cs3 is not None:
        if away_cs3 == 3:
            score -= 16
            evidence.append(f"{away['team_name']} kept clean sheets in all 3 recent games — shutting teams out")
        elif away_cs3 == 2:
            score -= 9
            evidence.append(f"{away['team_name']} kept {away_cs3} clean sheets in last 3 — defence in form")

    if home_cs3 is not None:
        if home_cs3 == 3:
            score -= 16
            evidence.append(f"{home['team_name']} kept clean sheets in all 3 recent games — shutting teams out")
        elif home_cs3 == 2:
            score -= 9
            evidence.append(f"{home['team_name']} kept {home_cs3} clean sheets in last 3 — defence in form")

    conf = _clamp(score)
    signal = "yes" if conf >= 60 else ("lean_yes" if conf >= 53 else ("lean_no" if conf >= 47 else "no"))

    return {
        "type": "BTTS_YES",
        "label": "Both Teams to Score",
        "confidence": conf,
        "signal": signal,
        "evidence": evidence,
    }


def _calc_over25(home: dict, away: dict) -> dict:
    """Total Goals Over 2.5."""
    evidence = []
    score = 50.0

    # League-specific neutral point — in BL1/DED a 62% Over 2.5 rate is average,
    # not a positive signal; in SA a 47% rate is average, not a negative signal.
    _league = home.get("competition_code") or away.get("competition_code") or ""
    _o25_base = _LEAGUE_OVER25_BASE.get(_league, _DEFAULT_OVER25_BASE)

    # Historical over-2.5 rates
    h_o25 = home.get("over25_rate") or 0.0
    a_o25 = away.get("over25_rate") or 0.0

    if h_o25 and a_o25:
        avg = (h_o25 + a_o25) / 2
        score += (avg - _o25_base) * 70
        evidence.append(
            f"Over 2.5 rates: {home['team_name']} {h_o25:.0%}, "
            f"{away['team_name']} {a_o25:.0%} (league base {_o25_base:.0%})"
        )

    # Expected goals: use fixture-adjusted xG (blends attack with opponent defence)
    # Previously used raw offensive xG only, ignoring tight defences entirely.
    h_xg, a_xg = _expected_xg_for_match(home, away)
    expected_total = h_xg + a_xg

    if expected_total > 0:
        # Expected xG directly vs the 2.5 line
        xg_diff = expected_total - 2.5
        score += xg_diff * 12   # +12 per goal above line
        evidence.append(f"Expected xG total: {expected_total:.2f} (home {h_xg:.2f} + away {a_xg:.2f})")

    # High-scoring tendencies
    h_scored = home.get("scored_per_game") or 0.0
    a_scored = away.get("scored_per_game") or 0.0
    avg_goals = (h_scored + a_scored)
    if avg_goals > 0 and expected_total == 0:
        score += (avg_goals - 2.5) * 10

    # xG overperformance regression penalty
    # A team consistently scoring far above their xG is likely to revert to mean.
    # Their goals_for_pg looks high but xG (real chance quality) says it won't last.
    # Only penalise when ratio > 1.25 (25% above xG) to ignore small variance.
    for _stats in [home, away]:
        _ratio = _stats.get("xg_overperform_ratio")
        if _ratio and _ratio > 1.25:
            _pen = min(8, round((_ratio - 1.25) * 32))
            score -= _pen
            evidence.append(
                f"{_stats['team_name']} scoring {_ratio:.2f}x their xG — regression risk, "
                f"goals likely to drop back (-{_pen})"
            )

    # Defensive mismatch penalty: one team rarely plays in high-scoring games
    # AND their own xG is low — they keep games tight regardless of the opponent.
    # Only fires when weak team's rate < 50% AND xG < 1.8 (Famalicão pattern).
    # Does NOT fire when the weak-rate team still generates high xG or faces
    # a dominant attacker that overwhelms their tendency (Charleroi vs Brugge).
    if h_o25 and a_o25:
        weak_rate = min(h_o25, a_o25)
        weak_xg = h_xg if h_o25 < a_o25 else a_xg
        weak_name = home["team_name"] if h_o25 < a_o25 else away["team_name"]
        if weak_rate < _o25_base and weak_xg < 1.8 and weak_xg > 0:
            penalty = round((_o25_base - weak_rate) * 60)
            score -= penalty
            evidence.append(
                f"{weak_name} only {weak_rate:.0%} over-2.5 rate with {weak_xg:.2f} xG "
                f"— keeps games tight (-{penalty})"
            )

    # Elite away team game management penalty (larger impact for 3+ goals)
    # Same dual-condition logic as Over 1.5 but penalty is larger — needing
    # 3 goals when a dominant away team is managing a 1-0 is even less likely.
    _away_pos  = away.get("position")
    _home_pos  = home.get("position")
    _away_cs_a = away.get("away_cs_rate") or away.get("clean_sheet_rate") or 0.0
    _h_xg_raw  = home.get("home_xg_pg") or home.get("xg_per_game") or 0.0
    _a_xg_raw  = away.get("away_xg_pg") or away.get("xg_per_game") or 0.0
    _pos_gap   = ((_home_pos or 0) - (_away_pos or 0)) if (_away_pos and _home_pos) else 0
    _pos_condition = bool(_away_pos and _away_pos <= 4 and _pos_gap >= 5)
    _xg_condition  = bool(_a_xg_raw > 0 and _h_xg_raw > 0 and _a_xg_raw >= _h_xg_raw * 1.6)
    _cs_threshold = 0.35 if _pos_condition else 0.45
    if (_pos_condition or _xg_condition) and _away_cs_a >= _cs_threshold:
        _gap_label = f"pos gap {_pos_gap}" if _pos_condition else f"xG gap {_a_xg_raw:.2f} vs {_h_xg_raw:.2f}"
        _mgmt_penalty = min(25, round((_away_cs_a - 0.25) * 100 + 8))
        score -= _mgmt_penalty
        evidence.append(
            f"{away.get('team_name')} dominant away ({_gap_label}, {_away_cs_a:.0%} away CS) "
            f"— likely 1-0 game management (-{_mgmt_penalty})"
        )

    # La Liga penalty dependency note
    # 11.4% of La Liga goals come from penalties — highest in the Big 5.
    # When neither team has a proven penalty specialist, Over 2.5 is harder
    # to reach because this goal route is less reliable.
    if _league == "PD":
        evidence.append(
            "La Liga context: 11.4% of goals from penalties (Big 5 highest) "
            "— penalty award volatility adds uncertainty to goal totals"
        )

    conf = _clamp(score)
    signal = "yes" if conf >= 60 else ("lean_yes" if conf >= 53 else ("lean_no" if conf >= 47 else "no"))

    return {
        "type": "OVER_25",
        "label": "Over 2.5 Goals",
        "confidence": conf,
        "signal": signal,
        "evidence": evidence,
    }


def _calc_over35(home: dict, away: dict) -> dict:
    """Total Goals Over 3.5."""
    evidence = []
    score = 50.0

    h_o35 = home.get("over35_rate") or 0.0
    a_o35 = away.get("over35_rate") or 0.0

    if h_o35 and a_o35:
        avg = (h_o35 + a_o35) / 2
        score += (avg - 0.35) * 80   # baseline is ~35% not 50%
        evidence.append(
            f"Over 3.5 rates: {home['team_name']} {h_o35:.0%}, "
            f"{away['team_name']} {a_o35:.0%}"
        )

    h_xg, a_xg = _expected_xg_for_match(home, away)
    expected_total = h_xg + a_xg

    if expected_total > 0:
        xg_diff = expected_total - 3.5
        score += xg_diff * 14
        evidence.append(f"Expected xG total: {expected_total:.2f} vs 3.5 line")

    conf = _clamp(score)
    signal = "yes" if conf >= 60 else ("lean_yes" if conf >= 53 else ("lean_no" if conf >= 47 else "no"))

    return {
        "type": "OVER_35",
        "label": "Over 3.5 Goals",
        "confidence": conf,
        "signal": signal,
        "evidence": evidence,
    }


def _calc_under15(home: dict, away: dict) -> dict:
    """Total Goals Under 1.5 — a defensive match."""
    evidence = []
    score = 50.0

    # Low-scoring tendencies
    h_xg  = home.get("home_xg_pg") or home.get("xg_per_game") or 0.0
    a_xg  = away.get("away_xg_pg") or away.get("xg_per_game") or 0.0
    total = h_xg + a_xg

    if total > 0:
        diff = 1.5 - total   # positive when xG is below 1.5
        score += diff * 15
        evidence.append(f"Expected xG total: {total:.2f} (low-scoring environment)")

    h_cs  = home.get("home_cs_rate") or home.get("clean_sheet_rate") or 0.0
    a_cs  = away.get("away_cs_rate") or away.get("clean_sheet_rate") or 0.0
    if h_cs >= 0.40:
        score += 8
        evidence.append(f"{home['team_name']} {h_cs:.0%} clean sheets at home")
    if a_cs >= 0.40:
        score += 5
        evidence.append(f"{away['team_name']} {a_cs:.0%} clean sheets away")

    h_o25 = home.get("over25_rate") or 0.0
    a_o25 = away.get("over25_rate") or 0.0
    if h_o25 and a_o25:
        avg_o25 = (h_o25 + a_o25) / 2
        score -= (avg_o25 - 0.35) * 50   # high-scoring teams hurt this bet

    conf = _clamp(score)
    signal = "yes" if conf >= 60 else ("lean_yes" if conf >= 53 else ("lean_no" if conf >= 47 else "no"))

    return {
        "type": "UNDER_15",
        "label": "Under 1.5 Goals",
        "confidence": conf,
        "signal": signal,
        "evidence": evidence,
    }


def _calc_home_clean_sheet(home: dict, away: dict) -> dict:
    """Home team keeps a clean sheet."""
    evidence = []
    score = 50.0

    h_cs = home.get("home_cs_rate") or home.get("clean_sheet_rate") or 0.0
    if h_cs:
        score += (h_cs - 0.30) * 80
        evidence.append(f"{home['team_name']} clean sheet rate at home: {h_cs:.0%}")

    # Away team's attacking threat
    a_xg = away.get("away_xg_pg") or away.get("xg_per_game") or 0.0
    if a_xg > 0:
        score -= (a_xg - 0.8) * 15
        evidence.append(f"{away['team_name']} create {a_xg:.2f} xG/game away")

    a_btts = away.get("btts_rate") or 0.0
    if a_btts >= 0.65:
        score -= 8
        evidence.append(f"{away['team_name']} score in {a_btts:.0%} of games — regularly get on the board")

    conf = _clamp(score)
    signal = "yes" if conf >= 60 else ("lean_yes" if conf >= 53 else ("lean_no" if conf >= 47 else "no"))

    return {
        "type": "HOME_CLEAN_SHEET",
        "label": f"{home['team_name']} Clean Sheet",
        "confidence": conf,
        "signal": signal,
        "evidence": evidence,
    }


def _calc_away_clean_sheet(home: dict, away: dict) -> dict:
    """Away team keeps a clean sheet."""
    evidence = []
    score = 50.0

    a_cs = away.get("away_cs_rate") or away.get("clean_sheet_rate") or 0.0
    if a_cs:
        score += (a_cs - 0.25) * 80
        evidence.append(f"{away['team_name']} clean sheet rate away: {a_cs:.0%}")

    h_xg = home.get("home_xg_pg") or home.get("xg_per_game") or 0.0
    if h_xg > 0:
        score -= (h_xg - 1.0) * 12
        evidence.append(f"{home['team_name']} create {h_xg:.2f} xG/game at home")

    h_btts = home.get("btts_rate") or 0.0
    if h_btts >= 0.65:
        score -= 8
        evidence.append(f"{home['team_name']} score in {h_btts:.0%} of their matches")

    conf = _clamp(score)
    signal = "yes" if conf >= 60 else ("lean_yes" if conf >= 53 else ("lean_no" if conf >= 47 else "no"))

    return {
        "type": "AWAY_CLEAN_SHEET",
        "label": f"{away['team_name']} Clean Sheet",
        "confidence": conf,
        "signal": signal,
        "evidence": evidence,
    }


def _calc_home_score_2plus(home: dict, away: dict) -> dict:
    """Home team scores 2 or more goals."""
    evidence = []
    score = 50.0

    h_xg  = home.get("home_xg_pg") or home.get("xg_per_game") or 0.0
    a_xga = away.get("away_xga_pg") or away.get("xga_per_game") or 0.0

    if h_xg > 0:
        score += (h_xg - 1.2) * 20
        evidence.append(f"{home['team_name']} generate {h_xg:.2f} xG/game at home")
    if a_xga > 0:
        score += (a_xga - 1.0) * 12
        evidence.append(f"{away['team_name']} concede {a_xga:.2f} xGA/game away — poor defensive record")

    h_scored = home.get("scored_per_game") or 0.0
    if h_scored >= 1.8:
        score += 8
        evidence.append(f"{home['team_name']} average {h_scored:.2f} goals/game this season")

    conf = _clamp(score)
    signal = "yes" if conf >= 60 else ("lean_yes" if conf >= 53 else ("lean_no" if conf >= 47 else "no"))

    return {
        "type": "HOME_SCORE_2PLUS",
        "label": f"{home['team_name']} to Score 2+",
        "confidence": conf,
        "signal": signal,
        "evidence": evidence,
    }


def _calc_away_score_2plus(home: dict, away: dict) -> dict:
    """Away team scores 2 or more goals."""
    evidence = []
    score = 50.0

    a_xg  = away.get("away_xg_pg") or away.get("xg_per_game") or 0.0
    h_xga = home.get("home_xga_pg") or home.get("xga_per_game") or 0.0

    if a_xg > 0:
        score += (a_xg - 1.0) * 18
        evidence.append(f"{away['team_name']} generate {a_xg:.2f} xG/game away")
    if h_xga > 0:
        score += (h_xga - 1.2) * 10
        evidence.append(f"{home['team_name']} concede {h_xga:.2f} xGA/game at home")

    a_scored = away.get("scored_per_game") or 0.0
    if a_scored >= 1.6:
        score += 8
        evidence.append(f"{away['team_name']} average {a_scored:.2f} goals/game this season")

    conf = _clamp(score)
    signal = "yes" if conf >= 60 else ("lean_yes" if conf >= 53 else ("lean_no" if conf >= 47 else "no"))

    return {
        "type": "AWAY_SCORE_2PLUS",
        "label": f"{away['team_name']} to Score 2+",
        "confidence": conf,
        "signal": signal,
        "evidence": evidence,
    }


def _calc_yellow_cards(home: dict, away: dict, threshold: float = 3.5) -> dict:
    """Total match yellow cards over the threshold (default 3.5)."""
    evidence = []
    score = 50.0

    h_yc = home.get("yellow_cards_pg")
    a_yc = away.get("yellow_cards_pg")

    if h_yc is not None and a_yc is not None:
        expected = h_yc + a_yc
        diff = expected - threshold
        score += diff * 15
        evidence.append(
            f"Expected yellow cards: {expected:.1f} "
            f"({home['team_name']} {h_yc:.1f} + {away['team_name']} {a_yc:.1f}/game)"
        )
    elif h_yc is not None:
        score += (h_yc - (threshold / 2)) * 12
    elif a_yc is not None:
        score += (a_yc - (threshold / 2)) * 12
    else:
        # No data — neutral confidence
        return {
            "type": f"CARDS_OVER_{threshold}".replace(".", "_"),
            "label": f"Cards Over {threshold}",
            "confidence": 50,
            "signal": "lean_no",
            "evidence": ["Insufficient yellow card data"],
        }

    # Tactical context: defensive intent teams → more fouls
    h_ppda = home.get("ppda_avg")
    a_ppda = away.get("ppda_avg")
    if h_ppda and h_ppda > 12:
        score += 4
        evidence.append(f"{home['team_name']} sit deep (PPDA {h_ppda:.0f}) → more fouls likely")
    if a_ppda and a_ppda > 12:
        score += 4
        evidence.append(f"{away['team_name']} sit deep (PPDA {a_ppda:.0f}) → more fouls likely")

    conf = _clamp(score)
    signal = "yes" if conf >= 60 else ("lean_yes" if conf >= 53 else ("lean_no" if conf >= 47 else "no"))

    label = f"Cards Over {threshold:.0f}" if threshold == int(threshold) else f"Cards Over {threshold}"
    return {
        "type": f"CARDS_OVER_{str(threshold).replace('.', '_')}",
        "label": label,
        "confidence": conf,
        "signal": signal,
        "evidence": evidence,
    }


# ── Poisson-based match result calculators ────────────────────────────────────

def _calc_match_result(home: dict, away: dict) -> list[dict]:
    """Returns [home_win, draw, away_win] confidence dicts using Poisson model."""
    h_xg, a_xg = _expected_xg_for_match(home, away)
    if not h_xg and not a_xg:
        return []

    p_home, p_draw, p_away = _match_probs(h_xg, a_xg)
    evidence = [f"Expected xG: {home['team_name']} {h_xg:.2f} vs {away['team_name']} {a_xg:.2f}"]

    def _make(bet_type, label, prob):
        conf = _clamp(prob * 100)
        return {
            "type": bet_type, "label": label,
            "confidence": conf,
            "signal": "yes" if conf >= 60 else ("lean_yes" if conf >= 53 else ("lean_no" if conf >= 47 else "no")),
            "evidence": evidence + [f"Poisson probability: {prob:.1%}"],
            "probability": round(prob, 4),
        }

    return [
        _make("MATCH_WIN_HOME", f"{home['team_name']} to Win",  p_home),
        _make("MATCH_DRAW",     "Draw",                         p_draw),
        _make("MATCH_WIN_AWAY", f"{away['team_name']} to Win",  p_away),
    ]


def _calc_double_chance(home: dict, away: dict) -> list[dict]:
    """Returns 1X, X2, 12 double-chance confidence dicts."""
    h_xg, a_xg = _expected_xg_for_match(home, away)
    if not h_xg and not a_xg:
        return []

    p_home, p_draw, p_away = _match_probs(h_xg, a_xg)
    evidence = [f"Poisson model: H {p_home:.1%} / D {p_draw:.1%} / A {p_away:.1%}"]

    def _make(bet_type, label, prob):
        conf = _clamp(prob * 100)
        return {
            "type": bet_type, "label": label,
            "confidence": conf,
            "signal": "yes" if conf >= 60 else ("lean_yes" if conf >= 53 else ("lean_no" if conf >= 47 else "no")),
            "evidence": list(evidence),
            "probability": round(prob, 4),
            "draw_prob":   round(p_draw, 4),   # always store so callers can apply low-draw rule
        }

    results = [
        _make("DOUBLE_CHANCE_1X", f"{home['team_name']} or Draw (1X)", p_home + p_draw),
        _make("DOUBLE_CHANCE_X2", f"Draw or {away['team_name']} (X2)", p_draw + p_away),
        _make("DOUBLE_CHANCE_12", "Either Team to Win (12)",            p_home + p_away),
    ]

    # Stalemate-protection boost: when both defences are tight and one team is
    # clearly dominant, the dominant team's DC (covers even a 0-0) should rank
    # above Over 0.5. Boost that DC by up to +5 confidence points.
    #
    # NOTE: the original +12 boost was too aggressive — it inflated picks with
    # raw Poisson probability ~76% up to 88-89% confidence, masking the true
    # uncertainty. Capped at +5 so the displayed confidence stays honest.
    # Additionally, enforce a hard cap: boosted confidence cannot exceed
    # raw_prob * 100 + 5, preventing grade inflation on marginal picks.
    h_cs = home.get("home_cs_rate") or home.get("clean_sheet_rate") or 0.0
    a_cs = away.get("away_cs_rate") or away.get("clean_sheet_rate") or 0.0
    xg_gap = h_xg - a_xg
    if h_cs >= 0.30 and a_cs >= 0.30:
        if xg_gap >= 0.25:
            # Home team is dominant — boost 1X
            boost = 5 if xg_gap >= 0.55 else 3
            raw_cap = int((p_home + p_draw) * 100) + 5
            results[0]["confidence"] = min(raw_cap, results[0]["confidence"] + boost)
            results[0]["evidence"].append(
                f"Stalemate protection: both defences tight (H CS {h_cs:.0%} / A CS {a_cs:.0%}), "
                f"home dominant (+{boost} xG gap {xg_gap:.2f}) — 1X covers 0-0"
            )
        elif xg_gap <= -0.25:
            # Away team is dominant — boost X2
            boost = 5 if xg_gap <= -0.55 else 3
            raw_cap = int((p_draw + p_away) * 100) + 5
            results[1]["confidence"] = min(raw_cap, results[1]["confidence"] + boost)
            results[1]["evidence"].append(
                f"Stalemate protection: both defences tight (H CS {h_cs:.0%} / A CS {a_cs:.0%}), "
                f"away dominant (+{boost} xG gap {abs(xg_gap):.2f}) — X2 covers 0-0"
            )

    return results


def _calc_correct_score(home: dict, away: dict, top_n: int = 5) -> list[dict]:
    """Returns the top-N most likely scorelines."""
    h_xg, a_xg = _expected_xg_for_match(home, away)
    if not h_xg and not a_xg:
        return []

    scores = _score_probs(h_xg, a_xg)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]
    evidence = [f"Expected xG: {home['team_name']} {h_xg:.2f} — {away['team_name']} {a_xg:.2f}"]

    result = []
    for (h_g, a_g), prob in ranked:
        conf = _clamp(prob * 100 * 5)   # scale up — correct score odds are long
        result.append({
            "type": "CORRECT_SCORE",
            "label": f"Correct Score: {h_g}–{a_g}",
            "confidence": conf,
            "signal": "lean_yes" if conf >= 50 else "lean_no",
            "evidence": evidence + [f"Poisson probability: {prob:.1%}"],
            "probability": round(prob, 4),
            "score": f"{h_g}–{a_g}",
        })
    return result


def _calc_asian_handicap(home: dict, away: dict) -> list[dict]:
    """Returns key Asian Handicap lines based on xG superiority."""
    h_xg, a_xg = _expected_xg_for_match(home, away)
    if not h_xg and not a_xg:
        return []

    p_home, p_draw, p_away = _match_probs(h_xg, a_xg)
    scores = _score_probs(h_xg, a_xg)
    evidence = [f"xG: {home['team_name']} {h_xg:.2f} vs {away['team_name']} {a_xg:.2f}"]

    # Home -1: home wins by 2+
    p_home_minus1 = sum(p for (h, a), p in scores.items() if h - a >= 2)
    # Home -0.5 (= home win)
    p_home_minus05 = p_home
    # Away +0.5 (= away win or draw)
    p_away_plus05 = p_away + p_draw

    def _make(bet_type, label, prob):
        conf = _clamp(prob * 100)
        return {
            "type": bet_type, "label": label,
            "confidence": conf,
            "signal": "yes" if conf >= 60 else ("lean_yes" if conf >= 53 else ("lean_no" if conf >= 47 else "no")),
            "evidence": evidence + [f"Probability: {prob:.1%}"],
            "probability": round(prob, 4),
        }

    return [
        _make("ASIAN_HANDICAP_HOME_MINUS1",   f"{home['team_name']} -1 Asian Handicap",   p_home_minus1),
        _make("ASIAN_HANDICAP_HOME_MINUS05",  f"{home['team_name']} -0.5 (To Win)",        p_home_minus05),
        _make("ASIAN_HANDICAP_AWAY_PLUS05",   f"{away['team_name']} +0.5 (Win or Draw)",   p_away_plus05),
    ]


def _calc_win_to_nil(home: dict, away: dict) -> list[dict]:
    """Home/Away Win to Nil — win AND clean sheet combined probability."""
    h_xg, a_xg = _expected_xg_for_match(home, away)
    if not h_xg and not a_xg:
        return []

    scores = _score_probs(h_xg, a_xg)
    # Home win to nil: home wins AND away scores 0
    p_h_w2n = sum(p for (h, a), p in scores.items() if h > a and a == 0)
    p_a_w2n = sum(p for (h, a), p in scores.items() if a > h and h == 0)

    evidence = [f"xG: {home['team_name']} {h_xg:.2f} vs {away['team_name']} {a_xg:.2f}"]

    # Adjust using historical clean sheet rates
    h_cs = home.get("home_cs_rate") or home.get("clean_sheet_rate") or 0.0
    a_cs = away.get("away_cs_rate") or away.get("clean_sheet_rate") or 0.0
    if h_cs:
        p_h_w2n = (p_h_w2n + h_cs * 0.4) / 1.4   # blend Poisson + historical
        evidence.append(f"{home['team_name']} historical home CS rate: {h_cs:.0%}")
    if a_cs:
        p_a_w2n = (p_a_w2n + a_cs * 0.4) / 1.4
        evidence.append(f"{away['team_name']} historical away CS rate: {a_cs:.0%}")

    def _make(bet_type, label, prob):
        conf = _clamp(prob * 100)
        return {
            "type": bet_type, "label": label,
            "confidence": conf,
            "signal": "yes" if conf >= 60 else ("lean_yes" if conf >= 53 else ("lean_no" if conf >= 47 else "no")),
            "evidence": evidence,
            "probability": round(prob, 4),
        }

    return [
        _make("WIN_TO_NIL_HOME", f"{home['team_name']} Win to Nil", p_h_w2n),
        _make("WIN_TO_NIL_AWAY", f"{away['team_name']} Win to Nil", p_a_w2n),
    ]


def _calc_team_goal_lines(home: dict, away: dict) -> list[dict]:
    """
    Team-specific Over/Under goal lines:
    - Home to score 1+ (Over 0.5), 2+ (Over 1.5), 3+ (Over 2.5)
    - Away to score 1+ (Over 0.5), 2+ (Over 1.5)
    """
    h_xg, a_xg = _expected_xg_for_match(home, away)
    if not h_xg and not a_xg:
        return []

    scores = _score_probs(h_xg, a_xg)
    results = []

    def _team_goals_prob(scores, is_home, at_least):
        """P(team scores >= at_least)"""
        return sum(p for (h, a), p in scores.items() if (h if is_home else a) >= at_least)

    # Recent scoring form — scored in how many of last 3 / last 5 games?
    h_last3 = home.get("scored_last3")  # None if data unavailable
    a_last3 = away.get("scored_last3")
    h_last5 = home.get("scored_last5")
    a_last5 = away.get("scored_last5")

    # Build form penalty multipliers (applied to ph/pa for "score 1+" only)
    def _form_mult(last3, last5):
        if last3 is None:
            return 1.0, ""
        if last3 == 0:
            return 0.70, f"scored in 0 of last 3 — serious cold streak"
        if last3 == 1 and (last5 is None or last5 <= 2):
            return 0.82, f"scored in only 1 of last 3 — poor finishing form"
        if last3 == 1:
            return 0.88, f"scored in 1 of last 3 — slightly off form"
        return 1.0, ""

    h_form_mult, h_form_note = _form_mult(h_last3, h_last5)
    a_form_mult, a_form_note = _form_mult(a_last3, a_last5)

    # Home blank rate — if team regularly fails to score at home, reduce confidence
    h_blank_rate = home.get("home_blank_rate") or 0.0
    if h_blank_rate >= 0.15:
        h_form_mult *= 0.88
        h_form_note = (h_form_note + f"; blanks at home {h_blank_rate:.0%} of games").lstrip("; ")

    # xG conversion discount — penalise teams that consistently fail to convert xG.
    # Uses last-5-game ratio first, falls back to season ratio.
    # Catches repeat offenders like Monaco, Lausanne, AZ that blank despite high xG.
    def _xg_conv_mult(stats):
        conv = stats.get("xg_conversion_last5") or stats.get("xg_overperform_ratio")
        if conv is None:
            return 1.0, ""
        if conv < 0.45:
            return 0.75, (
                f"xG conversion only {conv:.2f}x in recent games "
                f"— severely failing to score despite chances"
            )
        if conv < 0.65:
            return 0.87, (
                f"xG conversion {conv:.2f}x — consistently scoring below expected goals"
            )
        return 1.0, ""

    h_conv_mult, h_conv_note = _xg_conv_mult(home)
    a_conv_mult, a_conv_note = _xg_conv_mult(away)
    h_form_mult *= h_conv_mult
    a_form_mult *= a_conv_mult
    if h_conv_note:
        h_form_note = (h_form_note + f"; {h_conv_note}").lstrip("; ")
    if a_conv_note:
        a_form_note = (a_form_note + f"; {a_conv_note}").lstrip("; ")

    ev_home = [f"{home['team_name']} expected {h_xg:.2f} xG at home"]
    if h_form_note:
        ev_home.append(f"{home['team_name']} {h_form_note}")
    ev_away = [f"{away['team_name']} expected {a_xg:.2f} xG away"]
    if a_form_note:
        ev_away.append(f"{away['team_name']} {a_form_note}")

    for at_least, label_suffix, h_type, a_type in [
        (1, "1+",  "HOME_TEAM_OVER_05", "AWAY_TEAM_OVER_05"),
        (2, "2+",  "HOME_SCORE_2PLUS",  "AWAY_SCORE_2PLUS"),
        (3, "3+",  "HOME_TEAM_OVER_25", "AWAY_TEAM_OVER_15"),
    ]:
        ph = _team_goals_prob(scores, True, at_least)
        pa = _team_goals_prob(scores, False, at_least)

        # Apply recent attacking form penalty to score 1+ predictions
        if at_least == 1:
            ph *= h_form_mult
            pa *= a_form_mult

        # Fix 4 — Opponent quality gate for "Team to Score 1+" bets
        # The 60/40 blend in _expected_xg_for_match is additive — it softens the
        # penalty too much against elite defences. Apply a multiplicative correction:
        #   adj = team_raw_xg × (opponent_xga / league_baseline)
        # If the result is very low, the team is being suppressed by a dominant defence.
        if at_least == 1:
            h_raw_xg = home.get("home_xg_pg") or home.get("xg_per_game") or 0.0
            a_xga = away.get("away_xga_pg") or away.get("xga_per_game") or 0.0
            if h_raw_xg and a_xga:
                adj_h = h_raw_xg * (a_xga / 1.35)
                if adj_h < 0.70:
                    gate = max(0.75, adj_h / 0.70)
                    ph *= gate
                    ev_home.append(
                        f"Opponent quality gate: {away['team_name']} concede only {a_xga:.2f} xGA "
                        f"away — {home['team_name']} adj-xG {adj_h:.2f}, scoring suppressed"
                    )

            a_raw_xg = away.get("away_xg_pg") or away.get("xg_per_game") or 0.0
            h_xga = home.get("home_xga_pg") or home.get("xga_per_game") or 0.0
            if a_raw_xg and h_xga:
                adj_a = a_raw_xg * (h_xga / 1.35)
                if adj_a < 0.70:
                    gate = max(0.75, adj_a / 0.70)
                    pa *= gate
                    ev_away.append(
                        f"Opponent quality gate: {home['team_name']} concede only {h_xga:.2f} xGA "
                        f"at home — {away['team_name']} adj-xG {adj_a:.2f}, scoring suppressed"
                    )

        # Blind-spot xG floor: when opponent has NO defensive data (xGA = None),
        # all opponent quality checks silently skip — we're flying blind on their
        # defence. Require xG ≥ 2.10 to qualify; below that, penalise heavily
        # since we can't distinguish a weak from a stingy defence.
        if at_least == 1:
            a_xga_raw = away.get("away_xga_pg") or away.get("xga_per_game")
            h_xga_raw = home.get("home_xga_pg") or home.get("xga_per_game")
            if a_xga_raw is None and h_xg and h_xg < 2.10:
                ph *= 0.70
                ev_home.append(
                    f"No opponent defensive data — xG {h_xg:.2f} below 2.10 blind-spot floor "
                    f"(-30% scoring probability)"
                )
            if h_xga_raw is None and a_xg and a_xg < 2.10:
                pa *= 0.70
                ev_away.append(
                    f"No opponent defensive data — xG {a_xg:.2f} below 2.10 blind-spot floor "
                    f"(-30% scoring probability)"
                )

        # Opponent's season-long clean sheet rate at the relevant venue
        away_cs = away.get("away_cs_rate") or away.get("clean_sheet_rate") or 0.0
        home_cs = home.get("home_cs_rate") or home.get("clean_sheet_rate") or 0.0
        if away_cs >= 0.35:
            ph *= (1 - away_cs * 0.35)
        if home_cs >= 0.35:
            pa *= (1 - home_cs * 0.35)

        # Opponent's RECENT defensive form — cs_last3 is the critical override.
        # If the defending team has kept 2+ clean sheets in their last 3 games,
        # their defence is hot right now regardless of the attacker's xG.
        if at_least == 1:
            away_cs3 = away.get("cs_last3")   # opponent of home team (away team defends)
            home_cs3 = home.get("cs_last3")   # opponent of away team (home team defends)

            if away_cs3 is not None:
                if away_cs3 == 3:
                    ph *= 0.75
                    ev_home.append(
                        f"{away['team_name']} kept clean sheets in all 3 recent games — defence is locked in"
                    )
                elif away_cs3 == 2:
                    ph *= 0.85
                    ev_home.append(
                        f"{away['team_name']} kept {away_cs3} clean sheets in last 3 — defence in form"
                    )

            if home_cs3 is not None:
                if home_cs3 == 3:
                    pa *= 0.75
                    ev_away.append(
                        f"{home['team_name']} kept clean sheets in all 3 recent games — defence is locked in"
                    )
                elif home_cs3 == 2:
                    pa *= 0.85
                    ev_away.append(
                        f"{home['team_name']} kept {home_cs3} clean sheets in last 3 — defence in form"
                    )

        # Blend with historical rates for "2+" markets
        if at_least == 2:
            h_hist = home.get("over25_rate") or 0.0
            a_hist = away.get("over25_rate") or 0.0
            h_home_xg = home.get("home_xg_pg") or 0.0
            a_away_xg = away.get("away_xg_pg") or 0.0
            if h_hist:
                ph = (ph + h_hist * 0.3) / 1.3
            if a_hist:
                pa = (pa + a_hist * 0.3) / 1.3

        conf_h = _clamp(ph * 100)
        conf_a = _clamp(pa * 100)

        def _sig(c):
            return "yes" if c >= 60 else ("lean_yes" if c >= 53 else ("lean_no" if c >= 47 else "no"))

        results.append({
            "type": h_type,
            "label": f"{home['team_name']} to Score {label_suffix}",
            "confidence": conf_h,
            "signal": _sig(conf_h),
            "evidence": ev_home + [f"Probability: {ph:.1%}"],
            "probability": round(ph, 4),
        })
        results.append({
            "type": a_type,
            "label": f"{away['team_name']} to Score {label_suffix}",
            "confidence": conf_a,
            "signal": _sig(conf_a),
            "evidence": ev_away + [f"Probability: {pa:.1%}"],
            "probability": round(pa, 4),
        })

    return results


def _calc_goal_in_both_halves(home: dict, away: dict) -> dict:
    """
    Goal in Both Halves — approximated using BTTS rate and total xG.
    Logic: if both teams are likely to score (BTTS high) AND enough goals expected,
    distributing at least one to each half becomes likely.
    """
    evidence = []
    score = 50.0

    h_btts = home.get("btts_rate") or 0.0
    a_btts = away.get("btts_rate") or 0.0
    avg_btts = (h_btts + a_btts) / 2 if (h_btts and a_btts) else max(h_btts, a_btts)

    if avg_btts:
        # BTTS is a necessary but not sufficient condition for GBH
        score += (avg_btts - 0.50) * 50
        evidence.append(f"Avg BTTS rate {avg_btts:.0%} — both teams regularly score")

    h_xg, a_xg = _expected_xg_for_match(home, away)
    total_xg = h_xg + a_xg
    if total_xg > 0:
        score += (total_xg - 2.2) * 8
        evidence.append(f"Total expected xG: {total_xg:.2f} — more goals = easier to fill both halves")

    # Over 2.5 rate as proxy (high-scoring games tend to have goals in both halves)
    h_o25 = home.get("over25_rate") or 0.0
    a_o25 = away.get("over25_rate") or 0.0
    if h_o25 and a_o25:
        avg_o25 = (h_o25 + a_o25) / 2
        score += (avg_o25 - 0.48) * 20

    conf = _clamp(score)
    return {
        "type": "GOAL_IN_BOTH_HALVES",
        "label": "Goal in Both Halves",
        "confidence": conf,
        "signal": "yes" if conf >= 60 else ("lean_yes" if conf >= 53 else ("lean_no" if conf >= 47 else "no")),
        "evidence": evidence,
    }


def _calc_over05(home: dict, away: dict) -> dict:
    """Over 0.5 total goals — at least one goal in the match.

    Fallback signal: when stronger bets (BTTS, Over 2.5, Team to Score) are
    suppressed by quality gaps or defensive matchups, a single goal still
    produces value. Uses Poisson P(>=1 goal) as the primary driver.
    """
    evidence = []

    h_xg, a_xg = _expected_xg_for_match(home, away)
    if not h_xg and not a_xg:
        return {
            "type": "OVER_05",
            "label": "Over 0.5 Goals",
            "confidence": 50,
            "signal": "lean_no",
            "evidence": ["Insufficient xG data"],
        }

    scores = _score_probs(h_xg, a_xg)
    prob = sum(p for (h, a), p in scores.items() if h + a >= 1)
    score = prob * 100
    evidence.append(f"Expected xG total {h_xg + a_xg:.2f} -> P(>=1 goal): {prob:.1%}")

    # If both teams have decent BTTS rates, a goal is near-certain
    h_btts = home.get("btts_rate") or 0.0
    a_btts = away.get("btts_rate") or 0.0
    if h_btts and a_btts:
        # Use the minimum (weakest scorer) not the average — one poor scorer is
        # the real risk factor; averaging masks it.
        min_btts = min(h_btts, a_btts)
        # 1.20 multiplier (was 1.55) — BTTS rate is a useful proxy but not near-certain;
        # previous value inflated confidence to 90%+ even in tight defensive matchups.
        proxy = min(0.90, min_btts * 1.20)
        # Equal 50/50 blend (was 67% BTTS / 33% Poisson) — Poisson must carry its weight.
        score = (score + proxy * 100) / 2
        evidence.append(f"BTTS rates H {h_btts:.0%} / A {a_btts:.0%} — both teams attack")

    # Penalise defensive matchups where even Over 0.5 is unreliable
    h_cs = home.get("home_cs_rate") or home.get("clean_sheet_rate") or 0.0
    a_cs = away.get("away_cs_rate") or away.get("clean_sheet_rate") or 0.0
    if h_cs >= 0.38 and a_cs >= 0.38:
        # Both defences solid — 0-0 is a realistic outcome; heavier penalty than before
        score -= 15
        evidence.append(f"Both defences strong (H CS {h_cs:.0%} / A CS {a_cs:.0%}) — goalless risk (-15)")
    elif h_cs >= 0.42 or a_cs >= 0.42:
        score -= 8
        evidence.append(f"One defence very strong (H CS {h_cs:.0%} / A CS {a_cs:.0%}) — suppressed scoring (-8)")

    # Stalemate risk — if both teams are evenly matched defensively AND neither has
    # a clear attacking edge, neither can penetrate the other. Classic 0-0 setup.
    # Check: similar xG output AND both have solid CS rates.
    if h_xg > 0 and a_xg > 0:
        xg_ratio = min(h_xg, a_xg) / max(h_xg, a_xg)  # 1.0 = perfectly balanced
        xg_gap = abs(h_xg - a_xg)
        if xg_ratio >= 0.75 and h_cs >= 0.33 and a_cs >= 0.33:
            # Both teams evenly matched AND both defend well — stalemate risk
            penalty = 18 if xg_ratio >= 0.90 else 12
            score -= penalty
            evidence.append(
                f"Stalemate risk: balanced xG ({h_xg:.2f} vs {a_xg:.2f}) with both "
                f"defences solid (H CS {h_cs:.0%} / A CS {a_cs:.0%}) — 0-0 possible (-{penalty})"
            )
        elif xg_gap < 0.3 and (h_cs >= 0.28 or a_cs >= 0.28):
            # Very similar xG output — neither side dominates
            score -= 8
            evidence.append(
                f"Narrow xG gap ({h_xg:.2f} vs {a_xg:.2f}) — neither side has clear "
                f"scoring edge (-8)"
            )
        # Dominant-team defensive scenario: one team is clearly better BUT the
        # weaker team's defence is tight enough to threaten a 0-0. The stronger
        # team may win 1-0 or the game stays goalless — Over 0.5 is risky.
        # Use the dominant team's DC instead (covers 0-0 + their win).
        if xg_gap >= 0.25 and h_cs >= 0.28 and a_cs >= 0.28:
            score -= 15
            evidence.append(
                f"Dominant-defensive mismatch: xG gap {xg_gap:.2f}, both defences tight "
                f"(H CS {h_cs:.0%} / A CS {a_cs:.0%}) — prefer dominant team DC over Over 0.5 (-15)"
            )

    conf = _clamp(score)
    return {
        "type": "OVER_05",
        "label": "Over 0.5 Goals",
        "confidence": conf,
        "signal": "yes" if conf >= 60 else ("lean_yes" if conf >= 53 else ("lean_no" if conf >= 47 else "no")),
        "evidence": evidence,
    }


def _calc_over15(home: dict, away: dict) -> dict:
    """Over 1.5 total goals."""
    evidence = []
    score = 50.0

    h_xg, a_xg = _expected_xg_for_match(home, away)
    if h_xg or a_xg:
        scores = _score_probs(h_xg, a_xg)
        prob = sum(p for (h, a), p in scores.items() if h + a >= 2)
        score = prob * 100
        evidence.append(f"Expected xG total {h_xg + a_xg:.2f} → P(≥2 goals): {prob:.1%}")

        # xG floor: combined xG must clear the bar to make Over 1.5 reliable.
        # Set a HIGHER floor for Serie A (lower-scoring, 70.7% O1.5 season rate)
        # and a LOWER floor for high-scoring leagues (DED, SSL, BL1).
        total_xg = h_xg + a_xg
        _league_o15 = home.get("competition_code") or away.get("competition_code") or ""
        if _league_o15 == "SA":
            _xg_floor = 3.40   # Serie A — 46.7% Over 2.5, need more firepower evidence
        elif _league_o15 in ("DED", "SSL", "BL1", "CL"):
            _xg_floor = 2.90   # High-scoring leagues — bar is lower
        else:
            _xg_floor = 3.20   # Default
        if total_xg < _xg_floor:
            score -= 20
            evidence.append(
                f"Combined xG {total_xg:.2f} below {_xg_floor:.2f} floor "
                f"({'SA cautious' if _league_o15 == 'SA' else 'insufficient firepower'}) (-20)"
            )

    # Clean sheet rate guard: if either defence is solid, 2 goals is not a given
    h_cs = home.get("home_cs_rate") or home.get("clean_sheet_rate") or 0.0
    a_cs = away.get("away_cs_rate") or away.get("clean_sheet_rate") or 0.0
    if h_cs >= 0.38 and a_cs >= 0.38:
        penalty = round((h_cs + a_cs) / 2 * 40)  # ~16 pts at 40% avg CS rate
        score -= penalty
        evidence.append(
            f"Both defences tight (H CS {h_cs:.0%} / A CS {a_cs:.0%}) — under 2 goals risk (-{penalty})"
        )
    elif h_cs >= 0.45 or a_cs >= 0.45:
        score -= 10
        evidence.append(f"Strong defence present (H CS {h_cs:.0%} / A CS {a_cs:.0%}) — scoring suppressed (-10)")

    # Elite away team game management penalty
    # A dominant away side regularly wins 1-0 — they score once, drop into a
    # low block and manage the result. Season xG and over-rates are inflated
    # by the home side's record against exactly these opponents.
    # Detect via league position (when available) OR xG dominance gap (fallback).
    _away_pos  = away.get("position")
    _home_pos  = home.get("position")
    _away_cs_a = away.get("away_cs_rate") or away.get("clean_sheet_rate") or 0.0
    _h_xg_raw  = home.get("home_xg_pg") or home.get("xg_per_game") or 0.0
    _a_xg_raw  = away.get("away_xg_pg") or away.get("xg_per_game") or 0.0
    _pos_gap   = ((_home_pos or 0) - (_away_pos or 0)) if (_away_pos and _home_pos) else 0
    _pos_condition = bool(_away_pos and _away_pos <= 4 and _pos_gap >= 5)
    _xg_condition  = bool(_a_xg_raw > 0 and _h_xg_raw > 0 and _a_xg_raw >= _h_xg_raw * 1.6)
    # Lower CS threshold when position confirms dominance; stricter for xG-only
    _cs_threshold = 0.35 if _pos_condition else 0.45
    if (_pos_condition or _xg_condition) and _away_cs_a >= _cs_threshold:
        _gap_label = f"pos gap {_pos_gap}" if _pos_condition else f"xG gap {_a_xg_raw:.2f} vs {_h_xg_raw:.2f}"
        _mgmt_penalty = min(20, round((_away_cs_a - 0.25) * 80 + 5))
        score -= _mgmt_penalty
        evidence.append(
            f"{away.get('team_name')} dominant away ({_gap_label}, {_away_cs_a:.0%} away CS) "
            f"— likely 1-0 game management (-{_mgmt_penalty})"
        )

    # Reinforce with historical rates — blended with league-specific Over 1.5 baseline
    _league_o15 = home.get("competition_code") or away.get("competition_code") or ""
    _o15_base = _LEAGUE_OVER15_BASE.get(_league_o15, _DEFAULT_OVER15_BASE)
    h_o25 = home.get("over25_rate") or 0.0
    a_o25 = away.get("over25_rate") or 0.0
    if h_o25 and a_o25:
        # proxy Over 1.5 rate from Over 2.5 rates + league Over 1.5 baseline
        raw_proxy = (h_o25 + a_o25) / 2 * 1.10
        # Anchor toward league baseline so SA teams aren't unfairly penalised
        # for having 70% Over 1.5 rates when 71% is the league average
        proxy_o15 = min(0.92, max(raw_proxy, _o15_base * 0.9))
        score = (score * 2 + proxy_o15 * 100) / 3
        evidence.append(f"Over-2.5 rates: H {h_o25:.0%} / A {a_o25:.0%} (league O1.5 base {_o15_base:.0%})")

    conf = _clamp(score)
    return {
        "type": "OVER_15",
        "label": "Over 1.5 Goals",
        "confidence": conf,
        "signal": "yes" if conf >= 60 else ("lean_yes" if conf >= 53 else ("lean_no" if conf >= 47 else "no")),
        "evidence": evidence,
    }


def _calc_over45(home: dict, away: dict) -> dict:
    """Over 4.5 total goals."""
    evidence = []
    score = 50.0

    h_xg, a_xg = _expected_xg_for_match(home, away)
    if h_xg or a_xg:
        scores = _score_probs(h_xg, a_xg)
        prob = sum(p for (h, a), p in scores.items() if h + a >= 5)
        score = prob * 100
        evidence.append(f"Expected xG {h_xg + a_xg:.2f} → P(≥5 goals): {prob:.1%}")

    h_o35 = home.get("over35_rate") or 0.0
    a_o35 = away.get("over35_rate") or 0.0
    if h_o35 and a_o35:
        avg = (h_o35 + a_o35) / 2
        score = (score + avg * 60) / 2
        evidence.append(f"Over-3.5 rates: H {h_o35:.0%} / A {a_o35:.0%}")

    conf = _clamp(score)
    return {
        "type": "OVER_45",
        "label": "Over 4.5 Goals",
        "confidence": conf,
        "signal": "yes" if conf >= 60 else ("lean_yes" if conf >= 53 else ("lean_no" if conf >= 47 else "no")),
        "evidence": evidence,
    }


def _calc_cards_45(home: dict, away: dict) -> dict:
    """Total yellow cards Over 4.5."""
    return _calc_yellow_cards(home, away, threshold=4.5)


def _calc_odd_even(home: dict, away: dict) -> list[dict]:
    """Odd/Even total goals — Poisson probability."""
    h_xg, a_xg = _expected_xg_for_match(home, away)
    if not h_xg and not a_xg:
        return []

    scores = _score_probs(h_xg, a_xg)
    p_even = sum(p for (h, a), p in scores.items() if (h + a) % 2 == 0)
    p_odd  = 1 - p_even

    evidence = [f"xG total: {h_xg + a_xg:.2f} — Poisson probability model"]

    def _make(bet_type, label, prob):
        conf = _clamp(prob * 100)
        return {
            "type": bet_type, "label": label,
            "confidence": conf,
            "signal": "yes" if conf >= 60 else ("lean_yes" if conf >= 53 else ("lean_no" if conf >= 47 else "no")),
            "evidence": evidence,
            "probability": round(prob, 4),
        }

    return [
        _make("EVEN_GOALS", "Even Total Goals", p_even),
        _make("ODD_GOALS",  "Odd Total Goals",  p_odd),
    ]


def _calc_btts_no(home: dict, away: dict) -> dict:
    """Both Teams to Score — No (at least one team kept scoreless)."""
    btts_yes = _calc_btts(home, away)
    yes_conf = btts_yes["confidence"]
    no_conf  = _clamp(100 - yes_conf)

    # Mirror the evidence but reframe it
    evidence = []
    h_cs = home.get("home_cs_rate") or home.get("clean_sheet_rate") or 0.0
    a_cs = away.get("away_cs_rate") or away.get("clean_sheet_rate") or 0.0
    if h_cs:
        evidence.append(f"{home['team_name']} {h_cs:.0%} clean sheets at home")
    if a_cs:
        evidence.append(f"{away['team_name']} {a_cs:.0%} clean sheets away")
    h_btts = home.get("btts_rate") or 0.0
    a_btts = away.get("btts_rate") or 0.0
    if h_btts and a_btts:
        evidence.append(f"BTTS-Yes probability {((h_btts+a_btts)/2):.0%} → No probability {1-(h_btts+a_btts)/2:.0%}")

    return {
        "type": "BTTS_NO",
        "label": "Both Teams to Score — No",
        "confidence": no_conf,
        "signal": "yes" if no_conf >= 60 else ("lean_yes" if no_conf >= 53 else ("lean_no" if no_conf >= 47 else "no")),
        "evidence": evidence,
    }


def _apply_motivation_boost(signal: dict, home_ctx: dict | None, away_ctx: dict | None) -> dict:
    """
    Adjust BTTS / over-goal bets based on team motivation.
    Relegation fighters play more desperately → slightly more open games.
    Mid-table comfort teams play with less intensity → lean toward fewer goals.
    """
    bet_type = signal.get("type", "")
    is_goals_bet = bet_type in (
        "BTTS_YES", "OVER_25", "OVER_35", "HOME_SCORE_2PLUS", "AWAY_SCORE_2PLUS",
        "HOME_TEAM_OVER_05",
    )
    if not is_goals_bet:
        return signal

    conf = signal["confidence"]
    evidence = list(signal.get("evidence", []))

    for ctx, label in [(home_ctx, "home"), (away_ctx, "away")]:
        if not ctx:
            continue
        motivation = ctx.get("motivation", "standard")
        if motivation in ("relegation_fight", "relegation_threat"):
            if label == "home":
                # Home relegation team must attack — boosts goals
                conf = min(93, conf + 3)
                evidence.append(f"{ctx.get('motivation_label', 'High motivation')} — more open football expected")
            else:
                # Away relegation team often parks the bus and nicks one
                # — for home-team score bets this is a threat, not a boost
                if bet_type in ("HOME_TEAM_OVER_05", "BTTS_YES"):
                    conf = max(35, conf - 8)
                    evidence.append(f"{ctx.get('team_name', 'Away team')} fighting relegation away — likely to defend deep and hit on the counter (-8)")
                else:
                    conf = min(93, conf + 2)
                    evidence.append(f"{ctx.get('motivation_label', 'High motivation')} — every point vital")
        elif motivation == "nothing_to_play_for":
            conf = max(35, conf - 4)
            evidence.append(f"Mid-table comfort ({ctx.get('team_name', label)}) — lower intensity likely")

    signal = dict(signal)
    signal["confidence"] = conf
    signal["evidence"] = evidence
    signal["signal"] = (
        "yes" if conf >= 60 else
        ("lean_yes" if conf >= 53 else
         ("lean_no" if conf >= 47 else "no"))
    )
    return signal


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_all_bet_signals(
    home_stats: dict,
    away_stats: dict,
    home_context: dict | None = None,
    away_context: dict | None = None,
) -> list[dict]:
    """
    Runs ALL confidence calculators for one fixture.
    Returns a list of signal dicts sorted by confidence (highest first),
    filtered to signals that lean positive (confidence ≥ 53).
    Each signal is enriched with bookie market metadata.
    """
    raw: list[dict] = []

    # ── Goals markets ──────────────────────────────────────────────────────────
    raw.append(_calc_btts(home_stats, away_stats))
    raw.append(_calc_btts_no(home_stats, away_stats))
    raw.append(_calc_over05(home_stats, away_stats))
    raw.append(_calc_over15(home_stats, away_stats))
    raw.append(_calc_over25(home_stats, away_stats))
    raw.append(_calc_over35(home_stats, away_stats))
    raw.append(_calc_over45(home_stats, away_stats))
    raw.append(_calc_under15(home_stats, away_stats))

    # ── Match result (Poisson) ─────────────────────────────────────────────────
    raw.extend(_calc_match_result(home_stats, away_stats))

    # ── Double chance ──────────────────────────────────────────────────────────
    raw.extend(_calc_double_chance(home_stats, away_stats))

    # ── Correct score (top 5) ──────────────────────────────────────────────────
    raw.extend(_calc_correct_score(home_stats, away_stats, top_n=5))

    # ── Asian handicap ─────────────────────────────────────────────────────────
    raw.extend(_calc_asian_handicap(home_stats, away_stats))

    # ── Win to nil ─────────────────────────────────────────────────────────────
    raw.extend(_calc_win_to_nil(home_stats, away_stats))

    # ── Team goal lines ────────────────────────────────────────────────────────
    raw.extend(_calc_team_goal_lines(home_stats, away_stats))

    # ── Clean sheets — DISABLED (66% loss rate; historical CS% is noise, not signal)
    # raw.append(_calc_home_clean_sheet(home_stats, away_stats))
    # raw.append(_calc_away_clean_sheet(home_stats, away_stats))

    # ── Half markets ──────────────────────────────────────────────────────────
    raw.append(_calc_goal_in_both_halves(home_stats, away_stats))

    # ── Cards ─────────────────────────────────────────────────────────────────
    raw.append(_calc_yellow_cards(home_stats, away_stats, threshold=3.5))
    raw.append(_calc_cards_45(home_stats, away_stats))

    # ── Odd/Even ──────────────────────────────────────────────────────────────
    raw.extend(_calc_odd_even(home_stats, away_stats))

    # ── Apply motivation adjustments ──────────────────────────────────────────
    adjusted = [
        _apply_motivation_boost(s, home_context, away_context)
        for s in raw if s
    ]

    # ── Enrich with bookie map metadata ───────────────────────────────────────
    enriched = [enrich_signal(s) for s in adjusted]

    # Only surface bets that lean positive (confidence ≥ 53)
    positive = [s for s in enriched if s["signal"] in ("yes", "lean_yes")]

    # ── Grade every signal ─────────────────────────────────────────────────────
    graded = grade_all_signals(positive, home_stats, away_stats, home_context, away_context)

    # ── Small-sample confidence cap ────────────────────────────────────────────
    # Previously, small-sample warnings were cosmetic — they appeared in the
    # output but had zero effect on whether a pick was selected. The confidence
    # stayed at 90%+ even with 2 games of data, and 7 of 8 early-season losses
    # had this warning. Now we enforce hard caps so the threshold filter does
    # the right thing: early-season picks can only fire if the raw signal is
    # genuinely extreme.
    #   < 5 games  → cap confidence at 80%  (never reaches 85% threshold)
    #   < 8 games  → cap confidence at 86%  (only very strong signals pass)
    _min_played = min(home_stats.get("played") or 0, away_stats.get("played") or 0)
    if _min_played < 8:
        _conf_cap = 80 if _min_played < 5 else 86
        for s in graded:
            if s.get("confidence", 0) > _conf_cap:
                s["confidence"] = _conf_cap

    # ── Over-confirmation filter ───────────────────────────────────────────────
    # A lower over tier is only picked when the next tier up ALSO grades > 75.
    # Rationale: if Over 1.5 is already strong, Over 0.5 is near-certain — safe.
    # If Over 1.5 grades ≤ 75, there's real doubt about even 2 goals, meaning
    # Over 0.5 has genuine risk and should not be selected.
    #   Over 0.5  valid only if Over 1.5  grades > 75
    #   Over 1.5  valid only if Over 2.5  grades > 75
    #   Over 2.5  valid only if Over 3.5  grades > 75
    _grade_by_type = {s["type"]: s.get("grade_score", 0) for s in graded}
    _confirmation_pairs = [
        ("OVER_05", "OVER_15"),
        ("OVER_15", "OVER_25"),
        ("OVER_25", "OVER_35"),
        # Team-specific picks require Over 1.5 to also grade > 75 (same safety chain)
        ("HOME_TEAM_OVER_05", "OVER_15"),
        ("AWAY_TEAM_OVER_05", "OVER_15"),
    ]
    _to_drop = {
        lower
        for lower, higher in _confirmation_pairs
        if lower in _grade_by_type and _grade_by_type.get(higher, 0) <= 75
    }
    if _to_drop:
        graded = [s for s in graded if s["type"] not in _to_drop]

    # ── Scoring drought hard gate ──────────────────────────────────────────────
    # If a team scored in NONE of their last 3 games they are in a goal drought.
    # No xG model can reliably override 3 consecutive scoreless games — block the
    # team-to-score pick entirely rather than just penalising confidence.
    _h_drought = home_stats.get("scored_last3") == 0
    _a_drought = away_stats.get("scored_last3") == 0
    if _h_drought or _a_drought:
        _drought_drop = set()
        if _h_drought:
            _drought_drop.add("HOME_TEAM_OVER_05")
            print(f"[Drought] {home_stats.get('team_name')} scored in 0 of last 3 — HOME_TEAM_OVER_05 blocked")
        if _a_drought:
            _drought_drop.add("AWAY_TEAM_OVER_05")
            print(f"[Drought] {away_stats.get('team_name')} scored in 0 of last 3 — AWAY_TEAM_OVER_05 blocked")
        graded = [s for s in graded if s["type"] not in _drought_drop]

    # ── Repeat blank blocker ──────────────────────────────────────────────────
    # If a team scored 0 goals in 2+ of their last 6 actual fixtures (from
    # HistoricalFixture), they are a chronic chance-misser. Block their
    # "to Score 1+" pick regardless of what xG says.
    # Uses actual match results (not Prediction outcomes) so it works in both
    # live and backtest mode — the DB always has real scores.
    try:
        from bets.models import HistoricalFixture as _HF
        from django.db.models import Q as _Q

        def _is_chronic_blank(team_id: int, match_date_str: str) -> bool:
            """True if team scored 0 in 2+ of last 6 games (by actual result)."""
            qs = _HF.objects.filter(
                _Q(home_team_id=team_id) | _Q(away_team_id=team_id),
                match_date__lt=match_date_str,
                home_score__isnull=False,
            ).order_by("-match_date")[:6]
            blanks = 0
            for f in qs:
                is_h = f.home_team_id == team_id
                scored = f.home_score if is_h else f.away_score
                if scored == 0:
                    blanks += 1
            return blanks >= 2

        _h_id = home_stats.get("team_id")
        _a_id = away_stats.get("team_id")
        from datetime import date as _date_cls
        _match_dt = str(_date_cls.today())

        _repeat_drop = set()
        h_name = home_stats.get("team_name", "")
        a_name = away_stats.get("team_name", "")

        if _h_id and _is_chronic_blank(_h_id, _match_dt):
            _repeat_drop.add("HOME_TEAM_OVER_05")
            print(f"[RepeatBlank] {h_name} scored 0 in 2+ of last 6 — HOME_TEAM_OVER_05 blocked")
        if _a_id and _is_chronic_blank(_a_id, _match_dt):
            _repeat_drop.add("AWAY_TEAM_OVER_05")
            print(f"[RepeatBlank] {a_name} scored 0 in 2+ of last 6 — AWAY_TEAM_OVER_05 blocked")
        if _repeat_drop:
            graded = [s for s in graded if s["type"] not in _repeat_drop]
    except Exception:
        pass  # non-fatal

    # ── P(0-0) block ───────────────────────────────────────────────────────────
    # If the Poisson model gives >15% probability of a goalless game, every
    # goal-dependent pick for this fixture is unreliable and must be dropped.
    _h_xg_m, _a_xg_m = _expected_xg_for_match(home_stats, away_stats)
    if _h_xg_m > 0 and _a_xg_m > 0:
        _p00 = math.exp(-_h_xg_m) * math.exp(-_a_xg_m)
        if _p00 > 0.15:
            _p00_drop = {"OVER_05", "OVER_15", "BTTS_YES", "HOME_TEAM_OVER_05", "AWAY_TEAM_OVER_05"}
            _dropped = [s["type"] for s in graded if s["type"] in _p00_drop]
            if _dropped:
                print(f"[P(0-0)={_p00:.1%}] Dropping goal picks: {_dropped}")
            graded = [s for s in graded if s["type"] not in _p00_drop]

    # ── H2H low-scoring gate ───────────────────────────────────────────────────
    # If these two teams consistently produce low-scoring games against each other,
    # Over and team-to-score picks are unreliable regardless of season form.
    _home_id = home_stats.get("team_id")
    _away_id = away_stats.get("team_id")
    if _home_id and _away_id:
        try:
            _h2h = get_h2h_scored(_home_id, _away_id)
            _h2h_avg = _h2h.get("h2h_avg_goals")
            _h2h_low = _h2h.get("h2h_low_scoring_count", 0)
            _h2h_n   = _h2h.get("h2h_count", 0)

            if _h2h_n >= 3 and _h2h_avg is not None:
                if _h2h_avg < 1.0 or _h2h_low >= 4:
                    # Very tight H2H history — block all goal picks
                    _h2h_drop = {"OVER_05", "OVER_15", "OVER_25", "BTTS_YES",
                                 "HOME_TEAM_OVER_05", "AWAY_TEAM_OVER_05"}
                    _dropped = [s["type"] for s in graded if s["type"] in _h2h_drop]
                    if _dropped:
                        print(f"[H2H avg={_h2h_avg} goals, {_h2h_low}/{_h2h_n} low-scoring] "
                              f"Blocking all goal picks: {_dropped}")
                    graded = [s for s in graded if s["type"] not in _h2h_drop]
                elif _h2h_avg < 1.5 or _h2h_low >= 3:
                    # Generally tight H2H — block Over 1.5 and above, team picks
                    _h2h_drop = {"OVER_15", "OVER_25", "HOME_TEAM_OVER_05", "AWAY_TEAM_OVER_05"}
                    _dropped = [s["type"] for s in graded if s["type"] in _h2h_drop]
                    if _dropped:
                        print(f"[H2H avg={_h2h_avg} goals, {_h2h_low}/{_h2h_n} low-scoring] "
                              f"Blocking higher goal picks: {_dropped}")
                    graded = [s for s in graded if s["type"] not in _h2h_drop]
        except Exception:
            pass  # H2H fetch failure is non-fatal

    return graded


def get_todays_best_bets() -> dict:
    """
    Fetches all of today's SCHEDULED fixtures, computes bet signals for each,
    and returns the top predictions ranked by confidence.

    Returns:
      {
        "date":        "YYYY-MM-DD",
        "total_fixtures": int,
        "analysed":    int,
        "predictions": [
          {
            "fixture_id":   int,
            "kickoff":      str,
            "competition":  str,
            "home_team":    str,
            "away_team":    str,
            "bet_type":     str,
            "label":        str,
            "confidence":   int,
            "signal":       str,
            "evidence":     [str],
          }, ...
        ]
      }
    """
    today = datetime.utcnow().strftime("%Y-%m-%d")

    # Fetch today's matches
    try:
        resp = requests.get(
            f"{BASE_URL}/matches",
            headers=_headers(),
            params={"dateFrom": today, "dateTo": today},
            timeout=10,
        )
        resp.raise_for_status()
        all_matches = resp.json().get("matches", [])
    except requests.RequestException as exc:
        return {"error": f"Could not fetch today's fixtures: {exc}", "predictions": []}

    # Only process SCHEDULED / TIMED fixtures (not already live/finished)
    scheduled = [
        m for m in all_matches
        if m.get("status") in ("SCHEDULED", "TIMED")
    ]

    predictions: list[dict] = []
    analysed = 0

    for match in scheduled:
        home_data = match.get("homeTeam", {})
        away_data = match.get("awayTeam", {})
        comp_data = match.get("competition", {})

        home_id    = home_data.get("id")
        away_id    = away_data.get("id")
        home_name  = home_data.get("name", "")
        away_name  = away_data.get("name", "")
        comp_code  = comp_data.get("code", "")
        kickoff    = match.get("utcDate", "")
        fixture_id = match.get("id")

        if not home_id or not away_id:
            continue

        # If API-Football quota is exhausted, only predict Understat-covered leagues.
        # Non-Understat leagues rely on fake goals-based xG proxies which are unreliable.
        if _api_football_module._quota_exhausted and comp_code not in _UNDERSTAT_LEAGUES:
            continue
        try:
            home_stats = get_team_season_stats(
                home_id, home_name, comp_code, today
            )
            away_stats = get_team_season_stats(
                away_id, away_name, comp_code, today
            )
        except Exception as exc:
            print(f"[Predictions] Season stats failed for {home_name} vs {away_name}: {exc}")
            continue

        # Get motivation context
        home_ctx = away_ctx = None
        try:
            if comp_code:
                home_ctx = get_team_context(home_id, comp_code)
                away_ctx = get_team_context(away_id, comp_code)
        except Exception:
            pass

        signals = get_all_bet_signals(home_stats, away_stats, home_ctx, away_ctx)
        analysed += 1

        for sig in signals:
            predictions.append({
                "fixture_id":  fixture_id,
                "kickoff":     kickoff,
                "competition": comp_data.get("name", ""),
                "competition_code": comp_code,
                "home_team":   home_name,
                "away_team":   away_name,
                "bet_type":    sig["type"],
                "label":       sig["label"],
                "confidence":  sig["confidence"],
                "signal":      sig["signal"],
                "evidence":    sig["evidence"],
            })

    # Sort by grade_score (holistic quality) then confidence as tiebreaker
    predictions.sort(
        key=lambda p: (p.get("grade_score", 0), p.get("confidence", 0)),
        reverse=True,
    )

    # Flag the single best pick per fixture
    seen_fixtures: set = set()
    for p in predictions:
        fid = p["fixture_id"]
        p["top_pick"] = fid not in seen_fixtures
        seen_fixtures.add(fid)

    elite   = [p for p in predictions if p.get("is_elite")]
    grade_a = [p for p in predictions if p.get("grade") in ("A+", "A", "A-") and not p.get("is_elite")]

    return {
        "date":             today,
        "total_fixtures":   len(all_matches),
        "scheduled":        len(scheduled),
        "analysed":         analysed,
        "elite_count":      len(elite),
        "grade_a_count":    len(grade_a),
        "predictions":      predictions,
        # Convenience: top-3 elite picks at the front
        "elite_picks":      elite[:3],
    }
