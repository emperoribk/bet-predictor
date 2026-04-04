from datetime import datetime, timezone

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import BetSlip, Match
from .services.sportybet import decode_booking_code
from .services.football_data import find_fixture_id, get_live_match, get_all_live_matches, get_todays_matches, get_match_detail, get_match_preview
from .services.analysis import analyse_bet_slip
from .services.player_assessment import assess_lineup, predict_match as predict_match_signals
from .services.football_data import get_fixture_lineups, get_team_season_stats, get_fixtures_by_date
from .services.daily_predictions import get_todays_best_bets, get_all_bet_signals


def ms_to_datetime(ms):
    """Convert a millisecond timestamp to a UTC-aware datetime, or return None."""
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


class DecodeBookingCodeView(APIView):
    """
    POST /api/decode/
    Body: { "booking_code": "ABC123" }

    Decodes a SportyBet booking code, maps matches to football-data.org fixture IDs,
    stores them in the DB, and returns the initial bet slip.
    """

    def post(self, request):
        booking_code = request.data.get("booking_code", "").strip().upper()
        if not booking_code:
            return Response({"error": "booking_code is required."}, status=status.HTTP_400_BAD_REQUEST)

        # Always delete and re-decode so pick labels are always fresh
        BetSlip.objects.filter(booking_code=booking_code).delete()

        # Decode from SportyBet
        try:
            raw_matches = decode_booking_code(booking_code)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # Save to DB
        try:
            bet_slip = BetSlip.objects.create(booking_code=booking_code)
            for m in raw_matches:
                kickoff_dt = ms_to_datetime(m.get("kickoff_time"))
                fixture_id = find_fixture_id(
                    m["home_team"], m["away_team"], kickoff_dt
                )
                Match.objects.create(
                    bet_slip=bet_slip,
                    home_team=m["home_team"],
                    away_team=m["away_team"],
                    kickoff_time=kickoff_dt,
                    user_pick=m["user_pick"],
                    market=m.get("market", ""),
                    odds=m.get("odds"),
                    fixture_id=fixture_id,
                )
        except Exception as e:
            # Clean up partial data and return a clear error
            BetSlip.objects.filter(booking_code=booking_code).delete()
            return Response({"error": f"Failed to save matches: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        matches = bet_slip.matches.all()
        result = analyse_bet_slip(matches)
        return Response({"booking_code": booking_code, **result}, status=status.HTTP_201_CREATED)


class LiveStatsView(APIView):
    """
    GET /api/live-stats/?booking_code=ABC123

    Refreshes live data from football-data.org for all matches in the slip,
    runs the analysis engine, and returns updated results.
    Called by the frontend every 90 seconds.
    """

    def get(self, request):
        booking_code = request.query_params.get("booking_code", "").strip().upper()
        if not booking_code:
            return Response({"error": "booking_code is required."}, status=status.HTTP_400_BAD_REQUEST)

        bet_slip = BetSlip.objects.filter(booking_code=booking_code).first()
        if not bet_slip:
            return Response({"error": "Booking code not found. Decode it first."}, status=status.HTTP_404_NOT_FOUND)

        matches = list(bet_slip.matches.all())

        for match in matches:
            if not match.fixture_id:
                # Try to find fixture ID again in case it wasn't found on first decode
                fixture_id = find_fixture_id(match.home_team, match.away_team, match.kickoff_time)
                if fixture_id:
                    match.fixture_id = fixture_id
                    match.save(update_fields=["fixture_id"])
                continue

            # Skip finished matches — no need to poll again
            if match.analysis_status in ("WON", "LOST"):
                continue

            live = get_live_match(match.fixture_id)
            if not live:
                continue

            # Update match with fresh live data
            match.match_status = live["match_status"] or match.match_status
            match.match_minute = live["match_minute"]
            match.score_home = live["score_home"]
            match.score_away = live["score_away"]
            match.possession_home = live["possession_home"]
            match.possession_away = live["possession_away"]
            match.shots_on_target_home = live["shots_on_target_home"]
            match.shots_on_target_away = live["shots_on_target_away"]
            match.shots_total_home = live["shots_total_home"]
            match.shots_total_away = live["shots_total_away"]
            match.corners_home = live["corners_home"]
            match.corners_away = live["corners_away"]
            match.red_cards_home = live["red_cards_home"]
            match.red_cards_away = live["red_cards_away"]
            match.events = live["events"]
            match.save()

        # Re-run analysis on all updated matches
        result = analyse_bet_slip(matches)

        # Persist analysis status back to DB
        for match_result in result["matches"]:
            Match.objects.filter(id=match_result["id"]).update(
                analysis_status=match_result["analysis_status"],
                analysis_reason=match_result["analysis_reason"],
            )

        return Response({"booking_code": booking_code, **result})


class GlobalLiveMatchesView(APIView):
    """
    GET /api/live-now/
    Returns all currently live matches from football-data.org.
    """
    def get(self, request):
        matches = get_all_live_matches()
        return Response({"matches": matches, "count": len(matches)})


class TodaysMatchesView(APIView):
    """
    GET /api/matches-today/?date=YYYY-MM-DD
    Returns all matches on a given date (defaults to today) grouped by competition.
    The optional ?date= param powers the Fixtures calendar date picker.
    """
    def get(self, request):
        date = request.query_params.get("date", "").strip()
        if date:
            matches = get_fixtures_by_date(date)
        else:
            matches = get_todays_matches()
        return Response({"matches": matches, "count": len(matches)})


class MatchDetailView(APIView):
    """
    GET /api/match-detail/<int:fixture_id>/
    Returns full stats, insights, timeline and lineup for one match.
    """
    def get(self, request, fixture_id):
        data = get_match_detail(fixture_id)
        if not data:
            return Response({"error": "Match not found or data unavailable."}, status=status.HTTP_404_NOT_FOUND)
        return Response(data)


class MatchPreviewView(APIView):
    """
    GET /api/match-preview/<int:fixture_id>/
    Returns H2H history, recent team form, and lineups for upcoming fixtures.
    """
    def get(self, request, fixture_id):
        data = get_match_preview(fixture_id)
        if not data:
            return Response({"error": "Preview data unavailable."}, status=status.HTTP_404_NOT_FOUND)
        return Response(data)


class MatchAssessmentView(APIView):
    """
    GET /api/match-assessment/<int:fixture_id>/

    Runs the player assessment engine on both lineups for a fixture.
    Returns position-specific player scores, team efficiency composites,
    and bet signals derived from form analysis.

    Query params:
      n_games=5   — how many recent matches to average per player (default 5)
    """
    def get(self, request, fixture_id):
        n_games = int(request.query_params.get("n_games", 5))

        fixture = get_fixture_lineups(fixture_id)
        if not fixture:
            return Response({"error": "Fixture not found."}, status=status.HTTP_404_NOT_FOUND)

        home_lineup = fixture.get("home_lineup", [])
        away_lineup = fixture.get("away_lineup", [])
        home_bench  = fixture.get("home_bench", [])
        away_bench  = fixture.get("away_bench", [])
        home_team   = fixture.get("home_team", "")
        away_team   = fixture.get("away_team", "")
        comp_code   = fixture.get("competition_code", "")
        match_date  = fixture.get("match_date", "")
        xg_data     = fixture.get("xg_data")   # populated for finished matches

        if not home_lineup and not away_lineup:
            return Response(
                {"error": "Lineups not yet available for this fixture."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Unpack match-page xG for GK scoring if available
        home_opp_xg  = xg_data["away"]["xg"]           if xg_data else None
        home_opp_sot = xg_data["away"]["shots_on_target"] if xg_data else None
        home_conceded = xg_data["home"]["goals"]         if xg_data else None
        away_opp_xg  = xg_data["home"]["xg"]            if xg_data else None
        away_opp_sot = xg_data["home"]["shots_on_target"] if xg_data else None
        away_conceded = xg_data["away"]["goals"]          if xg_data else None

        home_assessment = assess_lineup(
            lineup=home_lineup, bench=home_bench,
            team_name=home_team, competition_code=comp_code,
            match_date=match_date, n_games=n_games,
            opponent_xg=home_opp_xg, opponent_sot=home_opp_sot,
            goals_conceded=home_conceded,
        )
        away_assessment = assess_lineup(
            lineup=away_lineup, bench=away_bench,
            team_name=away_team, competition_code=comp_code,
            match_date=match_date, n_games=n_games,
            opponent_xg=away_opp_xg, opponent_sot=away_opp_sot,
            goals_conceded=away_conceded,
        )

        prediction = predict_match_signals(
            home_assessment,
            away_assessment,
            home_formation=fixture.get("home_formation"),
            away_formation=fixture.get("away_formation"),
            home_context=fixture.get("home_context"),
            away_context=fixture.get("away_context"),
        )

        return Response({
            "fixture_id":       fixture_id,
            "home_team":        home_team,
            "away_team":        away_team,
            "match_date":       match_date,
            "home_assessment":  home_assessment,
            "away_assessment":  away_assessment,
            "prediction":       prediction,
        })


class TodaysBestBetsView(APIView):
    """
    GET /api/predictions/today/

    Scans every SCHEDULED fixture today, runs the statistical confidence engine
    across all bet types (BTTS, Over 2.5, Over 3.5, Under 1.5, Clean Sheets,
    Team to Score 2+, Yellow Cards), and returns all signals with confidence ≥ 53%
    sorted from highest to lowest confidence.

    Powered by:
      - Understat season xG / PPDA / BTTS / over-goal rates per team
      - football-data.org home/away standings splits
      - Match-level yellow card history from recent fixtures
      - Motivation tier (relegation fight, title race, mid-table apathy)
    """
    def get(self, request):
        result = get_todays_best_bets()
        return Response(result)


class FixtureBetSignalsView(APIView):
    """
    GET /api/predictions/fixture/<int:fixture_id>/

    Runs the bet confidence engine for a single fixture.
    Useful for previewing predictions before a specific match.
    """
    def get(self, request, fixture_id):
        from .services.football_data import get_fixture_lineups

        fixture = get_fixture_lineups(fixture_id)
        if not fixture:
            return Response({"error": "Fixture not found."}, status=status.HTTP_404_NOT_FOUND)

        today = fixture.get("match_date", "")
        comp_code = fixture.get("competition_code", "")
        home_id = fixture.get("home_id")
        away_id = fixture.get("away_id")
        home_name = fixture.get("home_team", "")
        away_name = fixture.get("away_team", "")

        if not home_id or not away_id:
            return Response({"error": "Team IDs unavailable."}, status=status.HTTP_404_NOT_FOUND)

        home_stats = get_team_season_stats(home_id, home_name, comp_code, today)
        away_stats = get_team_season_stats(away_id, away_name, comp_code, today)

        home_ctx = fixture.get("home_context")
        away_ctx = fixture.get("away_context")

        signals = get_all_bet_signals(home_stats, away_stats, home_ctx, away_ctx)

        return Response({
            "fixture_id": fixture_id,
            "home_team":  home_name,
            "away_team":  away_name,
            "kickoff":    fixture.get("match_date"),
            "signals":    signals,
            "home_stats": home_stats,
            "away_stats": away_stats,
        })
