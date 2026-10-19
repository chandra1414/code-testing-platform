from django.contrib.auth.decorators import login_required
from django.db.models import OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from assessments.models import Submission, Test, TestAttempt
from assessments.services import finish_attempt


@login_required
def history(request):
    attempts = TestAttempt.objects.filter(student=request.user).select_related("test")
    return render(request, "results/history.html", {"attempts": attempts})


@login_required
def detail(request, attempt_id):
    attempt = get_object_or_404(TestAttempt.objects.select_related("test", "student"), pk=attempt_id)
    if not request.user.is_staff and attempt.student_id != request.user.pk:
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden("You do not have permission to view this result.")
    if attempt.status == TestAttempt.Status.IN_PROGRESS:
        if timezone.now() < attempt.deadline:
            from django.shortcuts import redirect

            return redirect("assessments:take", attempt_id=attempt.pk)
        attempt = finish_attempt(attempt, expired=True)
    released = attempt.test.result_release_at is None or timezone.now() >= attempt.test.result_release_at
    submissions = {
        submission.question_id: submission
        for submission in attempt.submissions.select_related("question", "selected_option")
    }
    question_results = [
        {"question": question, "submission": submissions.get(question.pk)}
        for question in attempt.test.questions.all()
    ]
    if released:
        for item in question_results:
            item["correct_option_text"] = item["question"].options.filter(is_correct=True).values_list("text", flat=True).first()
    mcq_questions = [item for item in question_results if item["question"].question_type == "mcq"]
    correct_count = sum(
        item["submission"] is not None
        and item["submission"].evaluation_status == Submission.EvaluationStatus.CORRECT
        for item in mcq_questions
    )
    incorrect_count = sum(
        item["submission"] is not None
        and item["submission"].selected_option_id is not None
        and item["submission"].evaluation_status == Submission.EvaluationStatus.INCORRECT
        for item in mcq_questions
    )
    unanswered_count = len(mcq_questions) - correct_count - incorrect_count
    code_passed = sum(
        item["submission"] is not None
        and item["question"].question_type == "code"
        and item["submission"].evaluation_status == Submission.EvaluationStatus.CORRECT
        for item in question_results
    )
    return render(
        request,
        "results/detail.html",
        {
            "attempt": attempt,
            "question_results": question_results,
            "released": released,
            "correct_count": correct_count if released else None,
            "incorrect_count": incorrect_count if released else None,
            "unanswered_count": unanswered_count if released else None,
            "code_passed": code_passed if released else None,
            "percentage": (attempt.score * 100 / attempt.test.total_marks) if attempt.test.total_marks else 0,
            "time_taken_seconds": max(0, int((attempt.submitted_at - attempt.started_at).total_seconds())) if attempt.submitted_at else 0,
        },
    )


@login_required
def leaderboard(request):
    test_id = request.GET.get("test")
    selected_test = None
    eligible = TestAttempt.objects.filter(status__in=(TestAttempt.Status.SUBMITTED, TestAttempt.Status.EXPIRED))
    if test_id:
        selected_test = get_object_or_404(Test, pk=test_id, is_published=True)
        eligible = eligible.filter(test=selected_test)
        if selected_test.result_release_at and timezone.now() < selected_test.result_release_at:
            eligible = eligible.none()
    else:
        eligible = eligible.filter(test__is_published=True).filter(
            Q(test__result_release_at__isnull=True) | Q(test__result_release_at__lte=timezone.now())
        )
    best_attempt = eligible.filter(student_id=OuterRef("student_id")).order_by("-score", "submitted_at", "pk")
    leaders = eligible.filter(pk=Subquery(best_attempt.values("pk")[:1])).select_related("student", "test").order_by(
        "-score", "submitted_at", "student__username", "pk"
    )[:10]
    return render(
        request,
        "results/leaderboard.html",
        {"leaders": leaders, "tests": Test.objects.filter(is_published=True), "selected_test": selected_test},
    )
