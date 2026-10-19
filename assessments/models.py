from django.conf import settings
from django.db import models
from django.utils import timezone


class Test(models.Model):
    title = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    duration_minutes = models.PositiveSmallIntegerField()
    total_marks = models.PositiveIntegerField()
    passing_marks = models.PositiveIntegerField()
    is_published = models.BooleanField(default=False)
    allow_retakes = models.BooleanField(default=False)
    negative_marking_enabled = models.BooleanField(default=False)
    available_from = models.DateTimeField(null=True, blank=True)
    available_until = models.DateTimeField(null=True, blank=True)
    result_release_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(condition=models.Q(passing_marks__lte=models.F("total_marks")), name="test_passing_within_total"),
            models.CheckConstraint(condition=models.Q(total_marks__gt=0), name="test_total_marks_positive"),
            models.CheckConstraint(condition=models.Q(duration_minutes__gt=0), name="test_duration_positive"),
        ]

    def __str__(self):
        return self.title

    def is_available(self):
        now = timezone.now()
        return self.is_published and (self.available_from is None or self.available_from <= now) and (
            self.available_until is None or now <= self.available_until
        )


class Question(models.Model):
    class Type(models.TextChoices):
        MCQ = "mcq", "Multiple choice"
        CODE = "code", "Python coding"

    test = models.ForeignKey(Test, on_delete=models.CASCADE, related_name="questions")
    question_type = models.CharField(max_length=8, choices=Type.choices, default=Type.MCQ)
    title = models.CharField(max_length=180, blank=True)
    text = models.TextField()
    input_format = models.TextField(blank=True)
    output_format = models.TextField(blank=True)
    constraints = models.TextField(blank=True)
    sample_input = models.TextField(blank=True)
    sample_output = models.TextField(blank=True)
    starter_code = models.TextField(blank=True)
    marks = models.PositiveIntegerField()
    negative_marks = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    explanation = models.TextField(blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("order", "id")
        indexes = [models.Index(fields=("test", "order"))]
        constraints = [
            models.CheckConstraint(condition=models.Q(marks__gt=0), name="question_marks_positive"),
            models.CheckConstraint(condition=models.Q(negative_marks__gte=0), name="question_negative_marks_nonnegative"),
        ]

    def __str__(self):
        return self.title or f"{self.test}: question {self.order}"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.marks <= 0:
            raise ValidationError({"marks": "Question marks must be greater than zero."})
        if self.negative_marks < 0:
            raise ValidationError({"negative_marks": "Negative marks cannot be less than zero."})


class Option(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="options")
    text = models.TextField()
    is_correct = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("order", "id")

    def __str__(self):
        return self.text[:80]


class CodingTestCase(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="test_cases")
    input_data = models.TextField(blank=True)
    expected_output = models.TextField()
    is_hidden = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.question} ({'hidden' if self.is_hidden else 'sample'})"


class TestAttempt(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "In progress"
        SUBMITTED = "submitted", "Submitted"
        EXPIRED = "expired", "Expired"

    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="test_attempts")
    test = models.ForeignKey(Test, on_delete=models.PROTECT, related_name="attempts")
    attempt_number = models.PositiveSmallIntegerField()
    started_at = models.DateTimeField(default=timezone.now)
    deadline = models.DateTimeField()
    submitted_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.IN_PROGRESS)
    score = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    question_order = models.JSONField(default=list)

    class Meta:
        ordering = ("-started_at",)
        constraints = [
            models.UniqueConstraint(fields=("student", "test", "attempt_number"), name="unique_student_test_attempt"),
        ]
        indexes = [models.Index(fields=("student", "test", "status")), models.Index(fields=("status", "deadline"))]

    def __str__(self):
        return f"{self.student} — {self.test} (attempt {self.attempt_number})"


class Submission(models.Model):
    class EvaluationStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        CORRECT = "correct", "Correct"
        INCORRECT = "incorrect", "Incorrect"
        UNAVAILABLE = "unavailable", "Evaluation unavailable"
        ERROR = "error", "Evaluation error"

    attempt = models.ForeignKey(TestAttempt, on_delete=models.CASCADE, related_name="submissions")
    question = models.ForeignKey(Question, on_delete=models.PROTECT, related_name="submissions")
    selected_option = models.ForeignKey(Option, null=True, blank=True, on_delete=models.SET_NULL, related_name="submissions")
    submitted_code = models.TextField(blank=True)
    evaluation_status = models.CharField(max_length=12, choices=EvaluationStatus.choices, default=EvaluationStatus.PENDING)
    marks_awarded = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    feedback = models.TextField(blank=True)
    marked_for_review = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("attempt", "question"), name="unique_attempt_question_submission")]

    def __str__(self):
        return f"{self.attempt} — {self.question}"
