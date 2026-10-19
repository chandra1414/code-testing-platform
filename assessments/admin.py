from django.contrib import admin
from django.forms.models import BaseInlineFormSet
from django.core.exceptions import ValidationError

from assessments.models import CodingTestCase, Option, Question, Submission, Test, TestAttempt


class OptionInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if self.instance.question_type != Question.Type.MCQ:
            return
        active_forms = [form for form in self.forms if form.cleaned_data and not form.cleaned_data.get("DELETE")]
        if len(active_forms) != 4:
            raise ValidationError("Multiple-choice questions must have exactly four answer options.")
        correct_count = sum(bool(form.cleaned_data.get("is_correct")) for form in active_forms)
        if correct_count != 1:
            raise ValidationError("Select exactly one correct answer.")


class OptionInline(admin.TabularInline):
    model = Option
    extra = 4
    formset = OptionInlineFormSet


class CodingTestCaseInline(admin.TabularInline):
    model = CodingTestCase
    extra = 1


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("title", "test", "question_type", "marks", "order")
    list_filter = ("test", "question_type")
    search_fields = ("title", "text", "test__title")
    inlines = (OptionInline, CodingTestCaseInline)


@admin.register(Test)
class TestAdmin(admin.ModelAdmin):
    list_display = ("title", "is_published", "duration_minutes", "total_marks", "created_at")
    list_filter = ("is_published", "allow_retakes")
    search_fields = ("title", "description")
    actions = ("publish_tests",)

    @admin.action(description="Publish selected tests")
    def publish_tests(self, request, queryset):
        queryset.update(is_published=True)


@admin.register(TestAttempt)
class TestAttemptAdmin(admin.ModelAdmin):
    list_display = ("student", "test", "attempt_number", "status", "score", "started_at")
    list_filter = ("status", "test")
    search_fields = ("student__username", "test__title")
    readonly_fields = ("student", "test", "attempt_number", "started_at", "deadline", "submitted_at", "status", "score")


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("attempt", "question", "evaluation_status", "marks_awarded", "updated_at")
    readonly_fields = ("attempt", "question", "selected_option", "submitted_code", "evaluation_status", "marks_awarded", "feedback")


admin.site.register(CodingTestCase)
