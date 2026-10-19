from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from assessments.models import CodingTestCase, Option, Question, Submission, Test as Assessment, TestAttempt
from assessments.services import create_attempt, finish_attempt


class PlatformTestCase(TestCase):
    def setUp(self):
        self.student = User.objects.create_user("student", password="StrongPassword!123")
        self.other_student = User.objects.create_user("other", password="StrongPassword!123")
        self.assessment = Assessment.objects.create(
            title="Python basics",
            duration_minutes=30,
            total_marks=10,
            passing_marks=5,
            is_published=True,
            negative_marking_enabled=True,
        )

    def make_mcq(self, text, order, marks=5, negative_marks="2"):
        question = Question.objects.create(
            test=self.assessment,
            question_type=Question.Type.MCQ,
            text=text,
            marks=marks,
            negative_marks=negative_marks,
            order=order,
        )
        correct = Option.objects.create(question=question, text="Correct", is_correct=True, order=1)
        wrong = Option.objects.create(question=question, text="Wrong", is_correct=False, order=2)
        return question, correct, wrong

    def make_attempt(self, user=None, status=TestAttempt.Status.IN_PROGRESS, score=0, submitted_at=None):
        user = user or self.student
        return TestAttempt.objects.create(
            student=user,
            test=self.assessment,
            attempt_number=TestAttempt.objects.filter(student=user, test=self.assessment).count() + 1,
            deadline=timezone.now() + timedelta(minutes=30),
            status=status,
            score=score,
            submitted_at=submitted_at,
            question_order=[],
        )


class AuthenticationAndPermissionsTests(PlatformTestCase):
    def test_registration_creates_student_without_staff_privileges(self):
        response = self.client.post(
            reverse("accounts:register"),
            {
                "username": "newstudent",
                "first_name": "New",
                "last_name": "Student",
                "email": "new@example.com",
                "password1": "N3w-Strong-Password!123",
                "password2": "N3w-Strong-Password!123",
                "is_staff": "on",
                "is_superuser": "on",
            },
        )
        user = User.objects.get(username="newstudent")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(hasattr(user, "student_profile"))

    def test_login_and_student_dashboard(self):
        response = self.client.post(reverse("accounts:login"), {"username": "student", "password": "StrongPassword!123"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 200)

    def test_student_cannot_open_admin_or_another_students_attempt(self):
        staff = User.objects.create_superuser("admin", "admin@example.com", "StrongPassword!123")
        attempt = self.make_attempt(user=self.other_student)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get("/django-admin/").status_code, 302)
        self.assertEqual(self.client.get(reverse("results:detail", args=(attempt.pk,))).status_code, 403)


class AssessmentWorkflowTests(PlatformTestCase):
    def test_student_can_open_test_and_render_timed_exam(self):
        question, _, _ = self.make_mcq("Which answer?", 1)
        self.client.force_login(self.student)
        listing = self.client.get(reverse("assessments:list"))
        self.assertEqual(listing.status_code, 200)
        start = self.client.post(reverse("assessments:start", args=(self.assessment.pk,)))
        self.assertEqual(start.status_code, 302)
        exam = self.client.get(start.url)
        self.assertEqual(exam.status_code, 200)
        self.assertContains(exam, "TIME REMAINING")
        self.assertContains(exam, "Which answer?")
        self.assertContains(exam, "Correct")
        self.assertNotContains(exam, "is_correct")

    def test_review_marker_is_saved_for_its_attempt(self):
        question, _, _ = self.make_mcq("Review me", 1)
        attempt = create_attempt(self.student, self.assessment)
        self.client.force_login(self.student)
        response = self.client.post(
            reverse("assessments:save", args=(attempt.pk,)),
            {"question_id": question.pk, "mark_for_review": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(attempt.submissions.get(question=question).marked_for_review)

    def test_mcq_scoring_and_negative_marking_are_server_calculated(self):
        first, correct, _ = self.make_mcq("Question one", 1)
        second, _, wrong = self.make_mcq("Question two", 2)
        attempt = create_attempt(self.student, self.assessment)
        Submission.objects.create(attempt=attempt, question=first, selected_option=correct)
        Submission.objects.create(attempt=attempt, question=second, selected_option=wrong)
        result = finish_attempt(attempt)
        self.assertEqual(result.score, Decimal("3"))
        self.assertEqual(result.status, TestAttempt.Status.SUBMITTED)

    def test_attempt_deadline_is_enforced_server_side(self):
        question, _, _ = self.make_mcq("Timed question", 1)
        attempt = create_attempt(self.student, self.assessment)
        TestAttempt.objects.filter(pk=attempt.pk).update(deadline=timezone.now() - timedelta(seconds=1))
        attempt.refresh_from_db()
        self.client.force_login(self.student)
        response = self.client.post(
            reverse("assessments:save", args=(attempt.pk,)),
            {"question_id": question.pk, "option_id": ""},
        )
        attempt.refresh_from_db()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(attempt.status, TestAttempt.Status.EXPIRED)

    def test_duplicate_submission_does_not_change_score(self):
        question, correct, wrong = self.make_mcq("One question", 1)
        attempt = create_attempt(self.student, self.assessment)
        self.client.force_login(self.student)
        response = self.client.post(
            reverse("assessments:submit", args=(attempt.pk,)),
            {f"question_{question.pk}": str(correct.pk)},
        )
        attempt.refresh_from_db()
        original_score = attempt.score
        self.client.post(reverse("assessments:submit", args=(attempt.pk,)), {f"question_{question.pk}": str(wrong.pk)})
        attempt.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(attempt.score, original_score)
        self.assertEqual(attempt.score, Decimal("5"))

    def test_retakes_are_disallowed_by_default_and_can_be_enabled(self):
        self.make_mcq("Retryable question", 1)
        self.make_attempt(status=TestAttempt.Status.SUBMITTED)
        with self.assertRaisesMessage(ValueError, "already attempted"):
            create_attempt(self.student, self.assessment)
        self.assessment.allow_retakes = True
        self.assessment.save()
        retry = create_attempt(self.student, self.assessment)
        self.assertEqual(retry.attempt_number, 2)

    @patch.dict("os.environ", {"CODE_RUNNER_URL": ""})
    def test_coding_answer_reports_unavailable_without_a_runner(self):
        question = Question.objects.create(
            test=self.assessment,
            question_type=Question.Type.CODE,
            title="Add numbers",
            text="Return the sum.",
            marks=5,
        )
        CodingTestCase.objects.create(question=question, input_data="1 2", expected_output="3")
        attempt = create_attempt(self.student, self.assessment)
        Submission.objects.create(attempt=attempt, question=question, submitted_code="print(1 + 2)")
        result = finish_attempt(attempt)
        submission = result.submissions.get(question=question)
        self.assertEqual(submission.evaluation_status, Submission.EvaluationStatus.UNAVAILABLE)
        self.assertEqual(submission.marks_awarded, 0)
        self.assertIn("unavailable", submission.feedback)

    @patch.dict("os.environ", {"CODE_RUNNER_URL": "https://runner.example/execute"})
    @patch("assessments.services.urllib.request.urlopen", side_effect=URLError("connection refused"))
    def test_coding_runner_failure_is_reported_without_awarding_marks(self, _urlopen):
        question = Question.objects.create(
            test=self.assessment,
            question_type=Question.Type.CODE,
            title="Add numbers",
            text="Return the sum.",
            marks=5,
        )
        CodingTestCase.objects.create(question=question, input_data="1 2", expected_output="3")
        attempt = create_attempt(self.student, self.assessment)
        Submission.objects.create(attempt=attempt, question=question, submitted_code="print(1 + 2)")
        result = finish_attempt(attempt)
        submission = result.submissions.get(question=question)
        self.assertEqual(submission.evaluation_status, Submission.EvaluationStatus.ERROR)
        self.assertEqual(submission.marks_awarded, 0)
        self.assertIn("did not return a valid response", submission.feedback)

    @patch.dict("os.environ", {"CODE_RUNNER_URL": "https://runner.example/execute", "CODE_RUNNER_TOKEN": "test-token"})
    @patch("assessments.services.urllib.request.urlopen")
    def test_coding_grader_awards_marks_from_runner_outputs(self, urlopen):
        from assessments.services import evaluate_code

        question = Question.objects.create(
            test=self.assessment,
            question_type=Question.Type.CODE,
            title="Add numbers",
            text="Return the sum.",
            marks=5,
        )
        CodingTestCase.objects.create(question=question, input_data="1 2", expected_output="3\n")
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"outputs":["3"]}'
        urlopen.return_value = response
        marks, status, feedback = evaluate_code(question, "print(1 + 2)")
        self.assertEqual(marks, Decimal("5.00"))
        self.assertEqual(status, Submission.EvaluationStatus.CORRECT)
        self.assertIn("1 of 1", feedback)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 8)

    def test_sample_run_endpoint_reports_missing_isolated_runner(self):
        question = Question.objects.create(
            test=self.assessment,
            question_type=Question.Type.CODE,
            title="Print a value",
            text="Print the requested value.",
            sample_input="",
            sample_output="hello",
            marks=5,
        )
        attempt = create_attempt(self.student, self.assessment)
        self.client.force_login(self.student)
        response = self.client.post(
            reverse("assessments:run", args=(attempt.pk,)),
            {"question_id": question.pk, "code": "print('hello')"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn("unavailable", response.json()["error"])

    def test_staff_can_reach_test_management(self):
        staff = User.objects.create_superuser("manager", "manager@example.com", "StrongPassword!123")
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 200)
        self.assertEqual(self.client.get("/django-admin/assessments/test/add/").status_code, 200)
        self.assertEqual(self.client.get("/django-admin/assessments/question/").status_code, 200)
        self.assertEqual(self.client.get(reverse("assessments:preview", args=(self.assessment.pk,))).status_code, 200)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse("assessments:preview", args=(self.assessment.pk,))).status_code, 403)


class ResultsAndLeaderboardTests(PlatformTestCase):
    def test_results_are_private_and_released_answers_are_visible_to_owner(self):
        question, correct, _ = self.make_mcq("Question result", 1)
        attempt = self.make_attempt(status=TestAttempt.Status.SUBMITTED, score=5, submitted_at=timezone.now())
        attempt.test.questions.add(question)
        Submission.objects.create(
            attempt=attempt,
            question=question,
            selected_option=correct,
            evaluation_status=Submission.EvaluationStatus.CORRECT,
            marks_awarded=5,
            feedback="Correct answer.",
        )
        self.client.force_login(self.other_student)
        self.assertEqual(self.client.get(reverse("results:detail", args=(attempt.pk,))).status_code, 403)
        self.client.force_login(self.student)
        response = self.client.get(reverse("results:detail", args=(attempt.pk,)))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Correct answer.")

    def test_in_progress_attempt_cannot_view_result_or_correct_answer(self):
        question, correct, _ = self.make_mcq("Still in progress", 1)
        attempt = create_attempt(self.student, self.assessment)
        Submission.objects.create(attempt=attempt, question=question, selected_option=correct)
        self.client.force_login(self.student)
        response = self.client.get(reverse("results:detail", args=(attempt.pk,)))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("assessments:take", args=(attempt.pk,)))

    def test_unreleased_result_does_not_reveal_answer_or_leaderboard_score(self):
        question, correct, _ = self.make_mcq("Secret until release", 1)
        self.assessment.result_release_at = timezone.now() + timedelta(days=1)
        self.assessment.save()
        attempt = self.make_attempt(status=TestAttempt.Status.SUBMITTED, score=5, submitted_at=timezone.now())
        Submission.objects.create(
            attempt=attempt,
            question=question,
            selected_option=correct,
            evaluation_status=Submission.EvaluationStatus.CORRECT,
            marks_awarded=5,
            feedback="Correct answer.",
        )
        self.client.force_login(self.student)
        result = self.client.get(reverse("results:detail", args=(attempt.pk,)))
        self.assertNotContains(result, "Correct answer.")
        self.assertNotContains(result, "Correct")
        self.assertContains(self.client.get(reverse("results:leaderboard")), "No released results")

    def test_leaderboard_ties_use_earliest_completion_and_one_row_per_student(self):
        earlier = timezone.now() - timedelta(minutes=5)
        later = timezone.now()
        self.make_attempt(user=self.student, status=TestAttempt.Status.SUBMITTED, score=8, submitted_at=later)
        self.make_attempt(user=self.other_student, status=TestAttempt.Status.SUBMITTED, score=8, submitted_at=earlier)
        self.client.force_login(self.student)
        response = self.client.get(reverse("results:leaderboard"), {"test": self.assessment.pk})
        self.assertEqual(response.status_code, 200)
        self.assertLess(
            response.content.index(b'<td class="fw-semibold">other</td>'),
            response.content.index(b'<td class="fw-semibold">student</td>'),
        )
