import hashlib

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.forms import ProfileForm, RegistrationForm
from accounts.models import StudentProfile


class RateLimitedLoginView(LoginView):
    failure_limit = 5
    failure_window_seconds = 300

    def _throttle_key(self):
        identity = f"{self.request.META.get('REMOTE_ADDR', '')}:{self.request.POST.get('username', '').strip().casefold()}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return f"login-failures:{digest}"

    def post(self, request, *args, **kwargs):
        self.request = request
        key = self._throttle_key()
        if cache.get(key, 0) >= self.failure_limit:
            form = self.get_form()
            form.add_error(None, "Too many sign-in attempts. Wait five minutes and try again.")
            return self.form_invalid(form)
        response = super().post(request, *args, **kwargs)
        if response.status_code == 302:
            cache.delete(key)
        else:
            attempts = cache.get(key, 0)
            if attempts == 0:
                cache.add(key, 1, timeout=self.failure_window_seconds)
            else:
                cache.set(key, attempts + 1, timeout=self.failure_window_seconds)
        return response


@require_POST
def logout_view(request):
    logout(request)
    return redirect("accounts:login")


def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard:home")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Your student account is ready.")
        return redirect("dashboard:home")
    return render(request, "accounts/register.html", {"form": form})


@login_required
def profile(request):
    if request.user.is_staff:
        messages.info(request, "Staff profiles are managed through Django administration.")
        return redirect("dashboard:home")
    profile_record, _ = StudentProfile.objects.get_or_create(user=request.user)
    form = ProfileForm(request.POST or None, instance=profile_record)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile updated.")
        return redirect("accounts:profile")
    return render(request, "accounts/profile.html", {"form": form})
