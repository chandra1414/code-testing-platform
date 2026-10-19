from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from assessments.models import Question, Submission, Test, TestAttempt
from assessments.services import create_attempt, finish_attempt, run_code_sample


def _student_attempt(request, attempt_id):
    return get_object_or_404(TestAttempt.objects.select_related("test"), pk=attempt_id, student=request.user)


@login_required
def test_list(request):
    if request.user.is_staff:
        return redirect("dashboard:home")
    tests = Test.objects.filter(is_published=True).prefetch_related("questions")
    attempts = {}
    for attempt in TestAttempt.objects.filter(student=request.user).order_by("-started_at"):
        attempts.setdefault(attempt.test_id, attempt)
    for test in tests:
        test.student_attempt = attempts.get(test.pk)
    return render(request, "assessments/test_list.html", {"tests": tests, "now": timezone.now()})


@login_required
def preview_test(request, test_id):
    if not request.user.is_staff:
        return HttpResponseForbidden("Only staff can preview unpublished assessments.")
    test = get_object_or_404(Test.objects.prefetch_related("questions__options"), pk=test_id)
    return render(
        request,
        "assessments/preview.html",
        {"test": test, "questions": test.questions.all()},
    )


@login_required
@require_POST
def start_test(request, test_id):
    if request.user.is_staff:
        messages.error(request, "Staff accounts cannot take student tests.")
        return redirect("dashboard:home")
    test = get_object_or_404(Test, pk=test_id, is_published=True)
    try:
        attempt = create_attempt(request.user, test)
    except ValueError as error:
        messages.error(request, str(error))
        return redirect("assessments:list")
    return redirect("assessments:take", attempt_id=attempt.pk)


@login_required
def take_test(request, attempt_id):
    attempt = _student_attempt(request, attempt_id)
    if attempt.status != TestAttempt.Status.IN_PROGRESS:
        return redirect("results:detail", attempt_id=attempt.pk)
    if attempt.deadline <= timezone.now():
        finish_attempt(attempt, expired=True)
        messages.warning(request, "Time expired. Your test was submitted automatically.")
        return redirect("results:detail", attempt_id=attempt.pk)
    questions_by_id = {question.pk: question for question in attempt.test.questions.prefetch_related("options").all()}
    questions = [questions_by_id[qid] for qid in attempt.question_order if qid in questions_by_id]
    submissions = {item.question_id: item for item in attempt.submissions.all()}
    for question in questions:
        question.existing_submission = submissions.get(question.pk)
    has_code = any(question.question_type == Question.Type.CODE for question in questions)
    return render(
        request,
        "assessments/take_test.html",
        {
            "attempt": attempt,
            "questions": questions,
            "submissions": submissions,
            "has_code": has_code,
            "remaining_seconds": max(0, int((attempt.deadline - timezone.now()).total_seconds())),
        },
    )


@login_required
@require_POST
@transaction.atomic
def save_answer(request, attempt_id):
    attempt = get_object_or_404(
        TestAttempt.objects.select_for_update().select_related("test"),
        pk=attempt_id,
        student=request.user,
    )
    if attempt.status != TestAttempt.Status.IN_PROGRESS:
        return JsonResponse({"error": "This attempt is already submitted."}, status=409)
    if timezone.now() >= attempt.deadline:
        finish_attempt(attempt, expired=True)
        return JsonResponse({"error": "Time expired; the attempt was submitted."}, status=409)
    try:
        question_id = int(request.POST.get("question_id", ""))
    except ValueError:
        return JsonResponse({"error": "Invalid question."}, status=400)
    question = get_object_or_404(Question, pk=question_id, test=attempt.test)
    mark_for_review = request.POST.get("mark_for_review")
    if mark_for_review is not None:
        submission, _ = Submission.objects.get_or_create(attempt=attempt, question=question)
        submission.marked_for_review = mark_for_review == "true"
        submission.save(update_fields=("marked_for_review", "updated_at"))
        return JsonResponse({"saved": True, "marked_for_review": submission.marked_for_review})
    if question.question_type == Question.Type.MCQ:
        option_id = request.POST.get("option_id", "")
        selected_option = None
        if option_id:
            from assessments.models import Option

            selected_option = get_object_or_404(Option, pk=option_id, question=question)
        if not option_id and not request.POST.get("clear"):
            return JsonResponse({"error": "Select an answer or explicitly clear it."}, status=400)
        Submission.objects.update_or_create(attempt=attempt, question=question, defaults={"selected_option": selected_option, "submitted_code": ""})
    else:
        source = request.POST.get("code", "")
        if len(source) > 100_000:
            return JsonResponse({"error": "Code is too long."}, status=400)
        Submission.objects.update_or_create(
            attempt=attempt,
            question=question,
            defaults={"selected_option": None, "submitted_code": source},
        )
    return JsonResponse({"saved": True})


@login_required
@require_POST
def run_code(request, attempt_id):
    attempt = _student_attempt(request, attempt_id)
    if attempt.status != TestAttempt.Status.IN_PROGRESS:
        return JsonResponse({"error": "This attempt is already submitted."}, status=409)
    if timezone.now() >= attempt.deadline:
        finish_attempt(attempt, expired=True)
        return JsonResponse({"error": "Time expired; the attempt was submitted."}, status=409)
    try:
        question_id = int(request.POST.get("question_id", ""))
    except ValueError:
        return JsonResponse({"error": "Invalid question."}, status=400)
    question = get_object_or_404(Question, pk=question_id, test=attempt.test, question_type=Question.Type.CODE)
    source = request.POST.get("code", "")
    if not source or len(source) > 100_000:
        return JsonResponse({"error": "Enter code up to 100,000 characters."}, status=400)
    throttle_key = f"code-run:{request.user.pk}"
    if not cache.add(throttle_key, "1", timeout=2):
        return JsonResponse({"error": "Please wait a moment before running code again."}, status=429)
    output, error = run_code_sample(question, source)
    if error:
        return JsonResponse({"error": error}, status=503 if "unavailable" in error else 502)
    return JsonResponse({"output": output})


@login_required
@require_POST
def submit_test(request, attempt_id):
    from assessments.models import Option

    with transaction.atomic():
        attempt = get_object_or_404(
            TestAttempt.objects.select_for_update().select_related("test"),
            pk=attempt_id,
            student=request.user,
        )
        if attempt.status == TestAttempt.Status.IN_PROGRESS:
            if timezone.now() < attempt.deadline:
                for question in attempt.test.questions.all():
                    if question.question_type == Question.Type.MCQ:
                        option_id = request.POST.get(f"question_{question.pk}", "")
                        selected_option = None
                        if option_id:
                            selected_option = Option.objects.filter(pk=option_id, question=question).first()
                        Submission.objects.update_or_create(
                            attempt=attempt,
                            question=question,
                            defaults={"selected_option": selected_option, "submitted_code": ""},
                        )
                    else:
                        code = request.POST.get(f"code_{question.pk}", "")
                        if len(code) <= 100_000:
                            Submission.objects.update_or_create(
                                attempt=attempt,
                                question=question,
                                defaults={"selected_option": None, "submitted_code": code},
                            )
            attempt = finish_attempt(attempt, expired=timezone.now() >= attempt.deadline)
    return redirect("results:detail", attempt_id=attempt.pk)
