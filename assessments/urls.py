from django.urls import path

from assessments import views

app_name = "assessments"

urlpatterns = [
    path("", views.test_list, name="list"),
    path("<int:test_id>/preview/", views.preview_test, name="preview"),
    path("<int:test_id>/start/", views.start_test, name="start"),
    path("attempt/<int:attempt_id>/", views.take_test, name="take"),
    path("attempt/<int:attempt_id>/save/", views.save_answer, name="save"),
    path("attempt/<int:attempt_id>/run/", views.run_code, name="run"),
    path("attempt/<int:attempt_id>/submit/", views.submit_test, name="submit"),
]
