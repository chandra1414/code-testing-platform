from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse


class AccountTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_logout_requires_post(self):
        user = User.objects.create_user("student", password="StrongPassword!123")
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        self.assertEqual(self.client.post(reverse("accounts:logout")).status_code, 302)

    def test_profile_update_changes_user_details(self):
        user = User.objects.create_user(
            "student",
            password="StrongPassword!123",
            first_name="First",
            last_name="Name",
            email="old@example.com",
        )
        self.client.force_login(user)
        self.client.post(
            reverse("accounts:profile"),
            {"first_name": "New", "last_name": "Name", "email": "new@example.com", "display_name": "New Name"},
        )
        user.refresh_from_db()
        self.assertEqual(user.first_name, "New")
        self.assertEqual(user.email, "new@example.com")

    def test_login_failures_are_rate_limited(self):
        User.objects.create_user("limited", password="StrongPassword!123")
        for _ in range(5):
            self.client.post(reverse("accounts:login"), {"username": "limited", "password": "wrong"})
        response = self.client.post(reverse("accounts:login"), {"username": "limited", "password": "StrongPassword!123"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertContains(response, "Wait five minutes")
