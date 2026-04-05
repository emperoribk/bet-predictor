"""
Django management command: python manage.py run_accumulator [--date 2026-03-28]

Builds a daily accumulator from raw Poisson probabilities — bypasses the
full grading pipeline entirely so MATCH_WIN and BTTS_YES are no longer
blocked by the internal P(0-0) filter or the high grade thresholds.

Strategy
========
For every fixture on the target date:
  1. Pull team season stats (same DB-backed call as run_predictions)
  2. Compute h_xg / a_xg via _expected_xg_for_match()
  3. Derive raw Poisson probabilities for six markets:
       HOME_WIN   p_home
       AWAY_WIN   p_away
       DC_1X      p_home + p_draw
       DC_X2      p_draw + p_away
       OVER_25    P(total goals >= 3)
       BTTS       P(h >= 1 AND a >= 1)
  4. Keep the single best pick per fixture (highest probability that's
     in the target window 0.54 – 0.82).
  5. Sort all fixture picks by probability descending.
  6. Greedily build the accumulator: add picks until combined odds >= 2.0
     OR we have MAX_PICKS picks (capped at 5).

Odds are estimated as 1 / probability (fair odds, no margin).

For backtesting pass --backtest to run across all historical dates and
print a daily P&L table.
"""

import io, sys
from datetime import date as _date_cls
from django.core.management.base import BaseCommand

# Force UTF-8 and line-buffered output (prevents buffering when piped/captured)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# ── Configuration ─────────────────────────────────────────────────────────────
MIN_PROB    = 0.54   # below this: too uncertain to include
MAX_PROB    = 0.82   # above this: odds too short to contribute meaningfully
TARGET_ODDS = 2.0    # stop adding picks once combined odds reach this
MAX_PICKS   = 3      # never more than 3 legs (4+ legs reduce win rate too much)

# Markets that can appear in the accumulator (ordered by preference when
# two picks on the same fixture tie on probability — prefer safer markets)
MARKET_PRIORITY = ["DC_1X", "DC_X2", "OVER_25"]

TARGET_LEAGUES = [
    ("Premier League",   "England",      "PL"),
    ("La Liga",          "Spain",        "PD"),
    ("Primera Division", "Spain",        "PD"),
    ("Bundesliga",       "Germany",      "BL1"),
    ("Serie A",          "Italy",        "SA"),
    ("Ligue 1",          "France",       "FL1"),
    ("Eredivisie",       "Netherlands",  "DED"),
    ("Primeira Liga",    "Portugal",     "PPL"),
    ("Championship",     "England",      "ELC"),
    ("Premier League",   "Scotland",     "SPL"),
    ("Jupiler Pro League","Belgium",     "BJL"),
    ("Süper Lig",        "Turkey",       "TSL"),
    ("2. Bundesliga",    "Germany",      "BL2"),
    ("Super League",     "Switzerland",  "SSL"),
    ("UEFA Champions League", "Europe",  "CL"),
    ("UEFA Europa League",    "Europe",  "EL"),
    ("UEFA Conference League","Europe",  "UECL"),
]
_TARGET_CODES = {t[2] for t in TARGET_LEAGUES}


class Command(BaseCommand):
    help = "Build a daily accumulator using raw Poisson probabilities"

    def add_arguments(self, parser):
        parser.add_argument("--date",      type=str, default=None,
                            help="Date YYYY-MM-DD (default: today)")
        parser.add_argument("--backtest",  action="store_true",
                            help="Run across all historical dates and print P&L")
        parser.add_argument("--start-date", type=str, default=None,
                            help="Backtest start date YYYY-MM-DD (default: all history)")
        parser.add_argument("--min-prob",  type=float, default=MIN_PROB)
        parser.add_argument("--max-prob",  type=float, default=MAX_PROB)
        parser.add_argument("--target-odds", type=float, default=TARGET_ODDS)
        parser.add_argument("--max-picks", type=int, default=MAX_PICKS)

    def handle(self, *args, **options):
        min_prob    = options["min_prob"]
        max_prob    = options["max_prob"]
        target_odds = options["target_odds"]
        max_picks   = options["max_picks"]

        if options["backtest"]:
            self._backtest(min_prob, max_prob, target_odds, max_picks,
                           start_date=options.get("start_date"))
        else:
            target_date = options["date"] or str(_date_cls.today())
            self._run_date(target_date, min_prob, max_prob, target_odds, max_picks,
                           verbose=True)

    # ─────────────────────────────────────────────────────────────────────────
    def _backtest(self, min_prob, max_prob, target_odds, max_picks, start_date=None):
        from bets.models import HistoricalFixture
        qs = HistoricalFixture.objects.filter(home_score__isnull=False)
        if start_date:
            qs = qs.filter(match_date__gte=start_date)
        dates = (
            qs.values_list("match_date", flat=True)
            .distinct()
            .order_by("match_date")
        )
        dates = [str(d) for d in dates]

        total = hits = miss = no_bet = 0
        odds_sum = 0.0
        results_log = []

        for d in dates:
            acc = self._run_date(d, min_prob, max_prob, target_odds, max_picks,
                                 verbose=False)
            if not acc:
                no_bet += 1
                continue

            total += 1
            combined_odds = 1.0
            all_win = True
            pick_strs = []
            for pick in acc:
                combined_odds *= pick["odds"]
                won = pick.get("won")
                if won is False:
                    all_win = False
                icon = "[W]" if won else "[L]" if won is False else "[?]"
                pick_strs.append(
                    f"  {icon} {pick['label']} [{pick.get('score','?')}] @{pick['odds']:.2f}"
                )

            result = "WIN " if all_win else "LOSS"
            if all_win:
                hits += 1
                odds_sum += combined_odds
            else:
                miss += 1

            results_log.append((d, result, combined_odds, pick_strs))

        print(f"\n{'='*80}")
        print(f"  ACCUMULATOR BACKTEST   min_prob={min_prob}  max_prob={max_prob}"
              f"  target_odds={target_odds}  max_picks={max_picks}")
        print(f"{'='*80}\n")

        # Summary stats
        days_with_bet = total
        days_above_target = sum(1 for _, _, o, _ in results_log if o >= target_odds)
        avg_odds = (sum(o for _, _, o, _ in results_log) / total) if total else 0
        avg_picks = (
            sum(len(p) for _, _, _, p in results_log) / total
        ) if total else 0

        print(f"Days with a pick      : {days_with_bet}/{len(dates)+no_bet}")
        print(f"Days >= {target_odds:.1f} combined  : {days_above_target}/{days_with_bet}"
              f" ({days_above_target/days_with_bet*100:.0f}%)" if days_with_bet else "")
        print(f"Win rate (all correct): {hits}/{days_with_bet}"
              f" ({hits/days_with_bet*100:.0f}%)" if days_with_bet else "")
        print(f"Avg combined odds     : {avg_odds:.2f}")
        print(f"Avg picks per day     : {avg_picks:.1f}")
        print()

        print(f"{'DATE':<12} {'RESULT':<6} {'ODDS':<6} {'PICKS'}")
        print("-" * 90)
        for d, result, combined_odds, pick_strs in results_log:
            n = len(pick_strs)
            print(f"\n{d}  {result}  @{combined_odds:.2f}  ({n} picks):")
            for ps in pick_strs:
                print(ps)

    # ─────────────────────────────────────────────────────────────────────────
    def _run_date(self, target_date: str, min_prob, max_prob, target_odds,
                  max_picks, verbose=True):
        """
        Returns list of pick dicts for the accumulator on this date, or [].
        Each pick: {fixture, label, market, probability, odds, won, score}
        """
        from bets.services.football_data import get_fixtures_by_date, get_team_season_stats
        from bets.services.daily_predictions import (
            _match_probs, _score_probs, _expected_xg_for_match, _poisson
        )

        try:
            fixtures = get_fixtures_by_date(target_date)
        except Exception:
            return []

        league_fixtures = [
            f for f in fixtures
            if f.get("competition_code") in _TARGET_CODES
        ]

        candidates = []  # one per fixture: best pick in prob window

        for fix in league_fixtures:
            home_name = fix["home_team"]
            away_name = fix["away_team"]
            comp_code = fix.get("competition_code", "PL")

            try:
                home_stats = get_team_season_stats(
                    team_id=fix.get("home_team_id", 0),
                    team_name=home_name,
                    competition_code=comp_code,
                    match_date=target_date,
                )
                away_stats = get_team_season_stats(
                    team_id=fix.get("away_team_id", 0),
                    team_name=away_name,
                    competition_code=comp_code,
                    match_date=target_date,
                )
                if not home_stats or not away_stats:
                    continue

                h_xg, a_xg = _expected_xg_for_match(home_stats, away_stats)
                if h_xg <= 0 or a_xg <= 0:
                    continue

                p_home, p_draw, p_away = _match_probs(h_xg, a_xg)
                scores = _score_probs(h_xg, a_xg)

                # Over 2.5: P(total >= 3)
                p_over25 = sum(p for (h, a), p in scores.items() if h + a >= 3)
                # BTTS: P(h >= 1 AND a >= 1)
                p_btts = sum(p for (h, a), p in scores.items() if h >= 1 and a >= 1)
                # P(0-0) for diagnostics
                p_00 = _poisson(h_xg, 0) * _poisson(a_xg, 0)

                # Require minimum games played — skips early-season qualifiers
                # where small teams have 0-3 games of data and xG is unreliable.
                min_games = min(
                    home_stats.get("played", 0) or 0,
                    away_stats.get("played", 0) or 0,
                )
                if min_games < 3:
                    continue

                # Build candidate markets — DC only + OVER_25 (gated by xG sum)
                # Outright wins (HOME_WIN/AWAY_WIN) removed: they fail at ~35%
                # at 65-75% probability, too risky for multi-leg accumulators.
                # BTTS removed: fails when either team keeps a clean sheet (~40%).
                markets = {
                    "DC_1X":   (p_home + p_draw, f"{home_name} or Draw (1X)"),
                    "DC_X2":   (p_draw + p_away, f"Draw or {away_name} (X2)"),
                }
                # OVER_25 excluded — DC picks alone are statistically more reliable
                # at consistent win rates; OVER_25 introduces unnecessary variance.

                # Keep only picks in the probability window
                valid = [
                    (mkt, prob, label)
                    for mkt, (prob, label) in markets.items()
                    if min_prob <= prob <= max_prob
                ]

                if not valid:
                    continue

                # Pick the single best for this fixture:
                # Primary: highest probability (most certain)
                # Tie-break: market priority (DC > goals > outright)
                priority = {m: i for i, m in enumerate(MARKET_PRIORITY)}
                valid.sort(key=lambda x: (-x[1], priority.get(x[0], 99)))
                mkt, prob, label = valid[0]

                # Determine outcome if result is known
                home_score = fix.get("home_score")
                away_score = fix.get("away_score")
                won = None
                score_str = "?"
                if home_score is not None and away_score is not None:
                    score_str = f"{home_score}-{away_score}"
                    if mkt == "HOME_WIN":
                        won = home_score > away_score
                    elif mkt == "AWAY_WIN":
                        won = away_score > home_score
                    elif mkt == "DC_1X":
                        won = home_score >= away_score
                    elif mkt == "DC_X2":
                        won = away_score >= home_score
                    elif mkt == "OVER_25":
                        won = (home_score + away_score) >= 3
                    elif mkt == "BTTS":
                        won = home_score >= 1 and away_score >= 1

                candidates.append({
                    "fixture":  f"{home_name} vs {away_name}",
                    "label":    label,
                    "market":   mkt,
                    "probability": round(prob, 4),
                    "odds":     round(1 / prob, 2),
                    "won":      won,
                    "score":    score_str,
                    "h_xg":     round(h_xg, 2),
                    "a_xg":     round(a_xg, 2),
                    "p_00":     round(p_00, 3),
                })

            except Exception:
                import traceback; traceback.print_exc()
                continue

        if not candidates:
            return []

        # Sort by probability descending — most certain picks first
        candidates.sort(key=lambda x: -x["probability"])

        # Greedy accumulator: add picks until target_odds reached or max_picks
        accumulator = []
        combined = 1.0
        for pick in candidates:
            if combined >= target_odds:
                break
            if len(accumulator) >= max_picks:
                break
            accumulator.append(pick)
            combined *= pick["odds"]

        if verbose:
            self._print_accumulator(target_date, accumulator, combined, target_odds)

        return accumulator

    # ─────────────────────────────────────────────────────────────────────────
    def _print_accumulator(self, target_date, accumulator, combined_odds, target_odds):
        DIV = "=" * 70
        print(f"\n{DIV}")
        print(f"  DAILY ACCUMULATOR  |  {target_date}")
        print(f"{DIV}\n")
        if not accumulator:
            print("  No qualifying picks today.\n")
            return

        all_win = all(p.get("won") for p in accumulator)
        any_loss = any(p.get("won") is False for p in accumulator)
        result = "WIN" if all_win else "LOSS" if any_loss else "PENDING"

        print(f"  Combined odds: @{combined_odds:.2f}   [{result}]\n")
        for p in accumulator:
            icon = "[W]" if p.get("won") else "[L]" if p.get("won") is False else "[ ]"
            print(f"  {icon}  {p['fixture']}")
            print(f"       {p['label']}")
            print(f"       Prob: {p['probability']:.1%}  Odds: @{p['odds']:.2f}"
                  f"  xG: {p['h_xg']} vs {p['a_xg']}"
                  f"  P(0-0): {p['p_00']:.1%}"
                  + (f"  Score: {p['score']}" if p['score'] != '?' else ""))
            print()
        if combined_odds < target_odds:
            print(f"  [Note] Only {len(accumulator)} picks found — combined odds {combined_odds:.2f}"
                  f" below target {target_odds:.1f}\n")
