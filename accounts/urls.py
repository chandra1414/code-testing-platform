from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.RateLimitedLoginView.as_view(template_name="accounts/login.html"), name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("register/", views.register, name="register"),
    path("profile/", views.profile, name="profile"),
]
