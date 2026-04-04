"""
Management command: python manage.py seed_results

Inserts known historical match results directly into the DB (no API call needed).
Then re-evaluates all VOID/PENDING predictions and rebuilds daily scorecards.
"""
import sys, io
from datetime import date
from difflib import SequenceMatcher

from django.core.management.base import BaseCommand
from bets.models import Prediction, MatchResult, DailyScorecard

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Known results: (date, home, away, home_score, away_score, league_code) ────
KNOWN_RESULTS = [
    # 2026-01-03
    ("2026-01-03", "Elche CF",                           "Villarreal CF",                      1, 3, "PD"),
    ("2026-01-03", "RCD Espanyol de Barcelona",          "FC Barcelona",                       0, 2, "PD"),
    ("2026-01-03", "Juventus FC",                        "US Lecce",                           1, 1, "SA"),
    ("2026-01-03", "Sport Lisboa e Benfica",             "GD Estoril Praia",                   3, 1, "PPL"),
    # 2026-01-10
    ("2026-01-10", "PSV",                                "SBV Excelsior",                      5, 1, "DED"),
    ("2026-01-10", "AZ",                                 "FC Volendam",                        1, 0, "DED"),
    ("2026-01-10", "AS Roma",                            "US Sassuolo Calcio",                 2, 0, "SA"),
    ("2026-01-10", "Atalanta BC",                        "Torino FC",                          2, 0, "SA"),
    # 2026-01-17
    ("2026-01-17", "Liverpool FC",                       "Burnley FC",                         1, 1, "PL"),
    ("2026-01-17", "RB Leipzig",                         "FC Bayern München",                  1, 5, "BL1"),
    ("2026-01-17", "Fortuna Sittard",                    "PSV",                                1, 2, "DED"),
    ("2026-01-17", "1. FC Köln",                         "1. FSV Mainz 05",                    2, 1, "BL1"),
    ("2026-01-17", "NAC Breda",                          "NEC",                                3, 4, "DED"),
    ("2026-01-17", "Real Madrid CF",                     "Levante UD",                         2, 0, "PD"),
    ("2026-01-17", "Racing Club de Lens",                "AJ Auxerre",                         1, 0, "FL1"),
    ("2026-01-17", "Borussia Dortmund",                  "FC St. Pauli 1910",                  3, 2, "BL1"),
    # 2026-01-24
    ("2026-01-24", "NEC",                                "PEC Zwolle",                         2, 1, "DED"),
    ("2026-01-24", "PSV",                                "NAC Breda",                          2, 2, "DED"),
    ("2026-01-24", "FC Bayern München",                  "FC Augsburg",                        1, 2, "BL1"),
    ("2026-01-24", "FC Arouca",                          "Sporting Clube de Portugal",          1, 2, "PPL"),
    ("2026-01-24", "GD Estoril Praia",                   "Vitória SC",                         4, 2, "PPL"),
    ("2026-01-24", "Manchester City FC",                 "Wolverhampton Wanderers FC",          2, 0, "PL"),
    ("2026-01-24", "Olympique de Marseille",             "Racing Club de Lens",                 3, 1, "FL1"),
    ("2026-01-24", "1. FC Heidenheim 1846",              "RB Leipzig",                         0, 3, "BL1"),
    ("2026-01-24", "AFC Ajax",                           "FC Volendam",                        2, 0, "DED"),
    # 2026-01-31
    ("2026-01-31", "AZ",                                 "NEC",                                1, 3, "DED"),
    ("2026-01-31", "Elche CF",                           "FC Barcelona",                       1, 3, "PD"),
    ("2026-01-31", "Hamburger SV",                       "FC Bayern München",                  2, 2, "BL1"),
    ("2026-01-31", "Leeds United FC",                    "Arsenal FC",                         0, 4, "PL"),
    ("2026-01-31", "Liverpool FC",                       "Newcastle United FC",                 4, 1, "PL"),
    ("2026-01-31", "RB Leipzig",                         "1. FSV Mainz 05",                    1, 2, "BL1"),
    # 2026-02-07
    ("2026-02-07", "NEC",                                "Heracles Almelo",                    4, 1, "DED"),
    ("2026-02-07", "FC Barcelona",                       "RCD Mallorca",                       3, 0, "PD"),
    ("2026-02-07", "FC Nantes",                          "Olympique Lyonnais",                  0, 1, "FL1"),
    ("2026-02-07", "Arsenal FC",                         "Sunderland AFC",                     3, 0, "PL"),
    ("2026-02-07", "Wolverhampton Wanderers FC",         "Chelsea FC",                         1, 3, "PL"),
    # 2026-02-14
    ("2026-02-14", "SV Werder Bremen",                   "FC Bayern München",                  0, 3, "BL1"),
    ("2026-02-14", "Real Madrid CF",                     "Real Sociedad de Fútbol",            4, 1, "PD"),
    ("2026-02-14", "AFC Ajax",                           "Fortuna Sittard",                    4, 1, "DED"),
    ("2026-02-14", "Paris FC",                           "Racing Club de Lens",                0, 5, "FL1"),
    # 2026-02-21
    ("2026-02-21", "PSV",                                "SC Heerenveen",                      3, 1, "DED"),
    ("2026-02-21", "AFC Ajax",                           "NEC",                                1, 1, "DED"),
    ("2026-02-21", "FC Bayern München",                  "Eintracht Frankfurt",                 3, 2, "BL1"),
    ("2026-02-21", "1. FC Köln",                         "TSG 1899 Hoffenheim",                2, 2, "BL1"),
    ("2026-02-21", "Sport Lisboa e Benfica",             "AVS",                                3, 0, "PPL"),
    ("2026-02-21", "Moreirense FC",                      "Sporting Clube de Portugal",          0, 3, "PPL"),
    ("2026-02-21", "Sporting Clube de Braga",            "Vitória SC",                         3, 2, "PPL"),
    ("2026-02-21", "Club Atlético de Madrid",            "RCD Espanyol de Barcelona",           4, 2, "PD"),
    ("2026-02-21", "Paris Saint-Germain FC",             "FC Metz",                            3, 0, "FL1"),
    # 2026-02-28
    ("2026-02-28", "Heracles Almelo",                    "PSV",                                1, 3, "DED"),
    ("2026-02-28", "Borussia Dortmund",                  "FC Bayern München",                  2, 3, "BL1"),
    ("2026-02-28", "Liverpool FC",                       "West Ham United FC",                  5, 2, "PL"),
    ("2026-02-28", "NEC",                                "Fortuna Sittard",                    2, 3, "DED"),
    ("2026-02-28", "FC Barcelona",                       "Villarreal CF",                      4, 1, "PD"),
    ("2026-02-28", "FC Internazionale Milano",           "Genoa CFC",                          2, 0, "SA"),
    ("2026-02-28", "TSG 1899 Hoffenheim",                "FC St. Pauli 1910",                  0, 1, "BL1"),
    # 2026-03-07
    ("2026-03-07", "Juventus FC",                        "AC Pisa 1909",                       4, 0, "SA"),
    ("2026-03-07", "PSV",                                "AZ",                                 2, 1, "DED"),
    ("2026-03-07", "Sporting Clube de Braga",            "Sporting Clube de Portugal",          2, 2, "PPL"),
    ("2026-03-07", "Club Atlético de Madrid",            "Real Sociedad de Fútbol",             3, 2, "PD"),
    ("2026-03-07", "RB Leipzig",                         "FC Augsburg",                        2, 1, "BL1"),
    ("2026-03-07", "1. FC Köln",                         "Borussia Dortmund",                  1, 2, "BL1"),
    # 2026-03-14
    ("2026-03-14", "PSV",                                "NEC",                                2, 3, "DED"),
    ("2026-03-14", "Real Madrid CF",                     "Elche CF",                           4, 1, "PD"),
    ("2026-03-14", "TSG 1899 Hoffenheim",                "VfL Wolfsburg",                      1, 1, "BL1"),
    ("2026-03-14", "SC Heerenveen",                      "Telstar 1963",                       3, 0, "DED"),
    ("2026-03-14", "Club Atlético de Madrid",            "Getafe CF",                          1, 0, "PD"),
    ("2026-03-14", "Borussia Dortmund",                  "FC Augsburg",                        2, 0, "BL1"),
    # 2026-03-21
    ("2026-03-21", "Juventus FC",                        "US Sassuolo Calcio",                 1, 1, "SA"),
    ("2026-03-21", "Sport Lisboa e Benfica",             "Vitória SC",                         3, 0, "PPL"),
    ("2026-03-21", "FC Bayern München",                  "1. FC Union Berlin",                  4, 0, "BL1"),
]


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _find_result(home: str, away: str, results: list) -> dict | None:
    best, best_score = None, 0.0
    for r in results:
        s = min(_sim(home, r["home_team"]), _sim(away, r["away_team"]))
        if s > best_score:
            best_score, best = s, r
    return best if best_score >= 0.55 else None


def _evaluate(pred: Prediction, home_score: int, away_score: int) -> str:
    bet = pred.bet_type.lower()
    total = home_score + away_score
    if "over 2.5" in bet:
        return "WIN" if total > 2 else "LOSS"
    if "over 1.5" in bet:
        return "WIN" if total > 1 else "LOSS"
    if "over 0.5" in bet:
        return "WIN" if total > 0 else "LOSS"
    if "btts" in bet or "both teams to score" in bet:
        return "WIN" if home_score > 0 and away_score > 0 else "LOSS"
    if "to score 1+" in bet:
        team = bet.replace("to score 1+", "").strip()
        if _sim(team, pred.home_team.lower()) > _sim(team, pred.away_team.lower()):
            return "WIN" if home_score > 0 else "LOSS"
        else:
            return "WIN" if away_score > 0 else "LOSS"
    if "or draw" in bet or "1x" in bet:
        team = bet.split("or")[0].strip()
        if _sim(team, pred.home_team.lower()) > 0.5:
            return "WIN" if home_score >= away_score else "LOSS"
        else:
            return "WIN" if away_score >= home_score else "LOSS"
    if "to win" in bet:
        team = bet.replace("to win", "").strip()
        if _sim(team, pred.home_team.lower()) > _sim(team, pred.away_team.lower()):
            return "WIN" if home_score > away_score else "LOSS"
        else:
            return "WIN" if away_score > home_score else "LOSS"
    return "LOSS"


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
        defaults={"total_picks": total, "correct": wins, "losses": losses,
                  "voids": voids, "accuracy_pct": acc},
    )


class Command(BaseCommand):
    help = "Seed known historical results into DB and re-evaluate all predictions"

    def handle(self, *args, **options):
        print("\n  Seeding known results into MatchResult table...\n")

        # Group results by date for lookup
        results_by_date: dict[str, list] = {}
        for row in KNOWN_RESULTS:
            d_str, home, away, hs, as_, code = row
            results_by_date.setdefault(d_str, []).append({
                "home_team": home, "away_team": away,
                "home_score": hs, "away_score": as_, "league_code": code,
            })

        # Insert into MatchResult
        inserted = updated = 0
        for d_str, results in results_by_date.items():
            d = date.fromisoformat(d_str)
            for r in results:
                obj, created = MatchResult.objects.update_or_create(
                    match_date=d,
                    home_team=r["home_team"],
                    away_team=r["away_team"],
                    defaults={"home_score": r["home_score"],
                              "away_score": r["away_score"],
                              "league_code": r["league_code"]},
                )
                if created:
                    inserted += 1
                else:
                    updated += 1

        print(f"  MatchResult: {inserted} inserted, {updated} updated\n")
        print("  Evaluating predictions...\n")

        # Re-evaluate all non-WIN/LOSS predictions (PENDING or VOID from failed API)
        dates = sorted(results_by_date.keys())
        total_wins = total_losses = total_void = 0

        for d_str in dates:
            d = date.fromisoformat(d_str)
            results = results_by_date[d_str]
            preds = Prediction.objects.filter(match_date=d)
            print(f"  === {d_str} ===")

            for pred in preds:
                result = _find_result(pred.home_team, pred.away_team, results)
                if not result:
                    # Mark as VOID (postponed / not in our known list)
                    pred.outcome = "VOID"
                    pred.save(update_fields=["outcome", "updated_at"])
                    total_void += 1
                    print(f"    🔇 {pred.home_team} vs {pred.away_team} | {pred.bet_type} → VOID")
                    continue

                outcome = _evaluate(pred, result["home_score"], result["away_score"])
                pred.outcome           = outcome
                pred.actual_home_score = result["home_score"]
                pred.actual_away_score = result["away_score"]
                pred.save(update_fields=["outcome", "actual_home_score",
                                         "actual_away_score", "updated_at"])

                icon = "✅" if outcome == "WIN" else "❌"
                if outcome == "WIN":
                    total_wins += 1
                else:
                    total_losses += 1
                print(f"    {icon} {pred.home_team} vs {pred.away_team} | "
                      f"{pred.bet_type} | {result['home_score']}-{result['away_score']} → {outcome}")

            _rebuild_scorecard(d)
            sc = DailyScorecard.objects.get(date=d)
            print(f"    Scorecard: {sc.correct}/{sc.correct + sc.losses} ({sc.accuracy_pct}%)\n")

        valid = total_wins + total_losses
        overall = round(total_wins / valid * 100, 1) if valid > 0 else 0
        print(f"  OVERALL: {total_wins}/{valid} = {overall}%  ({total_void} void)\n")
