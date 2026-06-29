"""
Risala User and Profile Models
Following the official Risala_doc class diagram and ER diagram.
"""

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from risala_backend.users.managers import UserManager
from risala_backend.utils.models import TimeStampedModel, UUIDModel

# =============================================================================
# 1. IDENTITY & ACCESS CONTROL DOMAIN
# =============================================================================


class Permission(TimeStampedModel, UUIDModel):
    """Fine-grained access control permissions."""

    class Scope(models.TextChoices):
        SYSTEM = "SYSTEM", _("System")
        COURSE = "COURSE", _("Course")
        SESSION = "SESSION", _("Session")

    code = models.CharField(
        max_length=100, unique=True, help_text="e.g., CREATE_SESSION"
    )
    description = models.TextField(blank=True)
    scope = models.CharField(max_length=20, choices=Scope.choices, default=Scope.SYSTEM)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class Role(TimeStampedModel, UUIDModel):
    """System-level authority roles."""

    class RoleName(models.TextChoices):
        ADMIN = "ADMIN", _("Admin")
        USTAZ = "USTAZ", _("Ustaz")  # Teacher
        STUDENT = "STUDENT", _("Student")
        FINANCE = "FINANCE", _("Finance")
        SUPPORT = "SUPPORT", _("Support")

    name = models.CharField(max_length=50, choices=RoleName.choices, unique=True)
    description = models.TextField(blank=True)
    permissions = models.ManyToManyField(
        Permission,
        through="RolePermission",
        related_name="roles",
        blank=True,
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.get_name_display()


class RolePermission(TimeStampedModel, UUIDModel):
    """Junction table for Role-Permission Many-to-Many relationship."""

    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE)

    class Meta:
        unique_together = ("role", "permission")

    def __str__(self):
        return f"{self.role.name} - {self.permission.code}"


class User(AbstractBaseUser, PermissionsMixin, TimeStampedModel, UUIDModel):
    """
    Custom User model for Risala platform.
    Based on the official Risala_doc class diagram.
    """

    class Gender(models.TextChoices):
        MALE = "MALE", _("Male")
        FEMALE = "FEMALE", _("Female")
        OTHER = "OTHER", _("Other")

    # Core identity fields
    username = models.CharField(max_length=150, unique=True)
    email = models.EmailField(_("Email Address"), unique=True)
    full_name = models.CharField(_("Full Name"), max_length=255, blank=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)

    # Personal information
    gender = models.CharField(max_length=10, choices=Gender.choices, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    country = models.CharField(max_length=100, blank=True)
    user_timezone = models.CharField(max_length=50, default="UTC")
    preferred_language = models.CharField(max_length=10, default="en")

    # Account status
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_suspended = models.BooleanField(default=False)
    suspension_reason = models.TextField(blank=True)

    # Timestamps
    last_login_at = models.DateTimeField(null=True, blank=True)
    date_joined = models.DateTimeField(default=timezone.now)

    # Many-to-Many relationship with Role
    roles = models.ManyToManyField(
        Role,
        through="users.UserRole",
        through_fields=("user", "role"),
        related_name="users",
        blank=True,
    )

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.email

    def get_absolute_url(self) -> str:
        """Get URL for user's detail view."""
        return reverse("users:detail", kwargs={"username": self.username})

    def has_role(self, role_name: str) -> bool:
        """Check if user has a specific role."""
        return self.roles.filter(name=role_name).exists()

    def get_primary_role(self):
        """Get the user's primary (first) role."""
        return self.roles.first()


class UserRole(TimeStampedModel, UUIDModel):
    """Junction table for User-Role Many-to-Many relationship."""

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    assigned_at = models.DateTimeField(auto_now_add=True)
    assigned_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="role_assignments_made",
    )

    class Meta:
        unique_together = ("user", "role")

    def __str__(self):
        return f"{self.user.email} - {self.role.name}"


# =============================================================================
# 2. PROFILE DOMAIN (Composition, NOT Inheritance)
# =============================================================================


class TeacherProfile(TimeStampedModel, UUIDModel):
    """
    Profile for Teachers (Ustaz/Ustazah).
    Extends User through composition, NOT inheritance.
    """

    class TeachingLevel(models.TextChoices):
        BEGINNER = "BEGINNER", _("Beginner")
        INTERMEDIATE = "INTERMEDIATE", _("Intermediate")
        ADVANCED = "ADVANCED", _("Advanced")

    class Specialization(models.TextChoices):
        TAJWEED = "TAJWEED", _("Tajweed")
        HIFZ = "HIFZ", _("Hifz (Memorization)")
        TAFSIR = "TAFSIR", _("Tafsir")
        ARABIC = "ARABIC", _("Arabic Language")
        FIQH = "FIQH", _("Fiqh")
        AQEEDAH = "AQEEDAH", _("Aqeedah")

    class VerificationStatus(models.TextChoices):
        PENDING = "PENDING", _("Pending")
        VERIFIED = "VERIFIED", _("Verified")
        REJECTED = "REJECTED", _("Rejected")

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="teacher_profile",
    )
    biography = models.TextField(blank=True)
    qualifications = models.TextField(blank=True)
    years_of_experience = models.PositiveIntegerField(default=0)
    teaching_languages = models.JSONField(
        default=list, blank=True
    )  # ["Arabic", "English"]
    teaching_level = models.CharField(
        max_length=20,
        choices=TeachingLevel.choices,
        default=TeachingLevel.BEGINNER,
    )
    specialization = models.CharField(
        max_length=20,
        choices=Specialization.choices,
        blank=True,
    )
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    rating_average = models.DecimalField(max_digits=3, decimal_places=2, default=0.00)
    total_students = models.PositiveIntegerField(default=0)
    verification_status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING,
    )
    verified_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_teachers",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    profile_visibility = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Teacher Profile"
        verbose_name_plural = "Teacher Profiles"

    def __str__(self):
        return f"Teacher: {self.user.full_name or self.user.username}"


class StudentProfile(TimeStampedModel, UUIDModel):
    """
    Profile for Students/Learners.
    Extends User through composition, NOT inheritance.
    """

    class CurrentLevel(models.TextChoices):
        BEGINNER = "BEGINNER", _("Beginner")
        INTERMEDIATE = "INTERMEDIATE", _("Intermediate")
        ADVANCED = "ADVANCED", _("Advanced")

    class EnrollmentStatus(models.TextChoices):
        ACTIVE = "ACTIVE", _("Active")
        INACTIVE = "INACTIVE", _("Inactive")
        SUSPENDED = "SUSPENDED", _("Suspended")

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="student_profile",
    )
    learning_goals = models.TextField(blank=True)
    current_level = models.CharField(
        max_length=20,
        choices=CurrentLevel.choices,
        default=CurrentLevel.BEGINNER,
    )
    preferred_schedule = models.TextField(blank=True)
    guardian_contact = models.CharField(max_length=100, blank=True, null=True)
    enrollment_status = models.CharField(
        max_length=20,
        choices=EnrollmentStatus.choices,
        default=EnrollmentStatus.ACTIVE,
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Student Profile"
        verbose_name_plural = "Student Profiles"

    def __str__(self):
        return f"Student: {self.user.full_name or self.user.username}"


# =============================================================================
# SIGNALS - Auto-create profiles based on role assignment
# =============================================================================


@receiver(post_save, sender=UserRole)
def create_profile_on_role_assignment(sender, instance, created, **kwargs):
    """Auto-create profile when a role is assigned to a user."""
    if created:
        user = instance.user
        role_name = instance.role.name

        if role_name == Role.RoleName.USTAZ:
            TeacherProfile.objects.get_or_create(user=user)
        elif role_name == Role.RoleName.STUDENT:
            StudentProfile.objects.get_or_create(user=user)


# =============================================================================
# 3. SCHEDULING & BOOKINGS DOMAIN
# =============================================================================


class TeacherAvailability(TimeStampedModel, UUIDModel):
    """Recurring weekly availability for a teacher in their timezone."""

    class WeekDay(models.IntegerChoices):
        MONDAY = 0, _("Monday")
        TUESDAY = 1, _("Tuesday")
        WEDNESDAY = 2, _("Wednesday")
        THURSDAY = 3, _("Thursday")
        FRIDAY = 4, _("Friday")
        SATURDAY = 5, _("Saturday")
        SUNDAY = 6, _("Sunday")

    teacher = models.ForeignKey(
        TeacherProfile,
        on_delete=models.CASCADE,
        related_name="availabilities",
    )
    day_of_week = models.IntegerField(choices=WeekDay.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    timezone = models.CharField(max_length=50, default="UTC")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["teacher", "day_of_week", "start_time"]
        unique_together = (
            "teacher",
            "day_of_week",
            "start_time",
            "end_time",
            "timezone",
        )

    def __str__(self):
        return f"{self.teacher} - {self.get_day_of_week_display()} {self.start_time}-{self.end_time} ({self.timezone})"


class BookingOrder(TimeStampedModel, UUIDModel):
    """
    Represents a package/order of multiple session bookings.
    Created when a student selects a recurring schedule (e.g., 1 month package).
    """

    class Status(models.TextChoices):
        REQUESTED = "REQUESTED", _("Requested")
        APPROVED = "APPROVED", _("Approved")
        PENDING = "PENDING", _("Pending Payment")
        PAID = "PAID", _("Paid")
        FAILED = "FAILED", _("Failed")
        EXPIRED = "EXPIRED", _("Expired")

    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="booking_orders",
    )
    teacher = models.ForeignKey(
        TeacherProfile,
        on_delete=models.CASCADE,
        related_name="booking_orders",
    )
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    currency = models.CharField(max_length=3, default="usd")
    stripe_checkout_id = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    def __str__(self):
        return f"Order {self.id} - {self.student} ({self.status})"


class SessionBooking(TimeStampedModel, UUIDModel):
    """A booked session between a student and a teacher."""

    class Status(models.TextChoices):
        PENDING = "PENDING", _("Pending (Legacy)")
        REQUESTED = "REQUESTED", _("Requested")
        RESERVED = "RESERVED", _("Reserved (Awaiting Payment)")
        APPROVED = "APPROVED", _("Approved")
        CONFIRMED = "CONFIRMED", _("Confirmed")
        DECLINED = "DECLINED", _("Declined")
        EXPIRED = "EXPIRED", _("Expired")
        CANCELLED = "CANCELLED", _("Cancelled")

    teacher = models.ForeignKey(
        TeacherProfile,
        on_delete=models.CASCADE,
        related_name="session_bookings",
    )
    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="session_bookings",
    )
    order = models.ForeignKey(
        BookingOrder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bookings",
    )
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.REQUESTED
    )

    class Meta:
        ordering = ["start_at"]
        indexes = [
            models.Index(fields=["teacher", "start_at", "end_at"]),
            models.Index(fields=["student", "start_at", "end_at"]),
        ]

    def __str__(self):
        return f"{self.teacher} -> {self.student} @ {self.start_at}"

    def overlaps(self, other_start, other_end) -> bool:
        return not (self.end_at <= other_start or self.start_at >= other_end)


class Notification(TimeStampedModel, UUIDModel):
    """User-facing notification entry (booking lifecycle, etc.)."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    title = models.CharField(max_length=150)
    body = models.TextField(blank=True)
    related_booking = models.ForeignKey(
        SessionBooking,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
    )
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class TimeSlot(TimeStampedModel, UUIDModel):
    class BookingType(models.TextChoices):
        SINGLE = "SINGLE", _("Single Booking Only")
        RANGE = "RANGE", _("Range Booking Only")
        BOTH = "BOTH", _("Both Single & Range")

    teacher = models.ForeignKey(
        TeacherProfile, on_delete=models.CASCADE, related_name="time_slots"
    )
    start_time = models.DateTimeField(db_index=True)
    end_time = models.DateTimeField()
    duration_minutes = models.PositiveIntegerField(default=60)
    is_booked = models.BooleanField(default=False)
    allowed_booking_type = models.CharField(
        max_length=10, choices=BookingType.choices, default=BookingType.BOTH
    )
    booking = models.OneToOneField(
        SessionBooking,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="time_slot",
    )

    batch_id = models.UUIDField(null=True, blank=True, db_index=True)
    batch_start_date = models.DateField(null=True, blank=True)
    batch_end_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["start_time"]
        indexes = [
            models.Index(fields=["teacher", "start_time"]),
            models.Index(fields=["teacher", "is_booked"]),
        ]
        unique_together = (("teacher", "start_time"),)

    def __str__(self):
        return f"{self.teacher.user.username} - {self.start_time.strftime('%Y-%m-%d %H:%M')}"
