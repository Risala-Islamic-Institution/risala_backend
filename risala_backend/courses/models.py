"""
Education Domain Models - Course, CourseModule, Lesson
Based on Risala_doc class diagram.
"""
from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from risala_backend.utils.models import TimeStampedModel, UUIDModel
from risala_backend.users.models import StudentProfile


class Course(TimeStampedModel, UUIDModel):
    """
    Represents a learning offering on the platform.
    A course is created by a Teacher (TeacherProfile) and contains multiple Modules.
    Based on Risala_doc class diagram.
    """
    
    class Level(models.TextChoices):
        BEGINNER = "BEGINNER", _("Beginner")
        INTERMEDIATE = "INTERMEDIATE", _("Intermediate")
        ADVANCED = "ADVANCED", _("Advanced")
    
    class DurationType(models.TextChoices):
        FIXED = "FIXED", _("Fixed Duration")
        SUBSCRIPTION = "SUBSCRIPTION", _("Subscription Based")
    
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    description = models.TextField()
    # Free-form category — any teacher can classify their course as they wish.
    category = models.CharField(max_length=100, blank=True, default="")
    level = models.CharField(
        max_length=20,
        choices=Level.choices,
        default=Level.BEGINNER,
    )
    duration_type = models.CharField(
        max_length=20,
        choices=DurationType.choices,
        default=DurationType.FIXED,
    )
    total_weeks = models.PositiveIntegerField(default=0)
    syllabus = models.TextField(blank=True)
    prerequisites = models.TextField(blank=True)
    
    # Link to TeacherProfile (not User directly, per documentation)
    # Allow null to unblock existing migrations; application logic should still
    # require a teacher when creating new courses.
    created_by = models.ForeignKey(
        "users.TeacherProfile",
        on_delete=models.CASCADE,
        related_name="courses",
        null=True,
        blank=True,
    )
    
    thumbnail = models.ImageField(upload_to="courses/thumbnails/", blank=True, null=True)
    is_published = models.BooleanField(default=False)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)


class CourseModule(TimeStampedModel, UUIDModel):
    """
    Logical subdivision of a course.
    A module contains multiple Lessons.
    """
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="modules",
    )
    title = models.CharField(max_length=255)
    order_index = models.PositiveIntegerField(default=0)
    learning_objectives = models.TextField(blank=True)
    estimated_duration = models.PositiveIntegerField(default=0, help_text="Duration in minutes")
    is_mandatory = models.BooleanField(default=True)
    file = models.FileField(upload_to="modules/files/", blank=True, null=True, help_text="Upload a file (PDF, Video) for this module that can be dissected into lessons.")

    class Meta:
        ordering = ["order_index"]

    def __str__(self):
        return f"{self.course.title} - {self.title}"


class Lesson(TimeStampedModel, UUIDModel):
    """
    Atomic learning unit within a module.
    Can be Video, Live, or Reading type.
    """
    class LessonType(models.TextChoices):
        VIDEO = "VIDEO", _("Video")
        LIVE = "LIVE", _("Live Session")
        READING = "READING", _("Reading Material")
        QUIZ = "QUIZ", _("Quiz")

    module = models.ForeignKey(
        CourseModule,
        on_delete=models.CASCADE,
        related_name="lessons",
    )
    title = models.CharField(max_length=255)
    lesson_type = models.CharField(
        max_length=20,
        choices=LessonType.choices,
        default=LessonType.READING,
    )
    content_reference = models.TextField(blank=True, help_text="URL or content identifier")
    duration_minutes = models.PositiveIntegerField(default=0)
    requires_attendance = models.BooleanField(default=False)
    is_free_preview = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    pass_percent = models.PositiveIntegerField(default=70, help_text="Required percent to pass quizzes")
    start_marker = models.CharField(max_length=50, blank=True, help_text="Start point in the module file (e.g., page number or timestamp)")
    end_marker = models.CharField(max_length=50, blank=True, help_text="End point in the module file")

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.module.title} - {self.title}"


class Enrollment(TimeStampedModel, UUIDModel):
    """Student enrollment in a course."""

    class Status(models.TextChoices):
        ENROLLED = "ENROLLED", _("Enrolled")
        COMPLETED = "COMPLETED", _("Completed")
        CANCELLED = "CANCELLED", _("Cancelled")

    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="enrollments",
    )
    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="enrollments",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ENROLLED,
    )
    progress_percent = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("course", "student")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.student} -> {self.course} ({self.status})"

    def recompute_progress(self):
        total_lessons = Lesson.objects.filter(module__course=self.course).count()
        if total_lessons == 0:
            self.progress_percent = 0
            self.save(update_fields=["progress_percent", "updated_at"])
            return self.progress_percent
        completed = LessonProgress.objects.filter(enrollment=self, is_completed=True).count()
        percent = int(round((completed / total_lessons) * 100))
        self.progress_percent = max(0, min(100, percent))
        # If completed, mark status and issue certificate if needed
        status_updates = ["progress_percent", "updated_at"]
        if self.progress_percent == 100 and self.status != Enrollment.Status.COMPLETED:
            self.status = Enrollment.Status.COMPLETED
            status_updates.append("status")
        self.save(update_fields=status_updates)
        # Issue certificate once progress hits 100%
        if self.progress_percent == 100:
            try:
                Certificate.objects.get_or_create(enrollment=self)
                # Send notifications to student and teacher
                from risala_backend.users.models import Notification
                if getattr(self.student, "user", None):
                    Notification.objects.create(
                        user=self.student.user,
                        title="Course completed",
                        body=f"You have completed {self.course.title}. Your certificate is ready.",
                    )
                if getattr(self.course.created_by, "user", None):
                    Notification.objects.create(
                        user=self.course.created_by.user,
                        title="Student course completion",
                        body=f"A student completed {self.course.title}.",
                    )
            except Exception:
                pass
        return self.progress_percent


class LessonProgress(TimeStampedModel, UUIDModel):
    """Tracks a student's progress for a specific lesson within an enrollment."""

    enrollment = models.ForeignKey(
        Enrollment,
        on_delete=models.CASCADE,
        related_name="lesson_progress",
    )
    lesson = models.ForeignKey(
        Lesson,
        on_delete=models.CASCADE,
        related_name="progress_records",
    )
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(blank=True, null=True)
    score = models.PositiveIntegerField(blank=True, null=True, help_text="Optional quiz score")
    time_spent_minutes = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("enrollment", "lesson")
        ordering = ["lesson__order"]

    def save(self, *args, **kwargs):
        from django.utils import timezone
        if self.is_completed and not self.completed_at:
            self.completed_at = timezone.now()
        if not self.is_completed:
            self.completed_at = None
        super().save(*args, **kwargs)
        # Recompute enrollment aggregate after any change
        try:
            self.enrollment.recompute_progress()
        except Exception:
            pass


class QuizQuestion(TimeStampedModel, UUIDModel):
    lesson = models.ForeignKey(
        Lesson,
        on_delete=models.CASCADE,
        related_name="quiz_questions",
    )
    text = models.TextField()
    option_a = models.CharField(max_length=255)
    option_b = models.CharField(max_length=255)
    option_c = models.CharField(max_length=255, blank=True)
    option_d = models.CharField(max_length=255, blank=True)
    correct_option = models.CharField(max_length=1, choices=(('A','A'),('B','B'),('C','C'),('D','D')))

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.lesson.title} Q: {self.text[:30]}"


class QuizAttempt(TimeStampedModel, UUIDModel):
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="quiz_attempts")
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="quiz_attempts")
    score = models.PositiveIntegerField(default=0)
    is_passed = models.BooleanField(default=False)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-submitted_at"]


class QuizAnswer(TimeStampedModel, UUIDModel):
    attempt = models.ForeignKey(QuizAttempt, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(QuizQuestion, on_delete=models.CASCADE, related_name="answers")
    selected_option = models.CharField(max_length=1, choices=(('A','A'),('B','B'),('C','C'),('D','D')))


class CourseReview(TimeStampedModel, UUIDModel):
    enrollment = models.OneToOneField(Enrollment, on_delete=models.CASCADE, related_name="review")
    rating = models.PositiveIntegerField(default=5)
    comment = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]


class CourseAnnouncement(TimeStampedModel, UUIDModel):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="announcements")
    title = models.CharField(max_length=255)
    body = models.TextField()

    class Meta:
        ordering = ["-created_at"]


class CourseQuestion(TimeStampedModel, UUIDModel):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="questions")
    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name="course_questions")
    body = models.TextField()

    class Meta:
        ordering = ["-created_at"]


class CourseAnswer(TimeStampedModel, UUIDModel):
    question = models.ForeignKey(CourseQuestion, on_delete=models.CASCADE, related_name="answers")
    body = models.TextField()
    # answered by course teacher
    teacher = models.ForeignKey("users.TeacherProfile", on_delete=models.CASCADE, related_name="course_answers")


class Certificate(TimeStampedModel, UUIDModel):
    """Issued when an enrollment reaches 100% progress."""

    enrollment = models.OneToOneField(
        Enrollment,
        on_delete=models.CASCADE,
        related_name="certificate",
    )
    issued_at = models.DateTimeField(auto_now_add=True)
    code = models.CharField(max_length=64, unique=True, blank=True)

    class Meta:
        ordering = ["-issued_at"]

    def save(self, *args, **kwargs):
        if not self.code:
            import uuid
            self.code = uuid.uuid4().hex
        super().save(*args, **kwargs)
