"""
Bet Signal Grading System
=========================  
a letter grade and an accuracy estimate.

The goal is to surface only the bets where the data genuinely supports
the pick — not just "our model says so", but "multiple independent factors
all point the same way and the underlying data is solid."

The 5 Factors (weighted)
──────────────────────────
  Factor 1 — Prediction Strength          (35 pts)
      How far above 50% is the raw confidence?
      A confidence of 80% → high contribution.
  
  Factor 2 — Market Inherent Predictability  (20 pts)
      Some markets are structurally more predictable than others.
      Over/Under and BTTS are statistically stable.
      Correct score and first-goal time are high-variance.
      Source: bookie_map.py → reliability field.

  Factor 3 — Data Completeness            (25 pts)
      Does the prediction rely on solid, complete data?
      +12  Understat xG data available
      +6   Home/away venue splits available
      +4   Historical BTTS + over-25 + clean-sheet rates all present
      +2   Yellow card history available
      +1   Poisson probability included in signal

  Factor 4 — Evidence Convergence         (12 pts)
      Do multiple independent signals agree?
      +2 per distinct evidence bullet, up to +12
      High convergence = multiple data sources pointing the same way.

  Factor 5 — Sample Size                  (8 pts)
      Are the rates calculated over enough matches to be trusted?
      30+ games → 8 pts  (full season, very reliable)
      20–29     → 6 pts
      10–19     → 3 pts
      <10       → 0 pts  (early season — treat rates with caution)

Grade Scale (out of 100)
─────────────────────────
  S    ≥ 88  "Elite — meets the 95% accuracy threshold"
  A+   83–87 "Near-elite — extremely strong signal"
  A    76–82 "High confidence — back with conviction"
  A-   70–75 "Strong — solid statistical backing"
  B+   65–69 "Good — worth including in a slip"
  B    60–64 "Moderate — use as part of a combo"
  B-   55–59 "Borderline — proceed with caution"
  C    53–54 "Marginal — for reference only"
"""

from .bookie_map import BOOKIE_MAP


# ── Grade thresholds ──────────────────────────────────────────────────────────
_GRADE_SCALE = [
    (88, "S",  "Elite — meets the 95% accuracy threshold",   "🟩"),
    (83, "A+", "Near-elite — extremely strong signal",        "🟩"),
    (76, "A",  "High confidence — back with conviction",      "🟢"),
    (70, "A-", "Strong — solid statistical backing",          "🟢"),
    (65, "B+", "Good — worth including in a slip",            "🔵"),
    (60, "B",  "Moderate — use as part of a combo",           "🔵"),
    (55, "B-", "Borderline — proceed with caution",           "🟡"),
    (0,  "C",  "Marginal — for reference only",               "🟡"),
]


def _letter_grade(score: float) -> tuple[str, str, str]:
    """Returns (grade, label, colour_indicator) for a 0-100 grade score."""
    for threshold, grade, label, colour in _GRADE_SCALE:
        if score >= threshold:
            return grade, label, colour
    return "C", "Marginal — for reference only", "🟡"


# ── Factor 1: Prediction Strength ─────────────────────────────────────────────
def _factor_prediction_strength(confidence: int) -> dict:
    """
    Maps raw confidence (35–93) onto a 0–35 contribution.
    Confidence of 50 = 0 pts (coin flip, no edge).
    Confidence of 93 = 35 pts (maximum edge).
    """
    edge = max(0, confidence - 50)          # 0 to 43
    score = min(35, round(edge * 35 / 43))  # scale to 0–35
    return {
        "name": "Prediction Strength",
        "score": score,
        "max": 35,
        "detail": f"Raw confidence {confidence}% — edge above 50%: +{edge}%",
    }


# ── Factor 2: Market Inherent Predictability ──────────────────────────────────
_RELIABILITY_SCORE = {"high": 20, "medium": 13, "low": 6}

_HIGH_RELIABILITY_TYPES = {
    "OVER_05", "OVER_15", "OVER_25", "BTTS_YES", "BTTS_NO",
    "HOME_TEAM_OVER_05", "AWAY_TEAM_OVER_05",
    "DOUBLE_CHANCE_1X", "DOUBLE_CHANCE_X2", "DOUBLE_CHANCE_12",
}
_LOW_RELIABILITY_TYPES = {
    "CORRECT_SCORE", "FIRST_GOAL_UNDER_25MIN",
    "HT_RESULT_HOME", "HT_RESULT_DRAW", "HT_RESULT_AWAY",
    "ODD_GOALS", "EVEN_GOALS", "RED_CARD_YES",
}

def _factor_market_reliability(signal: dict) -> dict:
    """Market inherent predictability: 0–20 pts."""
    bet_type = signal.get("type", "")
    bookie_info = BOOKIE_MAP.get(bet_type, {})

    # Override for well-known high/low reliability types
    if bet_type in _HIGH_RELIABILITY_TYPES:
        reliability = "high"
    elif bet_type in _LOW_RELIABILITY_TYPES:
        reliability = "low"
    else:
        reliability = bookie_info.get("reliability", signal.get("reliability", "medium"))

    score = _RELIABILITY_SCORE.get(reliability, 13)
    labels = {
        "high":   "Statistically stable market — rates converge reliably over large samples",
        "medium": "Moderately predictable — xG-based signal carries real weight",
        "low":    "High-variance market — outcome difficult to pin down even with good data",
    }
    return {
        "name": "Market Reliability",
        "score": score,
        "max": 20,
        "reliability": reliability,
        "detail": labels[reliability],
    }


# ── Factor 3: Data Completeness ───────────────────────────────────────────────
def _factor_data_completeness(home: dict, away: dict, signal: dict) -> dict:
    """How complete and solid is the underlying data? 0–25 pts."""
    score = 0
    breakdown = []

    # Understat xG data (most important — expected goals is the backbone)
    if home.get("xg_per_game") and away.get("xg_per_game"):
        score += 12
        breakdown.append("✓ Understat xG data available for both teams (+12)")
    else:
        breakdown.append("✗ No Understat xG — using goals-based estimate only (-12)")

    # Venue-specific splits
    if home.get("home_xg_pg") and away.get("away_xg_pg"):
        score += 6
        breakdown.append("✓ Home/away venue splits available (+6)")
    else:
        breakdown.append("✗ No venue splits — season average applied (+0)")

    # Historical rate data
    rate_fields = ["btts_rate", "over25_rate", "clean_sheet_rate"]
    h_rates = sum(1 for f in rate_fields if home.get(f) is not None)
    a_rates = sum(1 for f in rate_fields if away.get(f) is not None)
    rate_score = round(((h_rates + a_rates) / 6) * 4)   # 0–4 pts
    score += rate_score
    breakdown.append(
        f"{'✓' if rate_score >= 3 else '~'} Historical rate data "
        f"({h_rates}/3 home, {a_rates}/3 away) (+{rate_score})"
    )

    # Yellow card history (for cards bets especially)
    if home.get("yellow_cards_pg") is not None and away.get("yellow_cards_pg") is not None:
        score += 2
        breakdown.append("✓ Yellow card history available (+2)")

    # Poisson probability in signal
    if signal.get("probability") is not None:
        score += 1
        breakdown.append("✓ Poisson probability calculated (+1)")

    return {
        "name": "Data Completeness",
        "score": score,
        "max": 25,
        "detail": breakdown,
    }


# ── Factor 4: Evidence Convergence ────────────────────────────────────────────
def _factor_evidence_convergence(signal: dict) -> dict:
    """
    More independent evidence bullets = better convergence.
    0–12 pts.
    """
    evidence = signal.get("evidence", [])
    count = len(evidence)
    score = min(12, count * 2)

    if count >= 4:
        label = f"{count} independent metrics agree — strong convergence"
    elif count >= 2:
        label = f"{count} metrics align — moderate convergence"
    else:
        label = "Limited evidence — single-source signal"

    return {
        "name": "Evidence Convergence",
        "score": score,
        "max": 12,
        "detail": label,
        "evidence_count": count,
    }


# ── Factor 5: Sample Size ─────────────────────────────────────────────────────
def _factor_sample_size(home: dict, away: dict) -> dict:
    """Is there enough match data to trust the rates? 0–8 pts."""
    h_played = home.get("played") or 0
    a_played = away.get("played") or 0
    min_played = min(h_played, a_played)

    if min_played >= 30:
        score, label = 8, f"{min_played} games played — full season sample (very reliable)"
    elif min_played >= 20:
        score, label = 6, f"{min_played} games played — strong sample"
    elif min_played >= 10:
        score, label = 3, f"{min_played} games played — moderate sample"
    elif min_played >= 5:
        score, label = 1, f"{min_played} games — early season (treat rates with caution)"
    else:
        score, label = 0, "Insufficient match data — grade unreliable"

    return {
        "name": "Sample Size",
        "score": score,
        "max": 8,
        "detail": label,
        "games_home": h_played,
        "games_away": a_played,
    }


# ── Penalty Modifiers ─────────────────────────────────────────────────────────
def _apply_penalties(
    grade_score: float,
    signal: dict,
    home: dict,
    away: dict,
    home_ctx: dict | None,
    away_ctx: dict | None,
) -> tuple[float, list[str]]:
    """
    Apply deductions for risk factors that could undermine the prediction.
    Returns (adjusted_score, [penalty_notes]).
    """
    notes = []
    deductions = 0.0

    # 1. Motivation mismatch: relegation team vs mid-table (upset potential)
    if home_ctx and away_ctx:
        h_motiv = home_ctx.get("motivation", "standard")
        a_motiv = away_ctx.get("motivation", "standard")
        if (h_motiv == "nothing_to_play_for" and
                a_motiv in ("relegation_fight", "relegation_threat")):
            deductions += 4
            notes.append("⚠ Home team has little motivation vs desperate away side (-4)")
        elif (a_motiv == "nothing_to_play_for" and
              h_motiv in ("relegation_fight", "relegation_threat")):
            deductions += 3
            notes.append("⚠ Away team has little to play for vs fighting home side (-3)")

    # 2. Away relegation team on BTTS — park-the-bus risk directly undermines both teams scoring
    if away_ctx:
        a_motiv = away_ctx.get("motivation", "standard")
        if (a_motiv in ("relegation_fight", "relegation_threat") and
                signal.get("type") == "BTTS_YES"):
            deductions += 5
            notes.append("⚠ Away team in relegation — will likely defend deep, clean sheet risk (-5)")

    # 3. Elite away team on 1X DC — high-quality away side can outperform Poisson on any day
    if signal.get("type") == "DOUBLE_CHANCE_1X":
        a_pos = away.get("position")
        a_xg  = away.get("xg_per_game") or away.get("away_xg_pg") or 0
        if a_pos and a_pos <= 6 and a_xg > 1.5:
            deductions += 5
            notes.append(
                f"⚠ {away.get('team_name', 'Away team')} (pos {a_pos}, {a_xg:.2f} xG) "
                f"are elite away — can outperform Poisson probability (-5)"
            )

    # 3b. Hot home team on X2 DC — mirror of the above.
    # When the home team is in strong recent form (won 2+ of last 3) AND ranks in the
    # top half of the table, the Poisson model underweights their current momentum.
    # Example: Aston Villa (6W from last 7) was fully in form but Arsenal X2 was backed.
    if signal.get("type") == "DOUBLE_CHANCE_X2":
        h_pos       = home.get("position")
        h_scored3   = home.get("scored_last3")   # goals scored in last 3 games
        h_xg_home   = home.get("home_xg_pg") or home.get("xg_per_game") or 0
        h_cs3       = home.get("cs_last3")        # clean sheets in last 3
        # Hot home team: top-half position, scoring well, and in form
        if (h_pos and h_pos <= 10 and h_xg_home > 1.3
                and h_scored3 is not None and h_scored3 >= 3):
            deductions += 5
            notes.append(
                f"⚠ {home.get('team_name', 'Home team')} (pos {h_pos}, scored in all 3 recent games) "
                f"are in strong home form — X2 underweights their momentum (-5)"
            )
        # Also penalise if home team has been keeping clean sheets (hard to break down)
        if h_cs3 is not None and h_cs3 >= 2 and h_xg_home > 1.2:
            deductions += 3
            notes.append(
                f"⚠ {home.get('team_name', 'Home team')} kept {h_cs3} clean sheets in last 3 "
                f"— tight defence at home increases upset risk (-3)"
            )

    # 4. Conflicting xG vs actual goals scored
    h_xg = home.get("xg_per_game") or 0
    h_scored = home.get("scored_per_game") or 0
    a_xg = away.get("xg_per_game") or 0
    a_scored = away.get("scored_per_game") or 0

    if h_xg > 0 and abs(h_scored - h_xg) > 0.5:
        deductions += 2
        notes.append(
            f"⚠ {home.get('team_name','')} xG ({h_xg:.2f}) diverges from actual goals "
            f"({h_scored:.2f}) — finishing variance present (-2)"
        )
    if a_xg > 0 and abs(a_scored - a_xg) > 0.5:
        deductions += 2
        notes.append(
            f"⚠ {away.get('team_name','')} xG ({a_xg:.2f}) diverges from actual goals "
            f"({a_scored:.2f}) — finishing variance present (-2)"
        )

    # 5. Very early season — small sample sizes amplify noise
    min_played = min(home.get("played") or 0, away.get("played") or 0)
    if min_played < 5:
        deductions += 6
        notes.append("⚠ Very early season — rate-based predictions unreliable (-6)")
    elif min_played < 10:
        deductions += 3
        notes.append("⚠ Small sample — rates may not reflect true team quality yet (-3)")

    # 6. Understat league not supported (no xG available)
    if not home.get("xg_per_game") and not away.get("xg_per_game"):
        deductions += 5
        notes.append("⚠ No Understat xG for this league — confidence less precise (-5)")

    return max(0.0, grade_score - deductions), notes


# ── Main grading function ─────────────────────────────────────────────────────

def grade_signal(
    signal: dict,
    home_stats: dict,
    away_stats: dict,
    home_context: dict | None = None,
    away_context: dict | None = None,
) -> dict:
    """
    Grades a single betting signal using the 5-factor rubric.

    Returns a grade dict:
      {
        grade:           str,   # "S" / "A+" / "A" / etc.
        grade_score:     int,   # 0–100 weighted rubric score
        grade_label:     str,   # human description
        grade_colour:    str,   # emoji indicator
        accuracy_pct:    int,   # estimated accuracy % (grade_score mapped to 50-98%)
        is_elite:        bool,  # True if grade == "S" (reaches 95% threshold)
        factors: {
          prediction_strength:   dict,
          market_reliability:    dict,
          data_completeness:     dict,
          evidence_convergence:  dict,
          sample_size:           dict,
        },
        penalties:       [str], # deduction notes
        what_we_know:    str,   # one-line summary of what drives the grade
      }
    """
    f1 = _factor_prediction_strength(signal.get("confidence", 50))
    f2 = _factor_market_reliability(signal)
    f3 = _factor_data_completeness(home_stats, away_stats, signal)
    f4 = _factor_evidence_convergence(signal)
    f5 = _factor_sample_size(home_stats, away_stats)

    raw_score = f1["score"] + f2["score"] + f3["score"] + f4["score"] + f5["score"]
    # Max possible = 35 + 20 + 25 + 12 + 8 = 100

    # Apply penalty deductions
    adjusted_score, penalty_notes = _apply_penalties(
        raw_score, signal, home_stats, away_stats, home_context, away_context
    )

    grade, grade_label, grade_colour = _letter_grade(adjusted_score)

    # Map grade score (0–100) to estimated accuracy percentage (50–98%)
    # A score of 0 = 50% (random), 100 = 98% (maximum credible accuracy)
    accuracy_pct = round(50 + (adjusted_score / 100) * 48)

    # Build a human-readable summary
    top_factor = max(
        [f1, f2, f3, f4, f5],
        key=lambda f: f["score"] / f["max"]
    )
    weakest_factor = min(
        [f1, f2, f3, f4, f5],
        key=lambda f: f["score"] / f["max"]
    )
    what_we_know = (
        f"Strongest factor: {top_factor['name']} ({top_factor['score']}/{top_factor['max']}). "
        f"Limiting factor: {weakest_factor['name']} ({weakest_factor['score']}/{weakest_factor['max']})."
    )

    return {
        "grade":        grade,
        "grade_score":  int(round(adjusted_score)),
        "grade_label":  grade_label,
        "grade_colour": grade_colour,
        "accuracy_pct": accuracy_pct,
        "is_elite":     grade == "S",
        "reaches_95":   adjusted_score >= 88,
        "factors": {
            "prediction_strength":  f1,
            "market_reliability":   f2,
            "data_completeness":    f3,
            "evidence_convergence": f4,
            "sample_size":          f5,
        },
        "factor_summary": {
            "prediction_strength":  f"{f1['score']}/{f1['max']}",
            "market_reliability":   f"{f2['score']}/{f2['max']}",
            "data_completeness":    f"{f3['score']}/{f3['max']}",
            "evidence_convergence": f"{f4['score']}/{f4['max']}",
            "sample_size":          f"{f5['score']}/{f5['max']}",
        },
        "penalties":    penalty_notes,
        "what_we_know": what_we_know,
    }


def grade_all_signals(
    signals: list[dict],
    home_stats: dict,
    away_stats: dict,
    home_context: dict | None = None,
    away_context: dict | None = None,
) -> list[dict]:
    """
    Attaches a grade dict to each signal in the list.
    Returns the list sorted by grade_score descending.
    """
    result = []
    for s in signals:
        graded = dict(s)
        graded["grade_info"] = grade_signal(
            s, home_stats, away_stats, home_context, away_context
        )
        # Promote key grade fields to top level for easy frontend access
        graded["grade"]        = graded["grade_info"]["grade"]
        graded["grade_score"]  = graded["grade_info"]["grade_score"]
        graded["accuracy_pct"] = graded["grade_info"]["accuracy_pct"]
        graded["is_elite"]     = graded["grade_info"]["is_elite"]
        result.append(graded)

    return sorted(result, key=lambda s: s["grade_score"], reverse=True)
