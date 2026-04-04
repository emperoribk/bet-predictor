"""
Bookie Market Map
=================
Maps our internal bet type codes to the market names bookmakers use,
plus metadata about each market.

Structure per entry:
  {
    "market":      str,   # The exact name bookmakers display
    "category":    str,   # Market category group
    "bookie_names": [str],# Alternative names different bookmakers use
    "description": str,   # What the bet means
    "data_sources": [str],# What data powers this prediction
    "reliability": str,   # "high" | "medium" | "low"
                          # how predictable this bet type is statistically
  }
"""

BOOKIE_MAP: dict[str, dict] = {

    # ─── Match Result ──────────────────────────────────────────────────────────
    "MATCH_WIN_HOME": {
        "market":       "1X2 — Home Win",
        "category":     "Match Result",
        "bookie_names": ["1", "Home Win", "Home", "H"],
        "description":  "Home team wins at full time",
        "data_sources": ["xG per game", "home/away form", "standings", "motivation tier"],
        "reliability":  "medium",
    },
    "MATCH_DRAW": {
        "market":       "1X2 — Draw",
        "category":     "Match Result",
        "bookie_names": ["X", "Draw", "Tie"],
        "description":  "Match ends level after 90 minutes",
        "data_sources": ["xG balance", "Poisson model", "form"],
        "reliability":  "low",
    },
    "MATCH_WIN_AWAY": {
        "market":       "1X2 — Away Win",
        "category":     "Match Result",
        "bookie_names": ["2", "Away Win", "Away", "A"],
        "description":  "Away team wins at full time",
        "data_sources": ["xG per game", "away form", "standings", "motivation tier"],
        "reliability":  "medium",
    },

    # ─── Double Chance ─────────────────────────────────────────────────────────
    "DOUBLE_CHANCE_1X": {
        "market":       "Double Chance — 1X",
        "category":     "Match Result",
        "bookie_names": ["1X", "Home or Draw", "Home/Draw"],
        "description":  "Home win OR draw — away team must NOT win",
        "data_sources": ["Poisson model", "home advantage"],
        "reliability":  "medium",
    },
    "DOUBLE_CHANCE_X2": {
        "market":       "Double Chance — X2",
        "category":     "Match Result",
        "bookie_names": ["X2", "Away or Draw", "Away/Draw"],
        "description":  "Away win OR draw — home team must NOT win",
        "data_sources": ["Poisson model", "away form"],
        "reliability":  "medium",
    },
    "DOUBLE_CHANCE_12": {
        "market":       "Double Chance — 12",
        "category":     "Match Result",
        "bookie_names": ["12", "Either Team to Win", "Home or Away"],
        "description":  "Either team wins — no draw (stake returned if draw in some books)",
        "data_sources": ["Poisson model", "match xG balance"],
        "reliability":  "medium",
    },

    # ─── Draw No Bet ───────────────────────────────────────────────────────────
    "DNB_HOME": {
        "market":       "Draw No Bet — Home",
        "category":     "Match Result",
        "bookie_names": ["DNB Home", "Draw No Bet Home"],
        "description":  "Back home to win; stake refunded if draw",
        "data_sources": ["Poisson model", "xG superiority"],
        "reliability":  "medium",
    },
    "DNB_AWAY": {
        "market":       "Draw No Bet — Away",
        "category":     "Match Result",
        "bookie_names": ["DNB Away", "Draw No Bet Away"],
        "description":  "Back away to win; stake refunded if draw",
        "data_sources": ["Poisson model", "xG superiority"],
        "reliability":  "medium",
    },

    # ─── Goals — Over/Under ────────────────────────────────────────────────────
    "OVER_05": {
        "market":       "Over/Under — Over 0.5 Goals",
        "category":     "Goals",
        "bookie_names": ["Over 0.5", "Over 0.5 Goals", "Total Goals Over 0.5"],
        "description":  "At least one goal in the match",
        "data_sources": ["xG per game"],
        "reliability":  "high",
    },
    "OVER_15": {
        "market":       "Over/Under — Over 1.5 Goals",
        "category":     "Goals",
        "bookie_names": ["Over 1.5", "Over 1.5 Goals"],
        "description":  "2 or more total goals",
        "data_sources": ["xG per game", "historical over-1.5 rates"],
        "reliability":  "high",
    },
    "OVER_25": {
        "market":       "Over/Under — Over 2.5 Goals",
        "category":     "Goals",
        "bookie_names": ["Over 2.5", "Over 2.5 Goals", "Total Goals Over 2.5"],
        "description":  "3 or more total goals",
        "data_sources": ["xG per game", "historical over-2.5 rates", "BTTS rate"],
        "reliability":  "high",
    },
    "OVER_35": {
        "market":       "Over/Under — Over 3.5 Goals",
        "category":     "Goals",
        "bookie_names": ["Over 3.5", "Over 3.5 Goals"],
        "description":  "4 or more total goals",
        "data_sources": ["xG per game", "historical over-3.5 rates"],
        "reliability":  "medium",
    },
    "OVER_45": {
        "market":       "Over/Under — Over 4.5 Goals",
        "category":     "Goals",
        "bookie_names": ["Over 4.5", "Over 4.5 Goals"],
        "description":  "5 or more total goals",
        "data_sources": ["xG per game", "historical rates"],
        "reliability":  "medium",
    },
    "UNDER_05": {
        "market":       "Over/Under — Under 0.5 Goals",
        "category":     "Goals",
        "bookie_names": ["Under 0.5", "Under 0.5 Goals", "No Goals"],
        "description":  "Goalless draw",
        "data_sources": ["xG per game", "clean sheet rates"],
        "reliability":  "medium",
    },
    "UNDER_15": {
        "market":       "Over/Under — Under 1.5 Goals",
        "category":     "Goals",
        "bookie_names": ["Under 1.5", "Under 1.5 Goals"],
        "description":  "0 or 1 total goals",
        "data_sources": ["xG per game", "clean sheet rates", "PPDA (pressing)"],
        "reliability":  "medium",
    },
    "UNDER_25": {
        "market":       "Over/Under — Under 2.5 Goals",
        "category":     "Goals",
        "bookie_names": ["Under 2.5", "Under 2.5 Goals"],
        "description":  "2 or fewer total goals",
        "data_sources": ["xG per game", "clean sheet rates"],
        "reliability":  "medium",
    },

    # ─── BTTS ──────────────────────────────────────────────────────────────────
    "BTTS_YES": {
        "market":       "Both Teams to Score — Yes",
        "category":     "Goals",
        "bookie_names": ["BTTS Yes", "Both Teams to Score Yes", "GG", "Goal-Goal"],
        "description":  "Both teams score at least one goal",
        "data_sources": ["BTTS rate", "xG per game", "clean sheet rate", "xGA"],
        "reliability":  "high",
    },
    "BTTS_NO": {
        "market":       "Both Teams to Score — No",
        "category":     "Goals",
        "bookie_names": ["BTTS No", "Both Teams to Score No", "NG", "No Goal"],
        "description":  "At least one team fails to score",
        "data_sources": ["BTTS rate", "clean sheet rates", "xG per game"],
        "reliability":  "high",
    },

    # ─── Goal Ranges ──────────────────────────────────────────────────────────
    "GOAL_RANGE_0_1": {
        "market":       "Goal Range — 0 to 1 Goals",
        "category":     "Goals",
        "bookie_names": ["0-1 Goals", "0 or 1 Goals"],
        "description":  "Match has 0 or 1 goal total",
        "data_sources": ["Poisson model", "clean sheet rates"],
        "reliability":  "medium",
    },
    "GOAL_RANGE_2_3": {
        "market":       "Goal Range — 2 to 3 Goals",
        "category":     "Goals",
        "bookie_names": ["2-3 Goals", "2 or 3 Goals"],
        "description":  "Match has exactly 2 or 3 goals",
        "data_sources": ["Poisson model", "xG per game"],
        "reliability":  "medium",
    },
    "GOAL_RANGE_4_PLUS": {
        "market":       "Goal Range — 4+ Goals",
        "category":     "Goals",
        "bookie_names": ["4+ Goals", "4 or More Goals"],
        "description":  "Match has 4 or more goals",
        "data_sources": ["Poisson model", "over-3.5 rates"],
        "reliability":  "medium",
    },

    # ─── Correct Score ─────────────────────────────────────────────────────────
    "CORRECT_SCORE": {
        "market":       "Correct Score",
        "category":     "Score",
        "bookie_names": ["Correct Score", "Exact Score", "Final Score"],
        "description":  "Predict the exact final scoreline",
        "data_sources": ["Poisson model with team xG per game"],
        "reliability":  "low",
    },

    # ─── Half-Time Markets ─────────────────────────────────────────────────────
    "HT_RESULT_HOME": {
        "market":       "Half-Time Result — Home",
        "category":     "Half & Period",
        "bookie_names": ["HT Home", "Half-Time Home Win", "1 (HT)"],
        "description":  "Home team leads at half time",
        "data_sources": ["xG per game", "first-half scoring rates"],
        "reliability":  "low",
    },
    "HT_RESULT_DRAW": {
        "market":       "Half-Time Result — Draw",
        "category":     "Half & Period",
        "bookie_names": ["HT Draw", "Half-Time Draw", "X (HT)"],
        "description":  "Match is level at half time",
        "data_sources": ["Poisson model (half xG)"],
        "reliability":  "low",
    },
    "HT_RESULT_AWAY": {
        "market":       "Half-Time Result — Away",
        "category":     "Half & Period",
        "bookie_names": ["HT Away", "Half-Time Away Win", "2 (HT)"],
        "description":  "Away team leads at half time",
        "data_sources": ["xG per game", "first-half scoring rates"],
        "reliability":  "low",
    },
    "GOAL_IN_BOTH_HALVES": {
        "market":       "Goal in Both Halves — Yes",
        "category":     "Half & Period",
        "bookie_names": ["Goal in Both Halves", "Score in Both Halves", "GBH Yes"],
        "description":  "At least one goal in both the first and second half",
        "data_sources": ["BTTS rate", "xG per game", "goal distribution"],
        "reliability":  "medium",
    },

    # ─── Team-Specific ─────────────────────────────────────────────────────────
    "HOME_TEAM_OVER_05": {
        "market":       "Team Total Goals — Home Over 0.5",
        "category":     "Team Goals",
        "bookie_names": ["Home Team Over 0.5 Goals", "Home to Score"],
        "description":  "Home team scores at least once",
        "data_sources": ["xG per game at home", "scored_per_game"],
        "reliability":  "high",
    },
    "HOME_TEAM_OVER_15": {
        "market":       "Team Total Goals — Home Over 1.5",
        "category":     "Team Goals",
        "bookie_names": ["Home Team Over 1.5 Goals", "Home 2+ Goals"],
        "description":  "Home team scores 2 or more",
        "data_sources": ["xG per game at home", "over rates"],
        "reliability":  "medium",
    },
    "HOME_TEAM_OVER_25": {
        "market":       "Team Total Goals — Home Over 2.5",
        "category":     "Team Goals",
        "bookie_names": ["Home Team Over 2.5 Goals", "Home 3+ Goals"],
        "description":  "Home team scores 3 or more",
        "data_sources": ["xG per game at home"],
        "reliability":  "medium",
    },
    "AWAY_TEAM_OVER_05": {
        "market":       "Team Total Goals — Away Over 0.5",
        "category":     "Team Goals",
        "bookie_names": ["Away Team Over 0.5 Goals", "Away to Score"],
        "description":  "Away team scores at least once",
        "data_sources": ["xG per game away", "scored_per_game"],
        "reliability":  "high",
    },
    "AWAY_TEAM_OVER_15": {
        "market":       "Team Total Goals — Away Over 1.5",
        "category":     "Team Goals",
        "bookie_names": ["Away Team Over 1.5 Goals", "Away 2+ Goals"],
        "description":  "Away team scores 2 or more",
        "data_sources": ["xG per game away", "over rates"],
        "reliability":  "medium",
    },
    "HOME_SCORE_2PLUS": {
        "market":       "Team Total Goals — Home Over 1.5",
        "category":     "Team Goals",
        "bookie_names": ["Home Team Over 1.5 Goals", "Home to Score 2+", "Home 2+ Goals"],
        "description":  "Home team scores 2 or more goals",
        "data_sources": ["home xG per game", "away xGA per game"],
        "reliability":  "medium",
    },
    "AWAY_SCORE_2PLUS": {
        "market":       "Team Total Goals — Away Over 1.5",
        "category":     "Team Goals",
        "bookie_names": ["Away Team Over 1.5 Goals", "Away to Score 2+", "Away 2+ Goals"],
        "description":  "Away team scores 2 or more goals",
        "data_sources": ["away xG per game", "home xGA per game"],
        "reliability":  "medium",
    },
    "HOME_CLEAN_SHEET": {
        "market":       "Clean Sheet — Home Yes",
        "category":     "Team Goals",
        "bookie_names": ["Home Clean Sheet Yes", "Home Team Clean Sheet", "HCS Yes"],
        "description":  "Home team concedes zero goals",
        "data_sources": ["home clean sheet rate", "away xG per game"],
        "reliability":  "medium",
    },
    "AWAY_CLEAN_SHEET": {
        "market":       "Clean Sheet — Away Yes",
        "category":     "Team Goals",
        "bookie_names": ["Away Clean Sheet Yes", "Away Team Clean Sheet", "ACS Yes"],
        "description":  "Away team concedes zero goals",
        "data_sources": ["away clean sheet rate", "home xG per game"],
        "reliability":  "medium",
    },
    "WIN_TO_NIL_HOME": {
        "market":       "Win to Nil — Home",
        "category":     "Team Goals",
        "bookie_names": ["Home Win to Nil", "Home Win & Clean Sheet", "Home W2N"],
        "description":  "Home team wins without conceding",
        "data_sources": ["home clean sheet rate", "home win rate", "Poisson model"],
        "reliability":  "medium",
    },
    "WIN_TO_NIL_AWAY": {
        "market":       "Win to Nil — Away",
        "category":     "Team Goals",
        "bookie_names": ["Away Win to Nil", "Away Win & Clean Sheet", "Away W2N"],
        "description":  "Away team wins without conceding",
        "data_sources": ["away clean sheet rate", "away win rate", "Poisson model"],
        "reliability":  "medium",
    },
    "TEAM_TO_SCORE_FIRST_HOME": {
        "market":       "First Goal — Home Team",
        "category":     "Team Goals",
        "bookie_names": ["Home Team to Score First", "First Goal Home", "Home First"],
        "description":  "Home team scores the opening goal",
        "data_sources": ["xG per game", "pressing (PPDA)", "form"],
        "reliability":  "medium",
    },
    "TEAM_TO_SCORE_FIRST_AWAY": {
        "market":       "First Goal — Away Team",
        "category":     "Team Goals",
        "bookie_names": ["Away Team to Score First", "First Goal Away", "Away First"],
        "description":  "Away team scores the opening goal",
        "data_sources": ["xG per game", "pressing (PPDA)", "form"],
        "reliability":  "medium",
    },

    # ─── Player Markets ────────────────────────────────────────────────────────
    "PLAYER_ANYTIME_SCORER": {
        "market":       "Anytime Goalscorer",
        "category":     "Player",
        "bookie_names": ["Anytime Goalscorer", "To Score", "Goalscorer"],
        "description":  "Player scores at any point in the match",
        "data_sources": ["player xG per game", "npxG", "shots inside box"],
        "reliability":  "medium",
    },
    "PLAYER_SHOTS_ON_TARGET": {
        "market":       "Player Shots on Target 1+",
        "category":     "Player",
        "bookie_names": ["Player Shots on Target", "1+ Shots on Target"],
        "description":  "Player registers at least one shot on target",
        "data_sources": ["player xG per game", "shots inside box per 90"],
        "reliability":  "medium",
    },
    "PLAYER_BRACE": {
        "market":       "Player to Score 2+ Goals",
        "category":     "Player",
        "bookie_names": ["Player Brace", "2+ Goals", "Score 2 or More"],
        "description":  "Player scores 2 or more goals in the match",
        "data_sources": ["player xG per game", "npxG per game", "shots"],
        "reliability":  "low",
    },

    # ─── Cards & Discipline ────────────────────────────────────────────────────
    "CARDS_OVER_3_5": {
        "market":       "Total Cards — Over 3.5",
        "category":     "Cards",
        "bookie_names": ["Over 3.5 Cards", "Total Bookings Over 3.5", "Cards 4+"],
        "description":  "4 or more yellow/red cards in total",
        "data_sources": ["yellow cards per game (both teams)", "PPDA (pressing style)"],
        "reliability":  "medium",
    },
    "CARDS_OVER_4_5": {
        "market":       "Total Cards — Over 4.5",
        "category":     "Cards",
        "bookie_names": ["Over 4.5 Cards", "Cards 5+", "Total Bookings Over 4.5"],
        "description":  "5 or more yellow/red cards in total",
        "data_sources": ["yellow cards per game (both teams)", "historical card rates"],
        "reliability":  "medium",
    },
    "RED_CARD_YES": {
        "market":       "Red Card in Match — Yes",
        "category":     "Cards",
        "bookie_names": ["Red Card Yes", "Any Red Card", "Red Card Shown"],
        "description":  "At least one red card shown in the match",
        "data_sources": ["yellow card history", "match intensity indicators"],
        "reliability":  "low",
    },

    # ─── Corners ──────────────────────────────────────────────────────────────
    "CORNERS_OVER_95": {
        "market":       "Total Corners — Over 9.5",
        "category":     "Corners",
        "bookie_names": ["Over 9.5 Corners", "Total Corners Over 9.5", "Corners 10+"],
        "description":  "10 or more corners in the match",
        "data_sources": ["attacking play style", "possession patterns", "xG volume"],
        "reliability":  "medium",
    },
    "CORNERS_OVER_105": {
        "market":       "Total Corners — Over 10.5",
        "category":     "Corners",
        "bookie_names": ["Over 10.5 Corners", "Corners 11+"],
        "description":  "11 or more corners in the match",
        "data_sources": ["attacking play style", "deep completions"],
        "reliability":  "medium",
    },

    # ─── Handicap ──────────────────────────────────────────────────────────────
    "ASIAN_HANDICAP_HOME_MINUS1": {
        "market":       "Asian Handicap — Home -1",
        "category":     "Handicap",
        "bookie_names": ["Home -1 AH", "Asian Handicap -1 Home"],
        "description":  "Home team wins by 2+ goals after -1 handicap applied",
        "data_sources": ["xG superiority", "Poisson model", "home form"],
        "reliability":  "medium",
    },
    "ASIAN_HANDICAP_HOME_MINUS05": {
        "market":       "Asian Handicap — Home -0.5",
        "category":     "Handicap",
        "bookie_names": ["Home -0.5 AH", "Asian Handicap -0.5 Home"],
        "description":  "Home team wins (no draw margin needed)",
        "data_sources": ["xG superiority", "Poisson model"],
        "reliability":  "medium",
    },
    "ASIAN_HANDICAP_AWAY_PLUS05": {
        "market":       "Asian Handicap — Away +0.5",
        "category":     "Handicap",
        "bookie_names": ["Away +0.5 AH", "Asian Handicap +0.5 Away"],
        "description":  "Away team wins or draws",
        "data_sources": ["xG balance", "Poisson model"],
        "reliability":  "medium",
    },

    # ─── Specials ──────────────────────────────────────────────────────────────
    "ODD_GOALS": {
        "market":       "Odd/Even Goals — Odd",
        "category":     "Specials",
        "bookie_names": ["Odd Goals", "Odd Total Goals"],
        "description":  "Match ends with an odd number of total goals (1, 3, 5...)",
        "data_sources": ["Poisson model"],
        "reliability":  "low",
    },
    "EVEN_GOALS": {
        "market":       "Odd/Even Goals — Even",
        "category":     "Specials",
        "bookie_names": ["Even Goals", "Even Total Goals"],
        "description":  "Match ends with an even number of goals (0, 2, 4...)",
        "data_sources": ["Poisson model"],
        "reliability":  "low",
    },
    "FIRST_GOAL_UNDER_25MIN": {
        "market":       "Time of First Goal — Under 25 Minutes",
        "category":     "Specials",
        "bookie_names": ["First Goal Under 25 Minutes", "Early Goal", "1st Goal Before 25'"],
        "description":  "The first goal arrives within the first 25 minutes",
        "data_sources": ["xG per game", "PPDA (pressing intensity)", "attack xG opening phase"],
        "reliability":  "low",
    },
}

# ── Category groupings (for frontend display) ─────────────────────────────────
CATEGORIES = {
    "Match Result":  ["MATCH_WIN_HOME", "MATCH_DRAW", "MATCH_WIN_AWAY",
                      "DOUBLE_CHANCE_1X", "DOUBLE_CHANCE_X2", "DOUBLE_CHANCE_12",
                      "DNB_HOME", "DNB_AWAY"],
    "Goals":         ["OVER_05", "OVER_15", "OVER_25", "OVER_35", "OVER_45",
                      "UNDER_05", "UNDER_15", "UNDER_25",
                      "BTTS_YES", "BTTS_NO",
                      "GOAL_RANGE_0_1", "GOAL_RANGE_2_3", "GOAL_RANGE_4_PLUS"],
    "Team Goals":    ["HOME_TEAM_OVER_05", "HOME_TEAM_OVER_15", "HOME_TEAM_OVER_25",
                      "AWAY_TEAM_OVER_05", "AWAY_TEAM_OVER_15",
                      "HOME_SCORE_2PLUS", "AWAY_SCORE_2PLUS",
                      "HOME_CLEAN_SHEET", "AWAY_CLEAN_SHEET",
                      "WIN_TO_NIL_HOME", "WIN_TO_NIL_AWAY",
                      "TEAM_TO_SCORE_FIRST_HOME", "TEAM_TO_SCORE_FIRST_AWAY"],
    "Half & Period": ["HT_RESULT_HOME", "HT_RESULT_DRAW", "HT_RESULT_AWAY",
                      "GOAL_IN_BOTH_HALVES"],
    "Score":         ["CORRECT_SCORE"],
    "Player":        ["PLAYER_ANYTIME_SCORER", "PLAYER_SHOTS_ON_TARGET", "PLAYER_BRACE"],
    "Cards":         ["CARDS_OVER_3_5", "CARDS_OVER_4_5", "RED_CARD_YES"],
    "Corners":       ["CORNERS_OVER_95", "CORNERS_OVER_105"],
    "Handicap":      ["ASIAN_HANDICAP_HOME_MINUS1", "ASIAN_HANDICAP_HOME_MINUS05",
                      "ASIAN_HANDICAP_AWAY_PLUS05"],
    "Specials":      ["ODD_GOALS", "EVEN_GOALS", "FIRST_GOAL_UNDER_25MIN"],
}

# Reliability tier descriptions
RELIABILITY_NOTES = {
    "high":   "Statistically stable — rates converge reliably over 20+ games of sample size",
    "medium": "Moderately predictable — xG-based probabilities carry meaningful signal",
    "low":    "High variance — treat as supplementary, not primary signal",
}


def get_market_info(bet_type: str) -> dict:
    """Returns the full bookie map entry for a bet type, or an empty dict."""
    return BOOKIE_MAP.get(bet_type, {})


def enrich_signal(signal: dict) -> dict:
    """
    Attaches bookie map metadata to a prediction signal dict.
    Adds: market, category, bookie_names, reliability, data_sources.
    """
    info = get_market_info(signal.get("type", ""))
    return {
        **signal,
        "market":       info.get("market",       signal.get("label", "")),
        "category":     info.get("category",     "Other"),
        "bookie_names": info.get("bookie_names", []),
        "reliability":  info.get("reliability",  "medium"),
        "data_sources": info.get("data_sources", []),
    }
