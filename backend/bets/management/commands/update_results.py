"""
Management command: python manage.py update_results [--date YYYY-MM-DD]

Fetches actual match results from football-data.org for all dates that have
PENDING predictions, then marks each prediction WIN / LOSS / VOID and
rebuilds the DailyScorecard rows.
"""
import sys, io
from datetime import date, timedelta
from difflib import SequenceMatcher

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from bets.models import Prediction, MatchResult, DailyScorecard

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE_URL = "https://api.football-data.org/v4"
COMP_CODES = [
    # Big 5 + Ligue 1
    "PL", "PD", "BL1", "SA", "FL1",
    # Extended Tier 1
    "DED", "PPL", "ELC", "SPL", "BJL", "TSL", "BL2",
    # Extended Tier 2
    "BSA", "MLS", "SSL", "ASL", "ABL",
    # European cups
    "CL", "EL", "UECL",
]


def _headers():
    return {"X-Auth-Token": settings.FOOTBALL_DATA_API_KEY}


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _fetch_results_from_db(match_date: str) -> list[dict] | None:
    """Fetch match results from local HistoricalFixture table (fast, no API calls)."""
    try:
        from bets.models import HistoricalFixture
        rows = HistoricalFixture.objects.filter(
            match_date=match_date,
            home_score__isnull=False,
            away_score__isnull=False,
        ).values("home_team", "away_team", "home_score", "away_score", "league_code")
        if rows.exists():
            return [
                {
                    "home_team":   r["home_team"],
                    "away_team":   r["away_team"],
                    "home_score":  r["home_score"],
                    "away_score":  r["away_score"],
                    "league_code": r["league_code"],
                }
                for r in rows
            ]
    except Exception:
        pass
    return None


def _fetch_results(match_date: str) -> list[dict]:
    """Fetch all finished matches — DB first, live API fallback."""
    from datetime import date as _date_cls
    try:
        if _date_cls.fromisoformat(match_date) < _date_cls.today():
            db = _fetch_results_from_db(match_date)
            if db:
                return db
    except Exception:
        pass

    results = []
    for code in COMP_CODES:
        try:
            r = requests.get(
                f"{BASE_URL}/competitions/{code}/matches",
                headers=_headers(),
                params={"dateFrom": match_date, "dateTo": match_date, "status": "FINISHED"},
                timeout=12,
            )
            if r.status_code == 429:
                print(f"  [Rate limit] sleeping 60s before retrying {code}...")
                import time; time.sleep(62)
                r = requests.get(
                    f"{BASE_URL}/competitions/{code}/matches",
                    headers=_headers(),
                    params={"dateFrom": match_date, "dateTo": match_date, "status": "FINISHED"},
                    timeout=12,
                )
            if r.status_code != 200:
                continue
            for m in r.json().get("matches", []):
                score = m.get("score", {}).get("fullTime", {})
                home_s = score.get("home")
                away_s = score.get("away")
                if home_s is None or away_s is None:
                    continue
                results.append({
                    "home_team":   m["homeTeam"]["name"],
                    "away_team":   m["awayTeam"]["name"],
                    "home_score":  int(home_s),
                    "away_score":  int(away_s),
                    "league_code": code,
                })
        except Exception as e:
            print(f"  [Warning] Could not fetch {code}: {e}")
    return results


def _find_result(home: str, away: str, results: list[dict]) -> dict | None:
    best, best_score = None, 0.0
    for r in results:
        s = min(_sim(home, r["home_team"]), _sim(away, r["away_team"]))
        if s > best_score:
            best_score, best = s, r
    return best if best_score >= 0.55 else None


def _evaluate(pred: Prediction, home_score: int, away_score: int) -> str:
    """Return WIN / LOSS based on bet_type and actual scores."""
    bet = pred.bet_type.lower()
    total = home_score + away_score

    if "over 2.5" in bet:
        return "WIN" if total > 2 else "LOSS"
    if "over 1.5" in bet:
        return "WIN" if total > 1 else "LOSS"
    if "over 0.5" in bet:
        return "WIN" if total > 0 else "LOSS"
    if "over 3.5" in bet:
        return "WIN" if total > 3 else "LOSS"
    if "btts" in bet or "both teams to score" in bet:
        return "WIN" if home_score > 0 and away_score > 0 else "LOSS"

    # "Either Team to Win (12)" — market wins if either side wins (no draw)
    if "12" in bet or "either team to win" in bet:
        return "WIN" if home_score != away_score else "LOSS"

    # "Team X to Score 1+"
    if "to score 1+" in bet:
        team = bet.replace("to score 1+", "").strip()
        home_lower = pred.home_team.lower()
        away_lower = pred.away_team.lower()
        if _sim(team, home_lower) > _sim(team, away_lower):
            return "WIN" if home_score > 0 else "LOSS"
        else:
            return "WIN" if away_score > 0 else "LOSS"

    # "Team X Clean Sheet" — team doesn't concede
    if "clean sheet" in bet:
        team = bet.replace("clean sheet", "").strip()
        if _sim(team, pred.home_team.lower()) > _sim(team, pred.away_team.lower()):
            return "WIN" if away_score == 0 else "LOSS"
        else:
            return "WIN" if home_score == 0 else "LOSS"

    # "Cards Over X.5"
    if "cards over" in bet:
        import re
        m = re.search(r"cards over (\d+\.?\d*)", bet)
        if m:
            threshold = float(m.group(1))
            total_cards = (pred.raw_data or {}).get("total_cards", None)
            if total_cards is not None:
                return "WIN" if total_cards > threshold else "LOSS"
        return "VOID"  # can't verify without card data

    # Double-chance markets:
    #   "Team or Draw (1X)"  → home team doesn't lose
    #   "Draw or Team (X2)"  → away team doesn't lose
    if "or draw" in bet or "draw or" in bet or "1x" in bet or "x2" in bet:
        if "x2" in bet or "draw or" in bet:
            return "WIN" if away_score >= home_score else "LOSS"
        else:
            return "WIN" if home_score >= away_score else "LOSS"

    # Win market "Team X to Win"
    if "to win" in bet:
        team = bet.replace("to win", "").strip()
        if _sim(team, pred.home_team.lower()) > _sim(team, pred.away_team.lower()):
            return "WIN" if home_score > away_score else "LOSS"
        else:
            return "WIN" if away_score > home_score else "LOSS"

    return "LOSS"  # unknown — conservative


def _rebuild_scorecard(match_date: date):
    preds = Prediction.objects.filter(match_date=match_date).exclude(outcome="PENDING")
    total  = preds.count()
    wins   = preds.filter(outcome="WIN").count()
    losses = preds.filter(outcome="LOSS").count()
    voids  = preds.filter(outcome="VOID").count()
    valid  = wins + losses
    acc    = round(wins / valid * 100, 1) if valid > 0 else 0.0

    DailyScorecard.objects.update_or_create(
        date=match_date,
        defaults={
            "total_picks": total,
            "correct":     wins,
            "losses":      losses,
            "voids":       voids,
            "accuracy_pct": acc,
        },
    )


class Command(BaseCommand):
    help = "Fetch actual results and update prediction outcomes + daily scorecards"

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="Specific date YYYY-MM-DD (default: all dates with PENDING predictions)",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Re-evaluate all predictions regardless of current outcome",
        )

    def handle(self, *args, **options):
        target = options.get("date")
        reeval = options.get("all", False)

        if target:
            dates = [date.fromisoformat(target)]
        else:
            qs = Prediction.objects.all() if reeval else Prediction.objects.filter(outcome="PENDING")
            dates = sorted(set(qs.values_list("match_date", flat=True)))

        if not dates:
            print("  No pending predictions found.")
            return

        print(f"\n  Updating results for {len(dates)} date(s)...\n")

        for d in dates:
            d_str = d.isoformat()
            print(f"  === {d_str} ===")

            api_results = _fetch_results(d_str)

            # Save raw results to MatchResult table
            for r in api_results:
                MatchResult.objects.update_or_create(
                    match_date=d,
                    home_team=r["home_team"],
                    away_team=r["away_team"],
                    defaults={
                        "home_score":  r["home_score"],
                        "away_score":  r["away_score"],
                        "league_code": r["league_code"],
                    },
                )

            preds = Prediction.objects.filter(match_date=d)
            if not reeval:
                preds = preds.filter(outcome="PENDING")

            for pred in preds:
                result = _find_result(pred.home_team, pred.away_team, api_results)
                if not result:
                    print(f"    [?] No result found: {pred.home_team} vs {pred.away_team} — marking VOID")
                    pred.outcome = "VOID"
                    pred.save(update_fields=["outcome", "updated_at"])
                    continue

                outcome = _evaluate(pred, result["home_score"], result["away_score"])
                pred.outcome          = outcome
                pred.actual_home_score = result["home_score"]
                pred.actual_away_score = result["away_score"]
                pred.save(update_fields=["outcome", "actual_home_score", "actual_away_score", "updated_at"])

                icon = "✅" if outcome == "WIN" else "❌"
                print(f"    {icon} {pred.home_team} vs {pred.away_team} | {pred.bet_type} | "
                      f"Result: {result['home_score']}-{result['away_score']} → {outcome}")

            _rebuild_scorecard(d)
            sc = DailyScorecard.objects.get(date=d)
            print(f"    Scorecard: {sc.correct}/{sc.total_picks} ({sc.accuracy_pct}%)\n")

        print("  Done.\n")
