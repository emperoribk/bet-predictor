"""
Management command: python manage.py scorecard [--month YYYY-MM] [--date YYYY-MM-DD]

Prints the scorecard from the database — per day, per month, and overall.
"""
import sys, io
from django.core.management.base import BaseCommand
from bets.models import Prediction, DailyScorecard

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DIV  = "=" * 70
THIN = "-" * 70


class Command(BaseCommand):
    help = "Print prediction scorecard from the database"

    def add_arguments(self, parser):
        parser.add_argument("--month", type=str, default=None, help="Filter by month e.g. 2026-01")
        parser.add_argument("--date",  type=str, default=None, help="Filter by date  e.g. 2026-01-03")
        parser.add_argument("--losses", action="store_true", help="Show only incorrect predictions")

    def handle(self, *args, **options):
        month  = options.get("month")
        day    = options.get("date")
        losses_only = options.get("losses", False)

        preds = Prediction.objects.exclude(outcome="PENDING")

        if day:
            preds = preds.filter(match_date=day)
        elif month:
            preds = preds.filter(match_date__startswith=month)

        if losses_only:
            preds = preds.filter(outcome="LOSS")

        preds = preds.order_by("match_date", "-grade_score")

        if not preds.exists():
            print("\n  No resolved predictions found for the given filter.\n")
            return

        print(f"\n{DIV}")
        print(f"  SPORTY SCORECARD  |  {'Losses only' if losses_only else 'All resolved picks'}")
        if day:
            print(f"  Filter: {day}")
        elif month:
            print(f"  Filter: {month}")
        print(f"{DIV}\n")

        # ── Per-day detail ────────────────────────────────────────────────────
        current_date = None
        day_wins = day_total = 0

        for p in preds:
            if p.match_date != current_date:
                if current_date is not None:
                    acc = round(day_wins / day_total * 100, 1) if day_total > 0 else 0
                    print(f"  Day total: {day_wins}/{day_total} ({acc}%)\n")
                current_date = p.match_date
                day_wins = day_total = 0
                print(f"  {THIN}")
                print(f"  {p.match_date}")
                print(f"  {THIN}")

            icon = "✅" if p.outcome == "WIN" else ("🔇" if p.outcome == "VOID" else "❌")
            score_str = ""
            if p.actual_home_score is not None:
                score_str = f"  [{p.actual_home_score}-{p.actual_away_score}]"
            print(f"  {icon} {p.home_team} vs {p.away_team}")
            print(f"       Bet    : {p.bet_type}{score_str}")
            print(f"       Grade  : {p.grade} {p.grade_score}/100  |  League: {p.league}")
            print()

            if p.outcome != "VOID":
                day_total += 1
                if p.outcome == "WIN":
                    day_wins += 1

        if current_date is not None:
            acc = round(day_wins / day_total * 100, 1) if day_total > 0 else 0
            print(f"  Day total: {day_wins}/{day_total} ({acc}%)\n")

        # ── Monthly summary ───────────────────────────────────────────────────
        if not losses_only:
            print(f"\n{DIV}")
            print(f"  MONTHLY BREAKDOWN")
            print(f"{DIV}\n")

            months = {}
            for p in Prediction.objects.exclude(outcome__in=["PENDING", "VOID"]).order_by("match_date"):
                m = p.match_date.strftime("%Y-%m")
                if m not in months:
                    months[m] = {"wins": 0, "total": 0}
                months[m]["total"] += 1
                if p.outcome == "WIN":
                    months[m]["wins"] += 1

            all_wins = all_total = 0
            for m, data in months.items():
                acc = round(data["wins"] / data["total"] * 100, 1) if data["total"] > 0 else 0
                bar_f = int(acc / 100 * 20)
                bar = "[" + "#" * bar_f + "." * (20 - bar_f) + "]"
                print(f"  {m}   {data['wins']:>2}/{data['total']:<2}  ({acc:>5.1f}%)  {bar}")
                all_wins  += data["wins"]
                all_total += data["total"]

            # ── Overall ───────────────────────────────────────────────────────
            overall_acc = round(all_wins / all_total * 100, 1) if all_total > 0 else 0
            print(f"\n{DIV}")
            print(f"  OVERALL: {all_wins}/{all_total} = {overall_acc}%")
            print(f"{DIV}\n")

            # ── By session (day of week) ───────────────────────────────────────
            DOW_ORDER = ['SAT', 'SUN', 'FRI', 'WED', 'MON', 'TUE', 'THU']
            DOW_LABEL = {
                'SAT': 'Saturday', 'SUN': 'Sunday', 'FRI': 'Friday',
                'WED': 'Wednesday', 'MON': 'Monday', 'TUE': 'Tuesday', 'THU': 'Thursday',
            }
            dow_stats = {}
            for p in Prediction.objects.exclude(outcome__in=["PENDING", "VOID"]).order_by("match_date"):
                d = p.day_of_week
                if d not in dow_stats:
                    dow_stats[d] = {"wins": 0, "total": 0}
                dow_stats[d]["total"] += 1
                if p.outcome == "WIN":
                    dow_stats[d]["wins"] += 1

            if dow_stats:
                print(f"\n{DIV}")
                print(f"  BY SESSION")
                print(f"{DIV}\n")
                for d in DOW_ORDER:
                    if d not in dow_stats:
                        continue
                    data = dow_stats[d]
                    acc = round(data["wins"] / data["total"] * 100, 1) if data["total"] > 0 else 0
                    bar_f = int(acc / 100 * 20)
                    bar = "[" + "#" * bar_f + "." * (20 - bar_f) + "]"
                    print(f"  {DOW_LABEL[d]:<12}  {data['wins']:>2}/{data['total']:<2}  ({acc:>5.1f}%)  {bar}")
                print()
