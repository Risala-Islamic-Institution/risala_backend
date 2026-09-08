from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from risala_backend.utils.models import TimeStampedModel, UUIDModel


class SessionAttendance(TimeStampedModel, UUIDModel):
    """
    Tracks real live-classroom presence for a SessionBooking.
    Automatically calculated from client heartbeats and room events.
    """

    class Verdict(models.TextChoices):
        PENDING = "PENDING", _("Pending / In Progress")
        VERIFIED_COMPLETE = "VERIFIED_COMPLETE", _("Verified Complete (Both Attended)")
        TEACHER_ABSENT = "TEACHER_ABSENT", _("Teacher Absent (No-Show)")
        STUDENT_ABSENT = "STUDENT_ABSENT", _("Student Absent (No-Show)")
        PARTIAL_DISPUTED = "PARTIAL_DISPUTED", _("Partial / Disputed")
        EXCUSED = "EXCUSED", _("Excused & Rescheduled")

    class SettlementStatus(models.TextChoices):
        HELD_IN_ESCROW = "HELD_IN_ESCROW", _("Held in Escrow")
        RELEASED_TO_TEACHER = "RELEASED_TO_TEACHER", _("Released to Teacher")
        REFUNDED_TO_STUDENT = "REFUNDED_TO_STUDENT", _("Refunded to Student")
        CREDITED_RESCHEDULE = "CREDITED_RESCHEDULE", _("Credited for Reschedule")

    booking = models.OneToOneField(
        "users.SessionBooking",
        on_delete=models.CASCADE,
        related_name="attendance",
    )

    # Teacher presence metrics
    teacher_joined_at = models.DateTimeField(null=True, blank=True)
    teacher_left_at = models.DateTimeField(null=True, blank=True)
    teacher_minutes_present = models.PositiveIntegerField(default=0)
    teacher_heartbeat_count = models.PositiveIntegerField(default=0)

    # Student presence metrics
    student_joined_at = models.DateTimeField(null=True, blank=True)
    student_left_at = models.DateTimeField(null=True, blank=True)
    student_minutes_present = models.PositiveIntegerField(default=0)
    student_heartbeat_count = models.PositiveIntegerField(default=0)

    # Attendance decision
    verdict = models.CharField(
        max_length=30,
        choices=Verdict.choices,
        default=Verdict.PENDING,
    )
    settlement_status = models.CharField(
        max_length=30,
        choices=SettlementStatus.choices,
        default=SettlementStatus.HELD_IN_ESCROW,
    )

    # Admin overrides / audit notes
    admin_notes = models.TextField(blank=True)
    evaluated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Session Attendance"
        verbose_name_plural = "Session Attendances"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Attendance for Booking {self.booking_id} ({self.verdict})"

    def evaluate_attendance(self):
        """
        Automated presence evaluation:
        - Scheduled duration derived from booking (default 60 min if unspecified).
        - If teacher >= 75% and student >= 75% -> VERIFIED_COMPLETE.
        - If teacher < 15 min -> TEACHER_ABSENT (Student gets refund / reschedule, teacher gets 0).
        - If student < 15 min and teacher >= 15 min -> STUDENT_ABSENT (Teacher gets paid, student no refund).
        """
        duration = 60
        if self.booking and self.booking.start_at and self.booking.end_at:
            total_sec = (self.booking.end_at - self.booking.start_at).total_seconds()
            duration = max(int(total_sec / 60), 30)

        min_req = int(duration * 0.75)

        if self.teacher_minutes_present >= min_req and self.student_minutes_present >= min_req:
            self.verdict = self.Verdict.VERIFIED_COMPLETE
            self.settlement_status = self.SettlementStatus.RELEASED_TO_TEACHER
        elif self.teacher_minutes_present < 15 and self.teacher_heartbeat_count <= 2:
            self.verdict = self.Verdict.TEACHER_ABSENT
            self.settlement_status = self.SettlementStatus.REFUNDED_TO_STUDENT
        elif self.student_minutes_present < 15 and self.teacher_minutes_present >= 15:
            self.verdict = self.Verdict.STUDENT_ABSENT
            self.settlement_status = self.SettlementStatus.RELEASED_TO_TEACHER
        else:
            self.verdict = self.Verdict.PARTIAL_DISPUTED

        self.evaluated_at = timezone.now()
        self.save()


class AttendanceHeartbeat(TimeStampedModel, UUIDModel):
    """
    Lightweight periodic ping reported by client during live class.
    """

    class EventType(models.TextChoices):
        JOIN = "JOIN", _("User Joined Room")
        PING = "PING", _("Periodic Heartbeat (every 2-3 min)")
        LEAVE = "LEAVE", _("User Left Room")

    attendance = models.ForeignKey(
        SessionAttendance,
        on_delete=models.CASCADE,
        related_name="heartbeats",
    )
    user = models.ForeignKey("users.User", on_delete=models.CASCADE)
    role = models.CharField(max_length=20)  # 'TEACHER' or 'STUDENT'
    event_type = models.CharField(
        max_length=20,
        choices=EventType.choices,
        default=EventType.PING,
    )
    is_mic_active = models.BooleanField(default=True)
    is_camera_active = models.BooleanField(default=True)
    other_participant_detected = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at"]


class TeacherPayoutLedger(TimeStampedModel, UUIDModel):
    """
    Platform Financial Ledger:
    - 10% Platform fee commission
    - 90% Teacher net earnings
    - Escrow tracking
    """

    class Status(models.TextChoices):
        HELD = "HELD", _("Held in Escrow")
        PAYABLE = "PAYABLE", _("Payable to Teacher")
        SETTLED = "SETTLED", _("Settled / Disbursed")
        REFUNDED = "REFUNDED", _("Refunded to Student")
        CANCELLED = "CANCELLED", _("Cancelled")

    booking = models.OneToOneField(
        "users.SessionBooking",
        on_delete=models.CASCADE,
        related_name="payout_ledger",
    )
    teacher = models.ForeignKey(
        "users.TeacherProfile",
        on_delete=models.CASCADE,
        related_name="payout_ledgers",
    )
    gross_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    platform_fee_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("10.00"))
    platform_fee_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    teacher_net_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.HELD,
    )
    disbursed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]


class SessionExcuse(TimeStampedModel, UUIDModel):
    """
    Emergency excuse submitted by student or teacher before or during a class.
    If approved by admin, generates a free reschedule credit.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", _("Pending Admin Review")
        APPROVED = "APPROVED", _("Approved (Reschedule Allowed)")
        REJECTED = "REJECTED", _("Rejected")

    booking = models.ForeignKey(
        "users.SessionBooking",
        on_delete=models.CASCADE,
        related_name="excuses",
    )
    submitted_by = models.ForeignKey("users.User", on_delete=models.CASCADE)
    reason = models.CharField(max_length=255)
    explanation = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    reviewed_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_excuses",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
