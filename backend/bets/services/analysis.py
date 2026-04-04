"""
Analysis engine — reads live match data and returns a status + reason
for each match in the bet slip.

Statuses:
  PENDING  — match not started yet
  SAFE     — bet selection looking good
  DANGER   — concerning signs, may not win
  CRITICAL — very likely to lose / serious threat
  WON      — match finished, selection won
  LOST     — match finished, selection lost
"""


def _is_home_pick(match) -> bool:
    """Returns True if the user's pick is the home team."""
    pick = (match.user_pick or "").lower()
    home = (match.home_team or "").lower()
    return pick in home or home in pick


def _is_away_pick(match) -> bool:
    pick = (match.user_pick or "").lower()
    away = (match.away_team or "").lower()
    return pick in away or away in pick


def _is_draw_pick(match) -> bool:
    pick = (match.user_pick or "").lower()
    return pick in ("draw", "x", "tie")


def _user_team_score(match) -> tuple[int, int]:
    """Returns (user_team_score, opponent_score)."""
    if _is_home_pick(match):
        return match.score_home, match.score_away
    if _is_away_pick(match):
        return match.score_away, match.score_home
    return 0, 0


def _user_team_possession(match) -> tuple[float | None, float | None]:
    """Returns (user_team_possession, opponent_possession)."""
    if _is_home_pick(match):
        return match.possession_home, match.possession_away
    if _is_away_pick(match):
        return match.possession_away, match.possession_home
    return None, None


def _user_team_shots(match) -> tuple[int | None, int | None]:
    """Returns (user_shots_on_target, opponent_shots_on_target)."""
    if _is_home_pick(match):
        return match.shots_on_target_home, match.shots_on_target_away
    if _is_away_pick(match):
        return match.shots_on_target_away, match.shots_on_target_home
    return None, None


def _user_red_cards(match) -> int:
    if _is_home_pick(match):
        return match.red_cards_home or 0
    if _is_away_pick(match):
        return match.red_cards_away or 0
    return 0


def _opponent_red_cards(match) -> int:
    if _is_home_pick(match):
        return match.red_cards_away or 0
    if _is_away_pick(match):
        return match.red_cards_home or 0
    return 0


def _late_game(match) -> bool:
    return (match.match_minute or 0) >= 70


def _very_late_game(match) -> bool:
    return (match.match_minute or 0) >= 80


def _check_finished(match) -> tuple[str, str] | None:
    """If match is finished, determine WON or LOST."""
    status = (match.match_status or "").upper()
    if status not in ("FINISHED", "AWARDED"):
        return None

    my_score, opp_score = _user_team_score(match)

    if _is_draw_pick(match):
        if my_score == opp_score:
            return "WON", "Match finished as a draw — your pick is correct."
        return "LOST", "Match did not end in a draw."

    if my_score > opp_score:
        return "WON", f"Match finished. Your team won {my_score}-{opp_score}."
    if my_score == opp_score:
        return "LOST", f"Match finished as a draw {my_score}-{opp_score} — your win pick lost."
    return "LOST", f"Your team lost {my_score}-{opp_score}."


def _is_to_score_pick(match) -> bool:
    return "to score" in (match.user_pick or "").lower()


def _to_score_team_goal_count(match) -> tuple[int, str | None, int]:
    """
    For a 'X to score' pick, returns (goals_scored, team_name, required_goals).
    required_goals is the minimum number the team must score (default 1).
    Examples:
      "Aston Villa to score"    → required=1
      "Aston Villa to score 2+" → required=2
      "Aston Villa to score 3+" → required=3
      "Aston Villa NOT to score"→ required=0 (handled by negated flag)
    """
    import re
    pick = (match.user_pick or "").lower()
    home = (match.home_team or "")
    away = (match.away_team or "")

    if "not to score" in pick:
        team_part = pick.replace("not to score", "").strip()
        required = 0
    else:
        # Check for "to score N+" pattern
        m = re.search(r'to score\s+(\d+)\+?', pick)
        required = int(m.group(1)) if m else 1
        team_part = pick.replace("to score", "").strip()
        # Strip trailing number/+ from team_part
        team_part = re.sub(r'\s*\d+\+?\s*$', '', team_part).strip()

    # Identify which team
    if team_part and (team_part in home.lower() or home.lower() in team_part):
        team = home
        goals = match.score_home or 0
    elif team_part and (team_part in away.lower() or away.lower() in team_part):
        team = away
        goals = match.score_away or 0
    else:
        team = None
        goals = 0

    return goals, team, required


def _analyse_to_score(match) -> tuple[str, str]:
    """Analysis for 'Team to score' / 'Team to score N+' / 'Team NOT to score' picks."""
    pick = (match.user_pick or "").lower()
    negated = "not to score" in pick
    goals, team, required = _to_score_team_goal_count(match)
    team_label = team or "The team"
    minute = match.match_minute or 0
    needed = max(1, required)  # goals needed to win the pick

    api_status = (match.match_status or "SCHEDULED").upper()
    finished = api_status in ("FINISHED", "AWARDED")

    goal_word = f"goal{'s' if goals != 1 else ''}"
    need_str = f"{needed}+" if needed > 1 else "at least 1"

    if not negated:
        # Bet: team MUST score 'needed' goals
        if goals >= needed:
            if finished:
                return "WON", f"{team_label} scored {goals} {goal_word} — pick correct."
            return "SAFE", f"{team_label} has scored {goals} {goal_word} (needed {need_str}) — pick is in."
        else:
            still_need = needed - goals
            if finished:
                return "LOST", f"{team_label} scored {goals} — needed {need_str}, pick failed."
            if minute >= 80:
                return "CRITICAL", f"{team_label} needs {still_need} more goal{'s' if still_need > 1 else ''} with very little time left."
            if minute >= 65:
                return "DANGER", f"{team_label} needs {still_need} more goal{'s' if still_need > 1 else ''} with {90 - minute} mins left."
            return "SAFE", f"{team_label} has {goals} goal{'s' if goals != 1 else ''} — needs {need_str} total, {90 - minute} mins left."
    else:
        # Bet: team should NOT score
        if goals >= 1:
            if finished:
                return "LOST", f"{team_label} scored {goals} — 'not to score' pick failed."
            return "CRITICAL", f"{team_label} has already scored {goals} — pick is failing."
        else:
            if finished:
                return "WON", f"{team_label} kept off the scoresheet — pick correct."
            if minute >= 70:
                return "SAFE", f"{team_label} hasn't scored — holding on with {90 - minute} mins left."
            return "SAFE", f"{team_label} yet to score — pick looking good."


def _analyse_both_teams_to_score(match) -> tuple[str, str]:
    """Analysis for 'Both teams to score' picks."""
    pick = (match.user_pick or "").lower()
    negated = "not to score" in pick
    home_goals = match.score_home or 0
    away_goals = match.score_away or 0
    both_scored = home_goals >= 1 and away_goals >= 1
    neither_scored = home_goals == 0 and away_goals == 0
    minute = match.match_minute or 0
    api_status = (match.match_status or "SCHEDULED").upper()
    finished = api_status in ("FINISHED", "AWARDED")

    if not negated:
        if both_scored:
            return ("WON" if finished else "SAFE"), f"Both teams have scored ({match.score_home}–{match.score_away}) — pick is in."
        if finished:
            return "LOST", f"Not both teams scored ({match.score_home}–{match.score_away})."
        if minute >= 80 and not both_scored:
            return "CRITICAL", f"Only one side has scored with minutes left — very hard to recover."
        if minute >= 65 and neither_scored:
            return "DANGER", f"Neither team has scored yet at {minute}' — this may not land."
        return "SAFE", f"In progress ({match.score_home}–{match.score_away}) — waiting for goals."
    else:
        if both_scored:
            if finished:
                return "LOST", f"Both teams scored ({match.score_home}–{match.score_away}) — your 'no BTTS' pick failed."
            return "CRITICAL", f"Both teams have already scored — pick is failing."
        if finished:
            return "WON", f"Not both teams scored ({match.score_home}–{match.score_away}) — pick correct."
        return "SAFE", f"BTTS not yet — pick intact at {minute}'."


def analyse_match(match) -> tuple[str, str]:
    """
    Core analysis function.
    Returns (status, reason) for a single Match model instance.
    """
    # Simulated Reality League — virtual matches, no real data exists
    home = (match.home_team or "").upper()
    away = (match.away_team or "").upper()
    if "SRL" in home or "SRL" in away:
        return "VIRTUAL", "Simulated Reality League match — no live data available."

    # Match not found in football-data.org (unsupported league)
    if not match.fixture_id:
        return "UNTRACKED", "League not covered by our data provider."

    api_status = (match.match_status or "SCHEDULED").upper()

    # Not started
    if api_status in ("SCHEDULED", "TIMED"):
        return "PENDING", "Match has not started yet."

    # ── Special market types ──────────────────────────────────
    pick_lower = (match.user_pick or "").lower()

    if "both teams to score" in pick_lower or "both teams not to score" in pick_lower:
        return _analyse_both_teams_to_score(match)

    if _is_to_score_pick(match):
        return _analyse_to_score(match)

    # Finished (standard 1X2 / over-under)
    result = _check_finished(match)
    if result:
        return result

    # --- Live match analysis ---
    my_score, opp_score = _user_team_score(match)
    my_poss, opp_poss = _user_team_possession(match)
    my_shots, opp_shots = _user_team_shots(match)
    my_reds = _user_red_cards(match)
    opp_reds = _opponent_red_cards(match)
    minute = match.match_minute or 0

    # Draw pick analysis
    if _is_draw_pick(match):
        if my_score != opp_score:
            diff = abs(my_score - opp_score)
            if diff >= 2 or _very_late_game(match):
                return "CRITICAL", f"Score is {match.score_home}-{match.score_away} — very unlikely to end in a draw."
            return "DANGER", f"Score is {match.score_home}-{match.score_away} — not a draw yet."
        if _late_game(match) and my_score == opp_score:
            return "SAFE", f"Score is level {my_score}-{opp_score} in the {minute}th minute."
        return "SAFE", f"Score is level {my_score}-{opp_score}."

    reasons = []
    danger_points = 0
    critical_points = 0

    # 1. Score check
    if my_score > opp_score:
        score_gap = my_score - opp_score
        if score_gap >= 2:
            reasons.append(f"Winning comfortably {my_score}-{opp_score}.")
        else:
            reasons.append(f"Leading {my_score}-{opp_score}.")
    elif my_score == opp_score:
        reasons.append(f"Score is level {my_score}-{opp_score}.")
        if _late_game(match):
            danger_points += 2
            reasons.append("Level in the late stages — risky.")
    else:
        diff = opp_score - my_score
        reasons.append(f"Losing {my_score}-{opp_score}.")
        danger_points += 2 * diff
        critical_points += diff
        if _very_late_game(match):
            critical_points += 2
            reasons.append("Losing in the 80th minute or later — very hard to recover.")

    # 2. Red cards
    if my_reds >= 1:
        critical_points += 2
        reasons.append(f"Your team is down to {11 - my_reds} men (red card).")
    if opp_reds >= 1:
        danger_points -= 2
        reasons.append(f"Opponent has a red card — numerical advantage for your team.")

    # 3. Possession check
    if my_poss is not None and opp_poss is not None:
        if opp_poss >= 65:
            danger_points += 2
            reasons.append(f"Opponent dominating possession ({opp_poss:.0f}%).")
        elif opp_poss >= 55:
            danger_points += 1
            reasons.append(f"Opponent has more possession ({opp_poss:.0f}%).")
        elif my_poss >= 55:
            reasons.append(f"Your team controlling possession ({my_poss:.0f}%).")

    # 4. Shots on target check
    if my_shots is not None and opp_shots is not None:
        if opp_shots >= 6:
            danger_points += 2
            critical_points += 1
            reasons.append(f"Opponent has {opp_shots} shots on target — heavy pressure.")
        elif opp_shots >= 4:
            danger_points += 1
            reasons.append(f"Opponent has {opp_shots} shots on target.")
        elif my_shots and my_shots >= 4:
            reasons.append(f"Your team creating chances ({my_shots} shots on target).")

    # 5. Late attacker substitution by opponent (chasing game)
    if _late_game(match):
        opp_name = match.away_team if _is_home_pick(match) else match.home_team
        late_opp_subs = [
            e for e in (match.events or [])
            if e.get("type") == "SUBSTITUTION"
            and (e.get("team") or "").lower() in (opp_name or "").lower()
            and (e.get("minute") or 0) >= 65
        ]
        if late_opp_subs:
            danger_points += 1
            reasons.append("Opponent made late substitution(s) — pushing for a goal.")

    # Decide final status
    reason_text = " ".join(reasons)

    if critical_points >= 3:
        return "CRITICAL", reason_text
    if danger_points >= 3:
        return "DANGER", reason_text
    if danger_points >= 1 and my_score <= opp_score:
        return "DANGER", reason_text

    return "SAFE", reason_text


def analyse_bet_slip(matches) -> dict:
    """
    Runs analysis on all matches in a bet slip.
    Returns a summary dict with per-match results and overall accumulator status.
    """
    results = []
    accumulator_dead = False

    for match in matches:
        status, reason = analyse_match(match)
        kickoff_iso = None
        if match.kickoff_time:
            try:
                kickoff_iso = match.kickoff_time.isoformat()
            except Exception:
                pass

        results.append({
            "id": match.id,
            "home_team": match.home_team,
            "away_team": match.away_team,
            "score": f"{match.score_home}-{match.score_away}",
            "minute": match.match_minute,
            "match_status": match.match_status,
            "kickoff_time": kickoff_iso,
            "fixture_id": match.fixture_id,
            "user_pick": match.user_pick,
            "analysis_status": status,
            "analysis_reason": reason,
            "possession_home": match.possession_home,
            "possession_away": match.possession_away,
            "shots_on_target_home": match.shots_on_target_home,
            "shots_on_target_away": match.shots_on_target_away,
            "red_cards_home": match.red_cards_home,
            "red_cards_away": match.red_cards_away,
            "events": match.events,
        })
        if status == "LOST":
            accumulator_dead = True

    counts = {s: sum(1 for r in results if r["analysis_status"] == s)
              for s in ("PENDING", "SAFE", "DANGER", "CRITICAL", "WON", "LOST", "VIRTUAL", "UNTRACKED")}

    return {
        "matches": results,
        "summary": counts,
        "accumulator_alive": not accumulator_dead,
    }
