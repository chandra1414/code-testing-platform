from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Q
from django.shortcuts import render
from django.utils import timezone

from accounts.models import StudentProfile
from assessments.models import Question, Test, TestAttempt


@login_required
def home(request):
    if request.user.is_staff:
        attempts = TestAttempt.objects.select_related("student", "test").filter(
            status__in=(TestAttempt.Status.SUBMITTED, TestAttempt.Status.EXPIRED)
        )
        tests = list(
            Test.objects.annotate(
                question_count=Count("questions"),
                participation_count=Count(
                    "attempts",
                    filter=Q(attempts__status__in=(TestAttempt.Status.SUBMITTED, TestAttempt.Status.EXPIRED)),
                ),
                average_score=Avg(
                    "attempts__score",
                    filter=Q(attempts__status__in=(TestAttempt.Status.SUBMITTED, TestAttempt.Status.EXPIRED)),
                ),
            ).order_by("-created_at")[:8]
        )
        top_attempts = attempts.order_by("-score", "submitted_at", "student__username")[:5]
        context = {
            "is_admin": True,
            "student_count": StudentProfile.objects.count(),
            "test_count": Test.objects.count(),
            "question_count": Question.objects.count(),
            "completed_count": attempts.count(),
            "average_score": attempts.aggregate(value=Avg("score"))["value"] or 0,
            "recent_attempts": attempts.order_by("-submitted_at")[:8],
            "top_attempts": top_attempts,
            "tests": tests,
            "participation_labels": [test.title for test in reversed(tests)],
            "participation_values": [test.participation_count for test in reversed(tests)],
            "performance_labels": [test.title for test in reversed(tests)],
            "performance_values": [
                float(test.average_score or 0) / test.total_marks * 100 if test.total_marks else 0
                for test in reversed(tests)
            ],
        }
    else:
        attempts = TestAttempt.objects.filter(student=request.user, status__in=(TestAttempt.Status.SUBMITTED, TestAttempt.Status.EXPIRED))
        now = timezone.now()
        available_tests = list(Test.objects.filter(is_published=True).annotate(question_count=Count("questions")))
        for test in available_tests:
            if test.available_from and test.available_from > now:
                test.student_status = "Upcoming"
            elif test.available_until and test.available_until < now:
                test.student_status = "Closed"
            else:
                test.student_status = "Available"
        chart_attempts = list(attempts.select_related("test").order_by("-submitted_at")[:6])
        chart_attempts.reverse()
        context = {
            "is_admin": False,
            "student_name": request.user.first_name or request.user.username,
            "available_tests": available_tests,
            "completed_count": attempts.count(),
            "average_score": attempts.aggregate(value=Avg("score"))["value"] or 0,
            "best_score": attempts.order_by("-score").values_list("score", flat=True).first() or 0,
            "recent_attempts": attempts.select_related("test").order_by("-submitted_at")[:5],
            "performance_labels": [attempt.test.title for attempt in chart_attempts],
            "performance_values": [
                float(attempt.score) / attempt.test.total_marks * 100 if attempt.test.total_marks else 0
                for attempt in chart_attempts
            ],
        }
    return render(request, "dashboard/home.html", context)
