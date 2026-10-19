import json
import os
import random
import urllib.error
import urllib.request
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from assessments.models import CodingTestCase, Option, Question, Submission, TestAttempt


def create_attempt(student, test):
    with transaction.atomic():
        locked_test = type(test).objects.select_for_update().get(pk=test.pk)
        if not locked_test.is_available():
            raise ValueError("This test is not currently available.")
        previous = TestAttempt.objects.filter(student=student, test=locked_test)
        if previous.filter(status=TestAttempt.Status.IN_PROGRESS).exists():
            return previous.filter(status=TestAttempt.Status.IN_PROGRESS).latest("started_at")
        if previous.exists() and not locked_test.allow_retakes:
            raise ValueError("You have already attempted this test.")
        started_at = timezone.now()
        ordered_ids = list(locked_test.questions.values_list("id", flat=True))
        if not ordered_ids:
            raise ValueError("This test has no questions yet.")
        random.SystemRandom().shuffle(ordered_ids)
        return TestAttempt.objects.create(
            student=student,
            test=locked_test,
            attempt_number=(previous.order_by("-attempt_number").values_list("attempt_number", flat=True).first() or 0) + 1,
            started_at=started_at,
            deadline=started_at + timedelta(minutes=locked_test.duration_minutes),
            question_order=ordered_ids,
        )


def _normalize_output(value):
    return "\n".join(line.rstrip() for line in value.strip().splitlines())


def _run_in_sandbox(source, cases):
    runner_url = os.getenv("CODE_RUNNER_URL", "").strip()
    if not runner_url:
        return None, Submission.EvaluationStatus.UNAVAILABLE, "Coding evaluation is unavailable: no isolated code runner is configured."
    if not runner_url.startswith("https://") and not runner_url.startswith("http://localhost"):
        return None, Submission.EvaluationStatus.ERROR, "Coding evaluation is misconfigured; contact an administrator."
    payload = json.dumps({"language": "python", "source": source, "test_cases": cases}).encode("utf-8")
    request = urllib.request.Request(
        runner_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.getenv('CODE_RUNNER_TOKEN', '')}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            response_body = response.read(1_000_001)
        if len(response_body) > 1_000_000:
            return None, Submission.EvaluationStatus.ERROR, "The isolated code runner returned an oversized response."
        result = json.loads(response_body.decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None, Submission.EvaluationStatus.ERROR, "The isolated code runner did not return a valid response."
    if not isinstance(result, dict):
        return None, Submission.EvaluationStatus.ERROR, "The isolated code runner returned an invalid response."
    outputs = result.get("outputs")
    if not isinstance(outputs, list) or len(outputs) != len(cases) or any(not isinstance(value, str) for value in outputs):
        return None, Submission.EvaluationStatus.ERROR, "The isolated code runner returned an invalid response."
    return outputs, Submission.EvaluationStatus.PENDING, ""


def evaluate_code(question, source):
    cases = list(CodingTestCase.objects.filter(question=question).values("input_data", "expected_output"))
    sample = {"input_data": question.sample_input, "expected_output": question.sample_output}
    if question.sample_output and sample not in cases:
        cases.append(sample)
    if not cases:
        return Decimal("0"), Submission.EvaluationStatus.ERROR, "No test cases are configured for this coding question."
    outputs, status, feedback = _run_in_sandbox(source, cases)
    if outputs is None:
        return Decimal("0"), status, feedback
    passed = sum(
        _normalize_output(actual) == _normalize_output(case["expected_output"])
        for actual, case in zip(outputs, cases)
    )
    marks = (Decimal(question.marks) * Decimal(passed) / Decimal(len(cases))).quantize(Decimal("0.01"))
    feedback = f"{passed} of {len(cases)} test cases passed."
    status = Submission.EvaluationStatus.CORRECT if passed == len(cases) else Submission.EvaluationStatus.INCORRECT
    return marks, status, feedback


def run_code_sample(question, source):
    if not question.sample_output:
        return None, "This coding question has no sample case configured."
    outputs, status, feedback = _run_in_sandbox(
        source,
        [{"input_data": question.sample_input, "expected_output": ""}],
    )
    if outputs is None:
        return None, feedback
    return outputs[0], ""


def finish_attempt(attempt, expired=False):
    with transaction.atomic():
        locked = TestAttempt.objects.select_for_update().select_related("test").get(pk=attempt.pk)
        if locked.status != TestAttempt.Status.IN_PROGRESS:
            return locked
        submissions = list(locked.submissions.select_related("question", "selected_option").all())
        score = Decimal("0")
        for submission in submissions:
            question = submission.question
            if question.question_type == Question.Type.MCQ:
                correct = submission.selected_option_id is not None and Option.objects.filter(
                    pk=submission.selected_option_id, question=question, is_correct=True
                ).exists()
                if correct:
                    submission.marks_awarded = Decimal(question.marks)
                    submission.evaluation_status = Submission.EvaluationStatus.CORRECT
                    submission.feedback = "Correct answer."
                    score += submission.marks_awarded
                elif submission.selected_option_id and locked.test.negative_marking_enabled:
                    submission.marks_awarded = -question.negative_marks
                    submission.evaluation_status = Submission.EvaluationStatus.INCORRECT
                    submission.feedback = "Incorrect answer."
                    score += submission.marks_awarded
                else:
                    submission.marks_awarded = Decimal("0")
                    submission.evaluation_status = Submission.EvaluationStatus.INCORRECT
                    submission.feedback = "Incorrect answer." if submission.selected_option_id else "Not answered."
                submission.save(update_fields=("marks_awarded", "evaluation_status", "feedback", "updated_at"))
            elif submission.submitted_code:
                marks, status, feedback = evaluate_code(question, submission.submitted_code)
                submission.marks_awarded = marks
                submission.evaluation_status = status
                submission.feedback = feedback
                submission.save(update_fields=("marks_awarded", "evaluation_status", "feedback", "updated_at"))
                score += marks
        locked.score = max(Decimal("0"), min(score, Decimal(locked.test.total_marks)))
        locked.status = TestAttempt.Status.EXPIRED if expired else TestAttempt.Status.SUBMITTED
        locked.submitted_at = timezone.now()
        locked.save(update_fields=("score", "status", "submitted_at"))
        return locked
