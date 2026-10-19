from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("tests/", include("assessments.urls")),
    path("results/", include("results.urls")),
    path("", include("dashboard.urls")),
]
