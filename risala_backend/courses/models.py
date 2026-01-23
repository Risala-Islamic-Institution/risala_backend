"""
Education Domain Models - Course, CourseModule, Lesson
Based on Risala_doc class diagram.
"""
from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from risala_backend.utils.models import TimeStampedModel, UUIDModel


class Course(TimeStampedModel, UUIDModel):
    """
    Represents a learning offering on the platform.
    A course is created by a Teacher (TeacherProfile) and contains multiple Modules.
    Based on Risala_doc class diagram.
    """
    
    class Category(models.TextChoices):
        QURAN = "QURAN", _("Quran")
        TAJWEED = "TAJWEED", _("Tajweed")
        ARABIC = "ARABIC", _("Arabic Language")
        TAFSIR = "TAFSIR", _("Tafsir")
        HIFZ = "HIFZ", _("Hifz (Memorization)")
        FIQH = "FIQH", _("Fiqh")
        AQEEDAH = "AQEEDAH", _("Aqeedah")
    
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
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.QURAN,
    )
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

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.module.title} - {self.title}"
