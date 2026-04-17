from django.urls import path
from .views import (
    DecodeBookingCodeView, LiveStatsView, GlobalLiveMatchesView,
    TodaysMatchesView, MatchDetailView, MatchPreviewView, MatchAssessmentView,
    TodaysBestBetsView, FixtureBetSignalsView, UpcomingPicksView,
)

urlpatterns = [
    path("decode/", DecodeBookingCodeView.as_view(), name="decode"),
    path("live-stats/", LiveStatsView.as_view(), name="live-stats"),
    path("live-now/", GlobalLiveMatchesView.as_view(), name="live-now"),
    path("matches-today/", TodaysMatchesView.as_view(), name="matches-today"),
    path("match-detail/<int:fixture_id>/", MatchDetailView.as_view(), name="match-detail"),
    path("match-preview/<int:fixture_id>/", MatchPreviewView.as_view(), name="match-preview"),
    path("match-assessment/<int:fixture_id>/", MatchAssessmentView.as_view(), name="match-assessment"),
    path("predictions/today/", TodaysBestBetsView.as_view(), name="predictions-today"),
    path("predictions/upcoming/", UpcomingPicksView.as_view(), name="predictions-upcoming"),
    path("predictions/fixture/<int:fixture_id>/", FixtureBetSignalsView.as_view(), name="predictions-fixture"),
]
