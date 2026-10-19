from django.urls import path

from results import views

app_name = "results"

urlpatterns = [
    path("", views.history, name="history"),
    path("attempt/<int:attempt_id>/", views.detail, name="detail"),
    path("leaderboard/", views.leaderboard, name="leaderboard"),
]
