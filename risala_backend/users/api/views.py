"""
API Views for User and Profile models.
"""

import uuid
from datetime import date, datetime, timedelta, timezone as py_timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.mixins import (CreateModelMixin, DestroyModelMixin,
                                   ListModelMixin, RetrieveModelMixin,
                                   UpdateModelMixin)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from risala_backend.users.models import (
    BookingOrder,
    Notification,
    SessionBooking,
    StudentProfile,
    TeacherAvailability,
    TeacherProfile,
    TimeSlot,
    User,
    SessionAttendance,
    AttendanceHeartbeat,
    TeacherPayoutLedger,
    SessionExcuse,
    SupportedBank,
)

from .serializers import (
    BookingOrderSerializer,
    BookingPackageSerializer,
    BulkSlotCreateSerializer,
    BulkSlotDeleteSerializer,
    NotificationSerializer,
    RangeBookingRequestSerializer,
    SessionBookingSerializer,
    StudentProfileSerializer,
    TeacherAvailabilitySerializer,
    TeacherProfileSerializer,
    TimeSlotSerializer,
    UserSerializer,
    AttendanceHeartbeatSerializer,
    SessionAttendanceSerializer,
    TeacherPayoutLedgerSerializer,
    TeacherPayoutAccountSerializer,
    SessionExcuseSerializer,
    TeacherAuditionSerializer,
    SupportedBankSerializer,
)


def sync_elapsed_session_bookings(bookings):
    """
    Auto-syncs session bookings that have elapsed or occurred:
    1. Sets status to COMPLETED if CONFIRMED or IN_PROGRESS and end_at <= now or start_at <= now - 45min.
    2. Ensures SessionAttendance exists, evaluated, default VERIFIED_COMPLETE if past session.
    3. Ensures TeacherPayoutLedger exists with status PAYABLE (or updated from HELD to PAYABLE).
    This ensures that sessions taught previously are accurately reflected in attendance and payouts.
    """
    now = timezone.now()
    threshold = now - timedelta(minutes=45)
    for b in bookings:
        is_past = (b.end_at and b.end_at <= now) or (b.start_at and b.start_at <= threshold)
        if b.status in [SessionBooking.Status.CONFIRMED, SessionBooking.Status.IN_PROGRESS] and is_past:
            b.status = SessionBooking.Status.COMPLETED
            b.save(update_fields=["status", "updated_at"])

            attendance, _ = SessionAttendance.objects.get_or_create(booking=b)
            if attendance.verdict == SessionAttendance.Verdict.PENDING:
                attendance.verdict = SessionAttendance.Verdict.VERIFIED_COMPLETE
                attendance.teacher_minutes_present = max(attendance.teacher_minutes_present, 45)
                attendance.student_minutes_present = max(attendance.student_minutes_present, 45)
                attendance.save()

            gross = b.hourly_rate or (b.teacher.hourly_rate if b.teacher else Decimal("10.00")) or Decimal("10.00")
            if gross <= Decimal("0.00"):
                gross = Decimal("10.00")
            platform_fee = (gross * Decimal("10.00")) / Decimal("100.00")
            teacher_net = gross - platform_fee

            ledger, created = TeacherPayoutLedger.objects.get_or_create(
                booking=b,
                teacher=b.teacher,
                defaults={
                    "gross_amount": gross,
                    "platform_fee_percent": Decimal("10.00"),
                    "platform_fee_amount": platform_fee,
                    "teacher_net_amount": teacher_net,
                    "status": TeacherPayoutLedger.Status.PAYABLE,
                },
            )
            if not created and ledger.status == TeacherPayoutLedger.Status.HELD:
                ledger.status = TeacherPayoutLedger.Status.PAYABLE
                ledger.save(update_fields=["status", "updated_at"])
        elif b.status == SessionBooking.Status.COMPLETED or is_past:
            gross = b.hourly_rate or (b.teacher.hourly_rate if b.teacher else Decimal("10.00")) or Decimal("10.00")
            if gross <= Decimal("0.00"):
                gross = Decimal("10.00")
            platform_fee = (gross * Decimal("10.00")) / Decimal("100.00")
            teacher_net = gross - platform_fee

            ledger, created = TeacherPayoutLedger.objects.get_or_create(
                booking=b,
                teacher=b.teacher,
                defaults={
                    "gross_amount": gross,
                    "platform_fee_percent": Decimal("10.00"),
                    "platform_fee_amount": platform_fee,
                    "teacher_net_amount": teacher_net,
                    "status": TeacherPayoutLedger.Status.PAYABLE,
                },
            )
            if not created and ledger.status == TeacherPayoutLedger.Status.HELD:
                ledger.status = TeacherPayoutLedger.Status.PAYABLE
                ledger.save(update_fields=["status", "updated_at"])


class UserViewSet(RetrieveModelMixin, ListModelMixin, UpdateModelMixin, GenericViewSet):
    """ViewSet for User operations."""

    serializer_class = UserSerializer
    queryset = User.objects.all()
    lookup_field = "username"
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Filter queryset to only show current user's data."""
        if self.request.user.is_authenticated:
            return self.queryset.filter(id=self.request.user.id)
        return self.queryset.none()

    @action(detail=False, methods=["get"])
    def me(self, request):
        """Get current authenticated user's data."""
        serializer = UserSerializer(request.user, context={"request": request})
        return Response(status=status.HTTP_200_OK, data=serializer.data)

    @action(detail=False, methods=["get"])
    def profile(self, request):
        """Get current user's profile (Teacher or Student)."""
        user = request.user

        # Check if user is a teacher
        if hasattr(user, "teacher_profile"):
            serializer = TeacherProfileSerializer(
                user.teacher_profile, context={"request": request}
            )
            return Response({"type": "teacher", "profile": serializer.data})

        # Check if user is a student
        if hasattr(user, "student_profile"):
            serializer = StudentProfileSerializer(
                user.student_profile, context={"request": request}
            )
            return Response({"type": "student", "profile": serializer.data})

        return Response(
            {"detail": "No profile found. Please complete registration."},
            status=status.HTTP_404_NOT_FOUND,
        )

    @action(detail=False, methods=["get"], url_path="student-analytics")
    def student_analytics(self, request):
        """
        Calculated attendance metrics and spending insights for the requesting student.
        Aggregates single and package bookings across multiple teachers.
        """
        user = request.user
        if not hasattr(user, "student_profile"):
            return Response({"detail": "Student profile required."}, status=status.HTTP_400_BAD_REQUEST)

        student = user.student_profile
        now = timezone.now()
        bookings = list(SessionBooking.objects.filter(student=student).select_related(
            "teacher", "teacher__user", "order", "attendance"
        ))

        # Auto-sync elapsed bookings
        sync_elapsed_session_bookings(bookings)

        completed_verdicts = [
            SessionAttendance.Verdict.VERIFIED_COMPLETE,
            SessionAttendance.Verdict.STUDENT_ABSENT,
        ]

        threshold = now - timedelta(minutes=45)
        attended_bookings = [
            b for b in bookings
            if b.status == SessionBooking.Status.COMPLETED
            or (b.end_at and b.end_at <= now)
            or (b.start_at and b.start_at <= threshold)
            or (hasattr(b, "attendance") and b.attendance and b.attendance.verdict in completed_verdicts)
            or (hasattr(b, "attendance") and b.attendance and (b.attendance.teacher_heartbeat_count > 0 or b.attendance.student_heartbeat_count > 0))
        ]

        remaining_bookings = [
            b for b in bookings
            if b not in attended_bookings
            and b.status in [
                SessionBooking.Status.CONFIRMED,
                SessionBooking.Status.RESERVED,
                SessionBooking.Status.REQUESTED,
                SessionBooking.Status.APPROVED,
            ]
            and (b.end_at is None or b.end_at > now)
        ]

        # Calculate distinct attended days and remaining days
        attended_dates = {b.start_at.date().isoformat() for b in attended_bookings if b.start_at}
        remaining_dates = {b.start_at.date().isoformat() for b in remaining_bookings if b.start_at}

        # Calculate student spending:
        paid_orders = BookingOrder.objects.filter(
            student=student, status=BookingOrder.Status.PAID
        )
        total_order_spent = sum((o.total_amount for o in paid_orders), Decimal("0.00"))

        standalone_bookings = [
            b for b in bookings
            if not b.order_id and b.status in [
                SessionBooking.Status.CONFIRMED,
                SessionBooking.Status.COMPLETED,
                SessionBooking.Status.IN_PROGRESS,
            ]
        ]
        standalone_spent = Decimal("0.00")
        for b in standalone_bookings:
            duration_hours = Decimal("1.00")
            if b.start_at and b.end_at:
                duration_hours = max(Decimal(str((b.end_at - b.start_at).total_seconds() / 3600.0)), Decimal("0.50"))
            rate = b.hourly_rate or (b.teacher.hourly_rate if b.teacher else Decimal("10.00")) or Decimal("10.00")
            standalone_spent += rate * duration_hours

        total_spent = total_order_spent + standalone_spent

        # Group by Teacher breakdown
        teacher_meta: dict[str, dict[str, str]] = {}
        teacher_attended: dict[str, int] = {}
        teacher_remaining: dict[str, int] = {}
        teacher_spent: dict[str, Decimal] = {}

        for b in bookings:
            if not b.teacher:
                continue
            tid = str(b.teacher.id)
            if tid not in teacher_meta:
                t_user = b.teacher.user
                avatar_url = ""
                if t_user.avatar:
                    try:
                        avatar_url = t_user.avatar.url
                    except Exception:
                        avatar_url = ""
                teacher_meta[tid] = {
                    "teacher_id": tid,
                    "teacher_name": t_user.full_name or t_user.username,
                    "avatar_url": avatar_url,
                }
                teacher_attended[tid] = 0
                teacher_remaining[tid] = 0
                teacher_spent[tid] = Decimal("0.00")

            if b in attended_bookings:
                teacher_attended[tid] += 1
            elif b in remaining_bookings:
                teacher_remaining[tid] += 1

        for order in paid_orders:
            if order.teacher_id:
                tid = str(order.teacher_id)
                if tid in teacher_spent:
                    teacher_spent[tid] += order.total_amount

        teachers_breakdown = [
            {
                "teacher_id": tid,
                "teacher_name": meta["teacher_name"],
                "avatar_url": meta["avatar_url"],
                "sessions_attended": teacher_attended.get(tid, 0),
                "sessions_remaining": teacher_remaining.get(tid, 0),
                "total_spent": str(teacher_spent.get(tid, Decimal("0.00")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            }
            for tid, meta in teacher_meta.items()
        ]

        total_finished = len(attended_bookings)
        missed = len([b for b in bookings if hasattr(b, "attendance") and b.attendance and b.attendance.verdict == SessionAttendance.Verdict.STUDENT_ABSENT])
        total_eval = total_finished + missed
        attendance_rate = round((total_finished / total_eval * 100), 1) if total_eval > 0 else 100.0

        # Trajectory (last 6 months spent and attended)
        trajectory = []
        for i in range(5, -1, -1):
            m_date = now - timedelta(days=i * 30)
            m_label = m_date.strftime("%b")
            m_year = m_date.year
            m_month = m_date.month
            m_attended = sum(1 for b in attended_bookings if b.start_at and b.start_at.year == m_year and b.start_at.month == m_month)
            m_spent = sum((o.total_amount for o in paid_orders if o.created_at.year == m_year and o.created_at.month == m_month), Decimal("0.00"))
            trajectory.append({
                "period": m_label,
                "sessions_attended": m_attended,
                "amount_spent": float(m_spent),
            })

        return Response({
            "total_sessions_attended": len(attended_bookings),
            "total_sessions_remaining": len(remaining_bookings),
            "days_attended": len(attended_dates),
            "days_remaining": len(remaining_dates),
            "total_spent": str(total_spent.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "attendance_rate": attendance_rate,
            "teachers_breakdown": teachers_breakdown,
            "trajectory": trajectory,
        }, status=status.HTTP_200_OK)


class TeacherProfileViewSet(
    RetrieveModelMixin, ListModelMixin, UpdateModelMixin, GenericViewSet
):
    """ViewSet for TeacherProfile operations."""

    serializer_class = TeacherProfileSerializer
    queryset = TeacherProfile.objects.filter(profile_visibility=True)
    lookup_field = "id"

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_queryset(self):
        """Show all visible teacher profiles for browsing."""
        queryset = TeacherProfile.objects.filter(profile_visibility=True).select_related("user")

        # Filter by specialization
        specialization = self.request.query_params.get("specialization")
        if specialization:
            queryset = queryset.filter(specialization=specialization)

        # Filter by verification status
        verified = self.request.query_params.get("verified")
        if verified == "true":
            queryset = queryset.filter(verification_status="VERIFIED")

        return queryset

    @action(detail=False, methods=["get"], url_path="my-analytics")
    def my_analytics(self, request):
        """
        Comprehensive calculated attendance, financial escrow breakdown & trajectory for Ustaz.
        """
        user = request.user
        if not hasattr(user, "teacher_profile"):
            return Response({"detail": "Teacher profile required."}, status=status.HTTP_400_BAD_REQUEST)

        teacher = user.teacher_profile
        now = timezone.now()
        bookings = list(SessionBooking.objects.filter(teacher=teacher).select_related(
            "student", "student__user", "order", "attendance"
        ))

        # Auto-sync elapsed bookings
        sync_elapsed_session_bookings(bookings)

        completed_verdicts = [
            SessionAttendance.Verdict.VERIFIED_COMPLETE,
            SessionAttendance.Verdict.STUDENT_ABSENT,
        ]

        threshold = now - timedelta(minutes=45)
        completed_bookings = [
            b for b in bookings
            if b.status == SessionBooking.Status.COMPLETED
            or (b.end_at and b.end_at <= now)
            or (b.start_at and b.start_at <= threshold)
            or (hasattr(b, "attendance") and b.attendance and b.attendance.verdict in completed_verdicts)
            or (hasattr(b, "attendance") and b.attendance and (b.attendance.teacher_heartbeat_count > 0 or b.attendance.student_heartbeat_count > 0))
        ]

        remaining_bookings = [
            b for b in bookings
            if b not in completed_bookings
            and b.status in [
                SessionBooking.Status.CONFIRMED,
                SessionBooking.Status.RESERVED,
                SessionBooking.Status.REQUESTED,
                SessionBooking.Status.APPROVED,
            ]
            and (b.end_at is None or b.end_at > now)
        ]

        # Distinct student count & student breakdown
        student_meta: dict[str, dict[str, str]] = {}
        student_attended: dict[str, int] = {}
        student_remaining: dict[str, int] = {}
        student_earned: dict[str, Decimal] = {}

        for b in bookings:
            if not b.student:
                continue
            sid = str(b.student.id)
            if sid not in student_meta:
                s_user = b.student.user
                avatar_url = ""
                if s_user.avatar:
                    try:
                        avatar_url = s_user.avatar.url
                    except Exception:
                        avatar_url = ""
                student_meta[sid] = {
                    "student_id": sid,
                    "student_name": s_user.full_name or s_user.username,
                    "student_email": s_user.email,
                    "avatar_url": avatar_url,
                }
                student_attended[sid] = 0
                student_remaining[sid] = 0
                student_earned[sid] = Decimal("0.00")

            if b in completed_bookings:
                student_attended[sid] += 1
            elif b in remaining_bookings:
                student_remaining[sid] += 1

        # Financial breakdown from TeacherPayoutLedger
        ledgers = TeacherPayoutLedger.objects.filter(teacher=teacher).select_related("booking")
        gross_earnings = sum((l.gross_amount for l in ledgers), Decimal("0.00"))
        platform_fee_total = sum((l.platform_fee_amount for l in ledgers), Decimal("0.00"))
        net_earnings = sum((l.teacher_net_amount for l in ledgers), Decimal("0.00"))

        escrow_held = sum((l.teacher_net_amount for l in ledgers if l.status == TeacherPayoutLedger.Status.HELD), Decimal("0.00"))
        escrow_payable = sum((l.teacher_net_amount for l in ledgers if l.status == TeacherPayoutLedger.Status.PAYABLE), Decimal("0.00"))
        escrow_settled = sum((l.teacher_net_amount for l in ledgers if l.status == TeacherPayoutLedger.Status.SETTLED), Decimal("0.00"))

        # Add earnings per student
        for l in ledgers:
            if l.booking and l.booking.student_id:
                sid = str(l.booking.student_id)
                if sid in student_earned:
                    student_earned[sid] += l.teacher_net_amount

        # Fallback if ledgers were not yet created for completed sessions
        for b in completed_bookings:
            if b.student:
                sid = str(b.student.id)
                rate = b.hourly_rate or (teacher.hourly_rate if teacher else Decimal("10.00")) or Decimal("10.00")
                net_rate = rate * Decimal("0.90")
                if sid in student_earned and student_earned[sid] == Decimal("0.00"):
                    student_earned[sid] += net_rate

        if gross_earnings == Decimal("0.00") and completed_bookings:
            for b in completed_bookings:
                rate = b.hourly_rate or (teacher.hourly_rate if teacher else Decimal("10.00")) or Decimal("10.00")
                gross_earnings += rate
            platform_fee_total = (gross_earnings * Decimal("10.00")) / Decimal("100.00")
            net_earnings = gross_earnings - platform_fee_total
            if escrow_payable == Decimal("0.00") and escrow_settled == Decimal("0.00"):
                escrow_payable = net_earnings

        students_breakdown = [
            {
                "student_id": sid,
                "student_name": meta["student_name"],
                "student_email": meta["student_email"],
                "avatar_url": meta["avatar_url"],
                "sessions_attended": student_attended.get(sid, 0),
                "sessions_remaining": student_remaining.get(sid, 0),
                "total_earned": str(student_earned.get(sid, Decimal("0.00")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            }
            for sid, meta in student_meta.items()
        ]

        # Trajectory (last 6 months net earnings and completed classes)
        trajectory = []
        for i in range(5, -1, -1):
            m_date = now - timedelta(days=i * 30)
            m_label = m_date.strftime("%b")
            m_year = m_date.year
            m_month = m_date.month
            m_sessions = sum(1 for b in completed_bookings if b.start_at and b.start_at.year == m_year and b.start_at.month == m_month)
            m_net = sum((l.teacher_net_amount for l in ledgers if l.created_at.year == m_year and l.created_at.month == m_month), Decimal("0.00"))
            trajectory.append({
                "period": m_label,
                "sessions_completed": m_sessions,
                "amount": float(m_net),
            })

        # Payout account
        payout_account = {
            "payout_bank_name": teacher.payout_bank_name,
            "payout_account_number": teacher.payout_account_number,
            "payout_account_holder": teacher.payout_account_holder,
            "payout_phone": teacher.payout_phone,
            "is_configured": bool(teacher.payout_account_number and teacher.payout_bank_name),
        }

        return Response({
            "total_sessions_taught": len(completed_bookings),
            "total_sessions_remaining": len(remaining_bookings),
            "total_students_count": len(student_meta),
            "gross_earnings": str(gross_earnings.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "platform_fee_total": str(platform_fee_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "net_earnings": str(net_earnings.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "escrow_held": str(escrow_held.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "escrow_payable": str(escrow_payable.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "escrow_settled": str(escrow_settled.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "students_breakdown": students_breakdown,
            "trajectory": trajectory,
            "payout_account": payout_account,
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get", "patch", "put"], url_path="payout-account")
    def payout_account(self, request):
        user = request.user
        if not hasattr(user, "teacher_profile"):
            return Response({"detail": "Teacher profile required."}, status=status.HTTP_400_BAD_REQUEST)

        teacher = user.teacher_profile
        if request.method == "GET":
            return Response({
                "payout_bank_name": teacher.payout_bank_name,
                "payout_account_number": teacher.payout_account_number,
                "payout_account_holder": teacher.payout_account_holder,
                "payout_phone": teacher.payout_phone,
                "is_configured": bool(teacher.payout_account_number and teacher.payout_bank_name),
            })

        serializer = TeacherPayoutAccountSerializer(teacher, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({
            **serializer.data,
            "is_configured": bool(teacher.payout_account_number and teacher.payout_bank_name),
            "detail": "Payout account details saved successfully.",
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="my-payouts")
    def my_payouts(self, request):
        user = request.user
        if not hasattr(user, "teacher_profile"):
            return Response({"detail": "Teacher profile required."}, status=status.HTTP_400_BAD_REQUEST)

        teacher = user.teacher_profile
        qs = TeacherPayoutLedger.objects.filter(teacher=teacher).select_related(
            "booking", "teacher", "teacher__user", "disbursed_by"
        ).order_by("-created_at")

        serializer = TeacherPayoutLedgerSerializer(qs, many=True, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class TeacherAvailabilityViewSet(
    CreateModelMixin,
    ListModelMixin,
    UpdateModelMixin,
    DestroyModelMixin,
    GenericViewSet,
):
    """CRUD availability for current teacher; list public availability by teacher."""

    serializer_class = TeacherAvailabilitySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Teachers see their own; public list can be filtered by teacher id
        teacher_profile = getattr(self.request.user, "teacher_profile", None)
        qs = TeacherAvailability.objects.all()
        teacher_id = self.request.query_params.get("teacher_id")
        if teacher_id:
            return qs.filter(teacher_id=teacher_id, is_active=True)
        if teacher_profile:
            return qs.filter(teacher=teacher_profile)
        return qs.none()

    @action(detail=False, methods=["get"], url_path="slots")
    def slots(self, request):
        """Generate upcoming slots for a given teacher based on availability and exclude booked times."""
        teacher_id = request.query_params.get("teacher_id")
        days = int(request.query_params.get("days", "14"))
        slot_minutes = int(request.query_params.get("slot_minutes", "60"))
        if not teacher_id:
            return Response({"detail": "teacher_id is required"}, status=400)

        teacher = TeacherProfile.objects.filter(id=teacher_id).first()
        if not teacher:
            return Response({"detail": "Teacher not found"}, status=404)

        # Collect availability blocks
        avails = TeacherAvailability.objects.filter(teacher=teacher, is_active=True)
        # Booked windows to exclude
        bookings = SessionBooking.objects.filter(
            teacher=teacher,
            status__in=[
                SessionBooking.Status.PENDING,
                SessionBooking.Status.REQUESTED,
                SessionBooking.Status.APPROVED,
                SessionBooking.Status.CONFIRMED,
            ],
        )

        now = datetime.utcnow()
        end_date = now + timedelta(days=days)
        slots = []

        for avail in avails:
            tz = ZoneInfo(avail.timezone or "UTC")
            # Iterate each day within window matching weekday
            cur = now
            while cur <= end_date:
                if cur.weekday() == avail.day_of_week:
                    # Build start/end for the day in teacher's timezone
                    day_start = datetime(
                        cur.year,
                        cur.month,
                        cur.day,
                        avail.start_time.hour,
                        avail.start_time.minute,
                        tzinfo=tz,
                    )
                    day_end = datetime(
                        cur.year,
                        cur.month,
                        cur.day,
                        avail.end_time.hour,
                        avail.end_time.minute,
                        tzinfo=tz,
                    )
                    # Slice into slot_minutes
                    slot_start = day_start
                    while slot_start + timedelta(minutes=slot_minutes) <= day_end:
                        slot_end = slot_start + timedelta(minutes=slot_minutes)
                        # Exclude overlaps with bookings
                        overlap = bookings.filter(
                            start_at__lt=slot_end, end_at__gt=slot_start
                        ).exists()
                        if not overlap and slot_start > now.replace(tzinfo=tz):
                            slots.append(
                                {
                                    "start_at": slot_start.isoformat(),
                                    "end_at": slot_end.isoformat(),
                                }
                            )
                        slot_start = slot_end
                cur += timedelta(days=1)

        return Response({"slots": sorted(slots, key=lambda s: s["start_at"])})


class SessionBookingViewSet(
    CreateModelMixin, RetrieveModelMixin, ListModelMixin, UpdateModelMixin, GenericViewSet
):
    serializer_class = SessionBookingSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        base_qs = SessionBooking.objects.select_related(
            "teacher", "teacher__user", "student", "student__user"
        )
        if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False) or (hasattr(user, "has_role") and user.has_role("ADMIN")):
            return base_qs.order_by("-start_at")
        if hasattr(user, "student_profile"):
            return base_qs.filter(student=user.student_profile).order_by("start_at")
        if hasattr(user, "teacher_profile"):
            return base_qs.filter(teacher=user.teacher_profile).order_by("start_at")
        return SessionBooking.objects.none()

    def perform_create(self, serializer):
        teacher = serializer.validated_data.get("teacher")
        # Lock in the teacher's current hourly rate
        hourly_rate = teacher.hourly_rate if teacher else 0.00
        serializer.save(
            student=self.request.user.student_profile, hourly_rate=hourly_rate
        )

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        booking = self.get_object()

        # Only the owning student can cancel
        if (
            not hasattr(request.user, "student_profile")
            or booking.student != request.user.student_profile
        ):
            return Response(
                {"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN
            )

        if booking.status == SessionBooking.Status.CANCELLED:
            return Response(
                {"detail": "Booking already cancelled."}, status=status.HTTP_200_OK
            )

        if booking.status == SessionBooking.Status.DECLINED:
            return Response(
                {"detail": "Booking already declined."}, status=status.HTTP_200_OK
            )

        if booking.start_at <= timezone.now():
            return Response(
                {"detail": "Cannot cancel a booking that has started or passed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        booking.status = SessionBooking.Status.CANCELLED
        booking.save(update_fields=["status", "updated_at"])
        
        # Free up the associated time slot
        if hasattr(booking, 'time_slot') and booking.time_slot:
            booking.time_slot.is_booked = False
            booking.time_slot.booking = None
            booking.time_slot.save(update_fields=["is_booked", "booking", "updated_at"])
            
        teacher_user = getattr(booking.teacher, "user", None)
        if teacher_user:
            Notification.objects.create(
                user=teacher_user,
                title="Booking cancelled",
                body=f"A booking on {booking.start_at} was cancelled by the student.",
                related_booking=booking,
            )
        serializer = self.get_serializer(booking)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="book-range")
    def book_range(self, request):
        """
        Creates a package of multiple recurring sessions attached to a BookingOrder from existing TimeSlots.
        """
        serializer = RangeBookingRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        time_slot_ids = serializer.validated_data["time_slot_ids"]
        end_date = serializer.validated_data.get("end_date")

        if not hasattr(request.user, "student_profile"):
            return Response(
                {"error": "Only students can book packages"},
                status=status.HTTP_403_FORBIDDEN,
            )

        student = request.user.student_profile
        
        with transaction.atomic():
            # First, fetch the base slots to determine patterns and teacher
            base_slots = TimeSlot.objects.filter(id__in=time_slot_ids)
            if not base_slots.exists():
                return Response(
                    {"error": "One or more time slots could not be found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
                
            teacher = base_slots.first().teacher
            
            # If end_date is provided, extrapolate the pattern. Otherwise just use the provided slots.
            if end_date:
                import datetime
                end_datetime = timezone.make_aware(datetime.datetime.combine(end_date, datetime.time.max))
                
                # Gather patterns: (day_of_week, start_time)
                patterns = []
                for s in base_slots:
                    start_local = timezone.localtime(s.start_time)
                    patterns.append((start_local.weekday(), start_local.time()))
                    
                # Find all unbooked slots for this teacher up to end_date that match the patterns
                all_teacher_slots = TimeSlot.objects.filter(
                    teacher=teacher,
                    is_booked=False,
                    booking__isnull=True,
                    start_time__lte=end_datetime,
                    start_time__gt=timezone.now(),
                    allowed_booking_type__in=[TimeSlot.BookingType.RANGE, TimeSlot.BookingType.BOTH]
                ).select_for_update()
                
                # Filter locally to match exact time/day (since TimeSlot might not have denormalized weekday/time fields easily filterable across timezones in ORM without complex extraction)
                target_slots = []
                for slot in all_teacher_slots:
                    start_local = timezone.localtime(slot.start_time)
                    if (start_local.weekday(), start_local.time()) in patterns:
                        target_slots.append(slot.id)
                        
                if not target_slots:
                     return Response(
                        {"error": "No available slots found for the requested pattern."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                slots = TimeSlot.objects.select_for_update().filter(id__in=target_slots)
            else:
                slots = TimeSlot.objects.select_for_update().filter(id__in=time_slot_ids)
                if len(slots) != len(set(time_slot_ids)):
                    return Response(
                        {"error": "One or more time slots could not be found."},
                        status=status.HTTP_404_NOT_FOUND,
                    )

            hourly_rate = teacher.hourly_rate or Decimal("10.00")
            if hourly_rate <= 0:
                hourly_rate = Decimal("10.00")

            total_hours = Decimal("0.00")
            potential_bookings = []
            
            for slot in slots:
                if slot.teacher != teacher:
                    return Response(
                        {"error": "All time slots must belong to the same teacher."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if slot.is_booked or slot.booking_id:
                    return Response(
                        {"error": f"Time slot on {slot.start_time} is no longer available."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if slot.start_time <= timezone.now():
                    return Response(
                        {"error": f"Time slot on {slot.start_time} has already passed."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if slot.allowed_booking_type == TimeSlot.BookingType.SINGLE:
                    return Response(
                        {"error": f"Time slot on {slot.start_time} only allows single bookings."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                
                duration = slot.end_time - slot.start_time
                hours = Decimal(duration.total_seconds() / 3600)
                total_hours += hours
                
                potential_bookings.append({
                    "slot": slot,
                    "booking": SessionBooking(
                        teacher=teacher,
                        student=student,
                        start_at=slot.start_time,
                        end_at=slot.end_time,
                        hourly_rate=hourly_rate,
                        status=SessionBooking.Status.REQUESTED,
                    )
                })
            
            if not potential_bookings:
                 return Response(
                    {"error": "No available slots found."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            total_amount = max(total_hours * hourly_rate, Decimal("1.00"))
            
            # Create Order - auto-approved so the student can pay immediately without unnecessary delay
            order = BookingOrder.objects.create(
                student=student,
                teacher=teacher,
                total_amount=total_amount,
                currency="usd",
                status=BookingOrder.Status.APPROVED,
            )
            
            # Save bookings and update slots
            for item in potential_bookings:
                booking = item["booking"]
                booking.order = order
                booking.status = SessionBooking.Status.APPROVED
                booking.save()
                
                slot = item["slot"]
                slot.is_booked = True
                slot.booking = booking
                slot.save(update_fields=["is_booked", "booking", "updated_at"])
                
        # Send notifications
        teacher_user = getattr(teacher, "user", None)
        if teacher_user:
            Notification.objects.create(
                user=teacher_user,
                title="New Package Booking (Auto-Approved)",
                body=f"A student booked a package of {len(potential_bookings)} sessions. Waiting for student payment.",
            )
        
        student_user = getattr(student, "user", None)
        if student_user:
            Notification.objects.create(
                user=student_user,
                title="Package Booked & Approved",
                body=f"Your package of {len(potential_bookings)} sessions with {teacher.user.full_name or teacher.user.username} is approved! Please complete payment before your first session begins.",
            )
        
        order_serializer = BookingOrderSerializer(order)
        return Response(order_serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="pending-approvals")
    def pending_approvals(self, request):
        """
        Returns pending single bookings and pending range bookings (orders) for the teacher.
        """
        teacher_profile = getattr(request.user, "teacher_profile", None)
        if not teacher_profile:
            return Response({"error": "Only teachers can view pending approvals."}, status=status.HTTP_403_FORBIDDEN)
            
        single_bookings = SessionBooking.objects.filter(
            teacher=teacher_profile,
            status=SessionBooking.Status.REQUESTED,
            order__isnull=True
        ).order_by("start_at")
        
        range_orders = BookingOrder.objects.filter(
            teacher=teacher_profile,
            status=BookingOrder.Status.REQUESTED
        ).order_by("created_at")
        
        return Response({
            "single_bookings": SessionBookingSerializer(single_bookings, many=True).data,
            "range_bookings": BookingOrderSerializer(range_orders, many=True).data,
        })
    @action(detail=False, methods=["post"], url_path="approve-package")
    def approve_package(self, request):
        order_id = request.data.get("order_id")
        if not order_id:
            return Response(
                {"error": "order_id is required"}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            order = BookingOrder.objects.get(id=order_id)
        except BookingOrder.DoesNotExist:
            return Response(
                {"error": "Order not found"}, status=status.HTTP_404_NOT_FOUND
            )

        teacher_profile = getattr(request.user, "teacher_profile", None)
        if not teacher_profile or order.teacher != teacher_profile:
            return Response(
                {"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN
            )

        if order.status != BookingOrder.Status.REQUESTED:
            return Response(
                {"detail": "Only requested packages can be approved."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()
        bookings_qs = order.bookings.all()
        total_sessions = bookings_qs.count()

        past_bookings = bookings_qs.filter(start_at__lt=now)
        future_bookings = bookings_qs.filter(start_at__gte=now)

        if not future_bookings.exists():
            return Response(
                {
                    "error": "Cannot approve this package because all scheduled sessions have already elapsed. Please ask the student to book upcoming dates."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            elapsed_count = past_bookings.count()
            future_count = future_bookings.count()

            if elapsed_count > 0:
                # 1. Release slots for elapsed/missed sessions
                TimeSlot.objects.filter(booking__in=past_bookings).update(
                    is_booked=False, booking=None
                )
                past_bookings.update(status=SessionBooking.Status.CANCELLED)

                # 2. Prorate total amount for remaining future sessions
                prorated = (order.total_amount * Decimal(future_count)) / Decimal(total_sessions)

                # 3. Round to the nearest 10th digit number (multiple of 10: e.g., 10, 20, 30...)
                rounded_10 = (prorated / Decimal("10")).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * Decimal("10")
                final_amount = max(rounded_10, Decimal("10.00"))

                order.total_amount = final_amount
                order.status = BookingOrder.Status.APPROVED
                order.save(update_fields=["total_amount", "status", "updated_at"])

                future_bookings.update(status=SessionBooking.Status.APPROVED)

                # Notify student with proration info
                student_user = getattr(order.student, "user", None)
                if student_user:
                    Notification.objects.create(
                        user=student_user,
                        title="Package Approved (Adjusted)",
                        body=f"Your package with {teacher_profile.user.full_name or teacher_profile.user.username} has been approved. {elapsed_count} elapsed session(s) were removed. Your updated prorated total is ${final_amount:.2f} for {future_count} session(s).",
                    )
            else:
                order.status = BookingOrder.Status.APPROVED
                order.save(update_fields=["status", "updated_at"])
                order.bookings.update(status=SessionBooking.Status.APPROVED)

                student_user = getattr(order.student, "user", None)
                if student_user:
                    Notification.objects.create(
                        user=student_user,
                        title="Package Approved",
                        body=f"Your package with {teacher_profile.user.full_name or teacher_profile.user.username} has been approved. Please proceed to payment.",
                    )

        return Response(BookingOrderSerializer(order).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        booking = self.get_object()
        teacher_profile = getattr(request.user, "teacher_profile", None)

        if not teacher_profile or booking.teacher != teacher_profile:
            return Response(
                {"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN
            )

        if booking.status in {
            SessionBooking.Status.CANCELLED,
            SessionBooking.Status.DECLINED,
            SessionBooking.Status.EXPIRED,
        }:
            return Response(
                {"detail": "Booking is not available for approval."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if booking.status == SessionBooking.Status.CONFIRMED:
            serializer = self.get_serializer(booking)
            return Response(serializer.data, status=status.HTTP_200_OK)

        if booking.status not in {
            SessionBooking.Status.REQUESTED,
            SessionBooking.Status.PENDING,
        }:
            return Response(
                {"detail": "Only requested bookings can be approved."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if booking.start_at < timezone.now():
            return Response(
                {
                    "error": "Cannot approve this booking because the scheduled session start time has already passed."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        booking.status = SessionBooking.Status.APPROVED
        booking.save(update_fields=["status", "updated_at"])

        student_user = getattr(booking.student, "user", None)
        if student_user:
            Notification.objects.create(
                user=student_user,
                title="Booking approved",
                body=f"Your booking on {booking.start_at} was approved.",
                related_booking=booking,
            )

        serializer = self.get_serializer(booking)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="confirm")
    def confirm(self, request, pk=None):
        # Backward-compatible alias; approvals are handled here.
        return self.approve(request, pk=pk)

    @action(detail=True, methods=["post"], url_path="decline")
    def decline(self, request, pk=None):
        booking = self.get_object()
        teacher_profile = getattr(request.user, "teacher_profile", None)

        if not teacher_profile or booking.teacher != teacher_profile:
            return Response(
                {"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN
            )

        if booking.status in {
            SessionBooking.Status.CANCELLED,
            SessionBooking.Status.DECLINED,
        }:
            return Response(
                {"detail": "Booking already closed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        booking.status = SessionBooking.Status.DECLINED
        booking.save(update_fields=["status", "updated_at"])
        
        # Free up the associated time slot
        if hasattr(booking, 'time_slot') and booking.time_slot:
            booking.time_slot.is_booked = False
            booking.time_slot.booking = None
            booking.time_slot.save(update_fields=["is_booked", "booking", "updated_at"])
            
        student_user = getattr(booking.student, "user", None)
        if student_user:
            Notification.objects.create(
                user=student_user,
                title="Booking declined",
                body=f"Your booking on {booking.start_at} was declined by the teacher.",
                related_booking=booking,
            )
        serializer = self.get_serializer(booking)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="start")
    def start(self, request, pk=None):
        booking = self.get_object()
        teacher_profile = getattr(request.user, "teacher_profile", None)
        is_admin = getattr(request.user, "is_staff", False) or getattr(request.user, "is_superuser", False) or (hasattr(request.user, "has_role") and request.user.has_role("ADMIN"))

        if not (teacher_profile and booking.teacher == teacher_profile) and not is_admin:
            return Response({"detail": "Only the assigned teacher or admin can start a session."}, status=status.HTTP_403_FORBIDDEN)

        if booking.status not in {SessionBooking.Status.CONFIRMED, SessionBooking.Status.IN_PROGRESS}:
            return Response(
                {"detail": "Only confirmed sessions can be started."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        booking.status = SessionBooking.Status.IN_PROGRESS
        booking.save(update_fields=["status", "updated_at"])

        serializer = self.get_serializer(booking)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="complete")
    def complete(self, request, pk=None):
        booking = self.get_object()
        teacher_profile = getattr(request.user, "teacher_profile", None)

        if not teacher_profile or booking.teacher != teacher_profile:
            return Response(
                {"detail": "Only the assigned teacher can mark the session as complete."},
                status=status.HTTP_403_FORBIDDEN
            )

        if booking.status not in {SessionBooking.Status.CONFIRMED, SessionBooking.Status.IN_PROGRESS}:
            return Response(
                {"detail": "Only confirmed or in-progress sessions can be completed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            booking.status = SessionBooking.Status.COMPLETED
            booking.save(update_fields=["status", "updated_at"])

            attendance, _ = SessionAttendance.objects.get_or_create(booking=booking)
            attendance.evaluate_attendance()

            gross = booking.hourly_rate or Decimal("10.00")
            if gross <= Decimal("0.00"):
                gross = Decimal("10.00")
            platform_fee = (gross * Decimal("10.00")) / Decimal("100.00")
            teacher_net = gross - platform_fee

            ledger, _ = TeacherPayoutLedger.objects.get_or_create(
                booking=booking,
                teacher=booking.teacher,
                defaults={
                    "gross_amount": gross,
                    "platform_fee_percent": Decimal("10.00"),
                    "platform_fee_amount": platform_fee,
                    "teacher_net_amount": teacher_net,
                    "status": TeacherPayoutLedger.Status.PAYABLE,
                },
            )
            if attendance.verdict == SessionAttendance.Verdict.VERIFIED_COMPLETE:
                ledger.status = TeacherPayoutLedger.Status.PAYABLE
                ledger.save(update_fields=["status", "updated_at"])

        serializer = self.get_serializer(booking)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="attendance/heartbeat")
    def attendance_heartbeat(self, request, pk=None):
        booking = self.get_object()
        user = request.user

        is_teacher = hasattr(user, "teacher_profile") and booking.teacher_id == user.teacher_profile.id
        is_student = hasattr(user, "student_profile") and booking.student_id == user.student_profile.id
        is_admin = getattr(user, "is_staff", False) or getattr(user, "is_superuser", False) or (hasattr(user, "has_role") and user.has_role("ADMIN"))

        if not (is_teacher or is_student or is_admin):
            return Response({"detail": "Not a participant in this booking."}, status=status.HTTP_403_FORBIDDEN)

        role_label = "TEACHER" if is_teacher else ("STUDENT" if is_student else "ADMIN")
        event_type = request.data.get("event_type", AttendanceHeartbeat.EventType.PING)
        is_mic_active = bool(request.data.get("is_mic_active", True))
        is_camera_active = bool(request.data.get("is_camera_active", True))
        other_participant_detected = bool(request.data.get("other_participant_detected", False))

        with transaction.atomic():
            attendance, _ = SessionAttendance.objects.select_for_update().get_or_create(booking=booking)
            now = timezone.now()

            if is_teacher:
                if not attendance.teacher_joined_at or event_type == AttendanceHeartbeat.EventType.JOIN:
                    attendance.teacher_joined_at = attendance.teacher_joined_at or now
                attendance.teacher_heartbeat_count += 1
                attendance.teacher_minutes_present = max(attendance.teacher_minutes_present + 3, 3)
                if event_type == AttendanceHeartbeat.EventType.LEAVE:
                    attendance.teacher_left_at = now
            elif is_student:
                if not attendance.student_joined_at or event_type == AttendanceHeartbeat.EventType.JOIN:
                    attendance.student_joined_at = attendance.student_joined_at or now
                attendance.student_heartbeat_count += 1
                attendance.student_minutes_present = max(attendance.student_minutes_present + 3, 3)
                if event_type == AttendanceHeartbeat.EventType.LEAVE:
                    attendance.student_left_at = now

            attendance.save()

            AttendanceHeartbeat.objects.create(
                attendance=attendance,
                user=user,
                role=role_label,
                event_type=event_type,
                is_mic_active=is_mic_active,
                is_camera_active=is_camera_active,
                other_participant_detected=other_participant_detected,
            )

            if event_type == AttendanceHeartbeat.EventType.LEAVE or now >= booking.end_at:
                attendance.evaluate_attendance()

            gross = booking.hourly_rate or Decimal("10.00")
            if gross <= Decimal("0.00"):
                gross = Decimal("10.00")
            platform_fee = (gross * Decimal("10.00")) / Decimal("100.00")
            teacher_net = gross - platform_fee

            ledger, _ = TeacherPayoutLedger.objects.get_or_create(
                booking=booking,
                teacher=booking.teacher,
                defaults={
                    "gross_amount": gross,
                    "platform_fee_percent": Decimal("10.00"),
                    "platform_fee_amount": platform_fee,
                    "teacher_net_amount": teacher_net,
                    "status": TeacherPayoutLedger.Status.HELD,
                },
            )
            if attendance.verdict == SessionAttendance.Verdict.VERIFIED_COMPLETE:
                ledger.status = TeacherPayoutLedger.Status.PAYABLE
                ledger.save(update_fields=["status", "updated_at"])
            elif attendance.verdict == SessionAttendance.Verdict.TEACHER_ABSENT:
                ledger.status = TeacherPayoutLedger.Status.REFUNDED
                ledger.save(update_fields=["status", "updated_at"])

        return Response(SessionAttendanceSerializer(attendance).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="attendance")
    def get_attendance(self, request, pk=None):
        booking = self.get_object()
        attendance, _ = SessionAttendance.objects.get_or_create(booking=booking)
        return Response(SessionAttendanceSerializer(attendance).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="submit-excuse")
    def submit_excuse(self, request, pk=None):
        booking = self.get_object()
        reason = request.data.get("reason", "").strip()
        explanation = request.data.get("explanation", "").strip()
        if not reason or not explanation:
            return Response(
                {"error": "Both reason and explanation are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        excuse = SessionExcuse.objects.create(
            booking=booking,
            submitted_by=request.user,
            reason=reason,
            explanation=explanation,
        )
        return Response(SessionExcuseSerializer(excuse).data, status=status.HTTP_201_CREATED)




class NotificationViewSet(ListModelMixin, UpdateModelMixin, GenericViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user).order_by(
            "-created_at"
        )

    @action(detail=True, methods=["post"], url_path="read")
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        if not notification.is_read:
            notification.is_read = True
            notification.save(update_fields=["is_read", "updated_at"])
        serializer = self.get_serializer(notification)
        return Response(serializer.data, status=status.HTTP_200_OK)


class TimeSlotViewSet(ListModelMixin, DestroyModelMixin, GenericViewSet):
    """
    Manage teacher time slots.

    - GET  /time-slots/              -> List own slots (teacher) or by teacher_id (student/public)
    - GET  /time-slots/?date=YYYY-MM -> Filter by month
    - POST /time-slots/bulk_create/  -> Generate slots from a pattern + date range
    - POST /time-slots/bulk_delete/  -> Delete unbooked slots in a date range
    - DELETE /time-slots/{id}/       -> Delete a single unbooked slot
    """

    serializer_class = TimeSlotSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        teacher_id = self.request.query_params.get("teacher_id")
        date_param = self.request.query_params.get("date")  # 'YYYY-MM'

        # Public query: student or anyone looking up a teacher's slots
        if teacher_id:
            # We return both booked and unbooked slots so the student UI can render 
            # busy times as disabled on the calendar.
            qs = TimeSlot.objects.filter(teacher_id=teacher_id)
            if date_param:
                try:
                    year, month = int(date_param[:4]), int(date_param[5:7])
                    qs = qs.filter(start_time__year=year, start_time__month=month)
                except (ValueError, IndexError):
                    pass
            return qs.filter(start_time__gt=timezone.now()).order_by("start_time")

        # Teacher: see all their own slots
        teacher_profile = getattr(self.request.user, "teacher_profile", None)
        if teacher_profile:
            qs = TimeSlot.objects.filter(teacher=teacher_profile)
            if date_param:
                try:
                    year, month = int(date_param[:4]), int(date_param[5:7])
                    qs = qs.filter(start_time__year=year, start_time__month=month)
                except (ValueError, IndexError):
                    pass
            return qs.order_by("start_time")

        return TimeSlot.objects.none()

    def destroy(self, request, *args, **kwargs):
        """Only allow deleting unbooked slots."""
        slot = self.get_object()
        teacher_profile = getattr(request.user, "teacher_profile", None)
        if not teacher_profile or slot.teacher != teacher_profile:
            return Response(
                {"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN
            )
        if slot.is_booked:
            return Response(
                {
                    "detail": "Cannot delete a slot that has already been booked by a student."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        slot.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["post"], url_path="bulk_create")
    def bulk_create(self, request):
        """
        Generate physical TimeSlot rows for a teacher from a weekly pattern over a date range.

        Example payload:
        {
            "duration_minutes": 60,
            "day_patterns": [
                {"day_of_week": 0, "selected_times": ["09:00", "14:30", "19:00"]},
                {"day_of_week": 2, "selected_times": ["10:00", "15:00"]},
                {"day_of_week": 4, "selected_times": ["09:00", "14:00"]}
            ],
            "start_date": "2026-07-01",
            "end_date": "2026-09-30",
            "skip_months": ["2026-08"],
            "overwrite": false
        }
        """
        teacher_profile = getattr(request.user, "teacher_profile", None)
        if not teacher_profile:
            return Response(
                {"detail": "Only teachers can manage availability."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = BulkSlotCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        duration = data["duration_minutes"]
        day_patterns = {
            p["day_of_week"]: p["selected_times"] for p in data["day_patterns"]
        }
        start_date = data["start_date"]
        end_date = data["end_date"]
        skip_months = set(data.get("skip_months", []))
        overwrite = data.get("overwrite", False)
        allowed_booking_type = data.get("allowed_booking_type", "BOTH")

        # Get teacher's timezone
        offset_minutes = data.get("timezone_offset_minutes")
        if offset_minutes is not None:
            teacher_tz = py_timezone(timedelta(minutes=offset_minutes))
        else:
            teacher_tz_str = (
                getattr(teacher_profile.user, "user_timezone", "UTC") or "UTC"
            )
            try:
                teacher_tz = ZoneInfo(teacher_tz_str)
            except Exception:
                teacher_tz = ZoneInfo("UTC")

        slots_to_create = []
        slots_to_delete_starts = []
        batch_id = (
            uuid.uuid4()
        )  # All slots from this distribution share the same batch_id

        cur_date = start_date
        while cur_date <= end_date:
            month_key = cur_date.strftime("%Y-%m")

            # Skip explicitly excluded months
            if month_key in skip_months:
                # Advance to the first day of the next month
                if cur_date.month == 12:
                    cur_date = cur_date.replace(year=cur_date.year + 1, month=1, day=1)
                else:
                    cur_date = cur_date.replace(month=cur_date.month + 1, day=1)
                continue

            weekday = cur_date.weekday()  # 0=Monday
            if weekday in day_patterns:
                for t in day_patterns[weekday]:
                    slot_start = datetime(
                        cur_date.year,
                        cur_date.month,
                        cur_date.day,
                        t.hour,
                        t.minute,
                        tzinfo=teacher_tz,
                    )
                    slot_end = slot_start + timedelta(minutes=duration)

                    # Don't create past slots
                    if slot_start <= datetime.now(tz=teacher_tz):
                        continue

                    if overwrite:
                        slots_to_delete_starts.append(slot_start)

                    slots_to_create.append(
                        TimeSlot(
                            teacher=teacher_profile,
                            start_time=slot_start,
                            end_time=slot_end,
                            duration_minutes=duration,
                            batch_id=batch_id,
                            batch_start_date=start_date,
                            batch_end_date=end_date,
                            allowed_booking_type=allowed_booking_type,
                        )
                    )

            cur_date += timedelta(days=1)

        created_count = 0
        skipped_count = 0

        with transaction.atomic():
            if overwrite and slots_to_delete_starts:
                TimeSlot.objects.filter(
                    teacher=teacher_profile,
                    start_time__in=slots_to_delete_starts,
                    is_booked=False,
                ).delete()

            for slot in slots_to_create:
                obj, created = TimeSlot.objects.get_or_create(
                    teacher=teacher_profile,
                    start_time=slot.start_time,
                    defaults={
                        "end_time": slot.end_time,
                        "duration_minutes": slot.duration_minutes,
                        "batch_id": slot.batch_id,
                        "batch_start_date": slot.batch_start_date,
                        "batch_end_date": slot.batch_end_date,
                        "allowed_booking_type": slot.allowed_booking_type,
                    },
                )
                if created:
                    created_count += 1
                else:
                    skipped_count += 1
                    # If slot already existed (and is unbooked), update its batch info to the new distribution
                    if not obj.is_booked:
                        obj.batch_id = slot.batch_id
                        obj.batch_start_date = slot.batch_start_date
                        obj.batch_end_date = slot.batch_end_date
                        obj.allowed_booking_type = slot.allowed_booking_type
                        obj.save(
                            update_fields=[
                                "batch_id",
                                "batch_start_date",
                                "batch_end_date",
                                "allowed_booking_type",
                            ]
                        )

        return Response(
            {
                "created": created_count,
                "skipped_duplicates": skipped_count,
                "message": f"Successfully generated {created_count} new slots.",
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["post"], url_path="bulk_delete")
    def bulk_delete(self, request):
        """Delete all unbooked slots in a given date range for the current teacher."""
        teacher_profile = getattr(request.user, "teacher_profile", None)
        if not teacher_profile:
            return Response(
                {"detail": "Only teachers can manage availability."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = BulkSlotDeleteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        start_dt = timezone.make_aware(
            datetime.combine(data["start_date"], datetime.min.time())
        )
        end_dt = timezone.make_aware(
            datetime.combine(data["end_date"], datetime.max.time())
        )

        deleted_count, _ = TimeSlot.objects.filter(
            teacher=teacher_profile,
            is_booked=False,
            start_time__gte=start_dt,
            start_time__lte=end_dt,
        ).delete()

        return Response(
            {
                "deleted": deleted_count,
                "message": f"Cleared {deleted_count} unbooked slots.",
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="delete_batch")
    def delete_batch(self, request):
        """
        Delete unbooked slots belonging to a specific batch.
        Optionally constrain deletion to a sub-range.

        Payload:
        {
            "batch_id": "<uuid>",
            "start_date": "2026-07-15",  // optional, defaults to batch start
            "end_date": "2026-08-20"       // optional, defaults to batch end
        }
        """
        teacher_profile = getattr(request.user, "teacher_profile", None)
        if not teacher_profile:
            return Response(
                {"detail": "Only teachers can manage availability."},
                status=status.HTTP_403_FORBIDDEN,
            )

        batch_id = request.data.get("batch_id")
        if not batch_id:
            return Response(
                {"detail": "batch_id is required."}, status=status.HTTP_400_BAD_REQUEST
            )

        # Optional date bounds
        start_date_str = request.data.get("start_date")
        end_date_str = request.data.get("end_date")

        qs = TimeSlot.objects.filter(
            teacher=teacher_profile,
            batch_id=batch_id,
            is_booked=False,
        )

        if start_date_str:
            try:
                sd = date.fromisoformat(start_date_str)
                qs = qs.filter(start_time__date__gte=sd)
            except ValueError:
                return Response(
                    {"detail": "Invalid start_date format."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if end_date_str:
            try:
                ed = date.fromisoformat(end_date_str)
                qs = qs.filter(start_time__date__lte=ed)
            except ValueError:
                return Response(
                    {"detail": "Invalid end_date format."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        days = request.data.get("days")
        times = request.data.get("times")

        if days or times:
            offset_minutes = request.data.get("timezone_offset_minutes")
            if offset_minutes is not None:
                teacher_tz = py_timezone(timedelta(minutes=int(offset_minutes)))
            else:
                _tz_str = getattr(teacher_profile.user, "user_timezone", "UTC") or "UTC"
                try:
                    teacher_tz = ZoneInfo(_tz_str)
                except Exception:
                    teacher_tz = ZoneInfo("UTC")
            slots_to_delete = []
            for slot in qs:
                local_dt = slot.start_time.astimezone(teacher_tz)
                slot_day = local_dt.isoweekday()
                slot_time = local_dt.strftime("%H:%M")

                day_match = True
                if days:
                    day_match = slot_day in days

                time_match = True
                if times:
                    time_match = slot_time in times

                if day_match and time_match:
                    slots_to_delete.append(slot.id)

            deleted_count, _ = TimeSlot.objects.filter(id__in=slots_to_delete).delete()
        else:
            deleted_count, _ = qs.delete()

        return Response(
            {
                "deleted": deleted_count,
                "message": f"Removed {deleted_count} unbooked slots from this batch.",
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="batch_info")
    def batch_info(self, request):
        """
        Get distinct days (1-7) and times ("HH:MM") in a specific batch.
        """
        teacher_profile = getattr(request.user, "teacher_profile", None)
        if not teacher_profile:
            return Response(
                {"detail": "Only teachers can manage availability."},
                status=status.HTTP_403_FORBIDDEN,
            )

        batch_id = request.query_params.get("batch_id")
        if not batch_id:
            return Response(
                {"detail": "batch_id is required."}, status=status.HTTP_400_BAD_REQUEST
            )

        qs = TimeSlot.objects.filter(
            teacher=teacher_profile,
            batch_id=batch_id,
            is_booked=False,
        )

        offset_minutes = request.query_params.get("timezone_offset_minutes")
        if offset_minutes is not None:
            teacher_tz = timezone.get_fixed_timezone(int(offset_minutes))
        else:
            teacher_tz_str = (
                getattr(teacher_profile.user, "user_timezone", "UTC") or "UTC"
            )
            try:
                teacher_tz = ZoneInfo(teacher_tz_str)
            except Exception:
                teacher_tz = ZoneInfo("UTC")

        days_set = set()
        times_set = set()

        for slot in qs:
            local_dt = slot.start_time.astimezone(teacher_tz)
            days_set.add(local_dt.isoweekday())
            times_set.add(local_dt.strftime("%H:%M"))

        return Response(
            {
                "days": sorted(list(days_set)),
                "times": sorted(list(times_set)),
            },
            status=status.HTTP_200_OK,
        )


def _is_admin_user(user):
    return bool(
        user
        and user.is_authenticated
        and (
            getattr(user, "is_staff", False)
            or getattr(user, "is_superuser", False)
            or (hasattr(user, "has_role") and user.has_role("ADMIN"))
        )
    )


class AdminTeacherAuditionViewSet(viewsets.ModelViewSet):
    """
    Platform Owner / Admin viewset for reviewing candidate teacher applications
    and conducting live recitation auditions before approving accounts.
    """

    serializer_class = TeacherAuditionSerializer
    permission_classes = [IsAuthenticated]
    queryset = TeacherProfile.objects.select_related("user").all().order_by("-created_at")

    def get_queryset(self):
        if not _is_admin_user(self.request.user):
            return TeacherProfile.objects.none()
        qs = super().get_queryset()
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(verification_status=status_param.upper())
        return qs

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        if not _is_admin_user(request.user):
            return Response({"detail": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)
        teacher = self.get_object()
        notes = request.data.get("audition_notes", "")
        recitation_score = request.data.get("recitation_score")

        teacher.verification_status = TeacherProfile.VerificationStatus.VERIFIED
        teacher.verified_by = request.user
        teacher.verified_at = timezone.now()
        if notes:
            teacher.audition_notes = notes
        if recitation_score is not None:
            try:
                teacher.recitation_score = int(recitation_score)
            except (ValueError, TypeError):
                pass
        teacher.save()

        # Send approval notification to teacher
        if teacher.user:
            Notification.objects.create(
                user=teacher.user,
                title="Audition Approved! Welcome to Risala",
                body="Masha'Allah! Your live recitation audition has been verified and approved by the Risala Academy Admin. You can now publish your schedule and teach students.",
            )

        return Response(self.get_serializer(teacher).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        if not _is_admin_user(request.user):
            return Response({"detail": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)
        teacher = self.get_object()
        notes = request.data.get("audition_notes", "")
        teacher.verification_status = TeacherProfile.VerificationStatus.REJECTED
        teacher.verified_by = request.user
        teacher.verified_at = timezone.now()
        if notes:
            teacher.audition_notes = notes
        teacher.save()

        if teacher.user:
            Notification.objects.create(
                user=teacher.user,
                title="Application Status Update",
                body=f"Your teacher application status has been reviewed. Notes: {notes or 'Requires further Tajweed mastery.'}",
            )

        return Response(self.get_serializer(teacher).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="score-audition")
    def score_audition(self, request, pk=None):
        if not _is_admin_user(request.user):
            return Response({"detail": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)
        teacher = self.get_object()
        notes = request.data.get("audition_notes")
        score = request.data.get("recitation_score")
        if notes is not None:
            teacher.audition_notes = notes
        if score is not None:
            try:
                teacher.recitation_score = int(score)
            except (ValueError, TypeError):
                pass
        teacher.save()
        return Response(self.get_serializer(teacher).data, status=status.HTTP_200_OK)


class AdminAttendanceMonitorViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Platform Owner / Admin viewset for auditing live classroom attendance,
    viewing presence metrics, and overriding automated verdicts.
    """

    serializer_class = SessionAttendanceSerializer
    permission_classes = [IsAuthenticated]
    queryset = SessionAttendance.objects.select_related(
        "booking",
        "booking__teacher",
        "booking__teacher__user",
        "booking__student",
        "booking__student__user",
    ).all().order_by("-created_at")

    def get_queryset(self):
        if not _is_admin_user(self.request.user):
            return SessionAttendance.objects.none()
        qs = super().get_queryset()
        verdict_param = self.request.query_params.get("verdict")
        if verdict_param:
            qs = qs.filter(verdict=verdict_param.upper())
        return qs

    @action(detail=True, methods=["post"], url_path="override-verdict")
    def override_verdict(self, request, pk=None):
        if not _is_admin_user(request.user):
            return Response({"detail": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)
        attendance = self.get_object()
        verdict = request.data.get("verdict")
        settlement_status = request.data.get("settlement_status")
        notes = request.data.get("admin_notes", "")

        if verdict and hasattr(SessionAttendance.Verdict, verdict):
            attendance.verdict = verdict
        if settlement_status and hasattr(SessionAttendance.SettlementStatus, settlement_status):
            attendance.settlement_status = settlement_status
        if notes:
            attendance.admin_notes = notes
        attendance.evaluated_at = timezone.now()
        attendance.save()

        # Update linked TeacherPayoutLedger
        ledger = getattr(attendance.booking, "payout_ledger", None)
        if ledger:
            if attendance.verdict == SessionAttendance.Verdict.VERIFIED_COMPLETE:
                ledger.status = TeacherPayoutLedger.Status.PAYABLE
            elif attendance.verdict == SessionAttendance.Verdict.TEACHER_ABSENT:
                ledger.status = TeacherPayoutLedger.Status.REFUNDED
            elif attendance.verdict == SessionAttendance.Verdict.EXCUSED:
                ledger.status = TeacherPayoutLedger.Status.CANCELLED
            ledger.save(update_fields=["status", "updated_at"])

        return Response(self.get_serializer(attendance).data, status=status.HTTP_200_OK)


class AdminEscrowLedgerViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Platform Owner / Admin financial ledger:
    - 10% platform fee
    - 90% teacher payouts
    - Escrow settlement
    """

    serializer_class = TeacherPayoutLedgerSerializer
    permission_classes = [IsAuthenticated]
    queryset = TeacherPayoutLedger.objects.select_related(
        "booking",
        "teacher",
        "teacher__user",
    ).all().order_by("-created_at")

    def get_queryset(self):
        if not _is_admin_user(self.request.user):
            return TeacherPayoutLedger.objects.none()
        qs = super().get_queryset()
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param.upper())
        return qs

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        if not _is_admin_user(request.user):
            return Response({"detail": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)
        qs = self.get_queryset()

        total_escrow_held = sum((item.gross_amount for item in qs.filter(status=TeacherPayoutLedger.Status.HELD)), Decimal("0.00"))
        total_commission = sum((item.platform_fee_amount for item in qs.filter(status__in=[TeacherPayoutLedger.Status.PAYABLE, TeacherPayoutLedger.Status.SETTLED])), Decimal("0.00"))
        total_payable = sum((item.teacher_net_amount for item in qs.filter(status=TeacherPayoutLedger.Status.PAYABLE)), Decimal("0.00"))
        total_settled = sum((item.teacher_net_amount for item in qs.filter(status=TeacherPayoutLedger.Status.SETTLED)), Decimal("0.00"))
        total_refunded = sum((item.gross_amount for item in qs.filter(status=TeacherPayoutLedger.Status.REFUNDED)), Decimal("0.00"))

        return Response(
            {
                "total_escrow_held": str(total_escrow_held),
                "total_commission_earned": str(total_commission),
                "total_payable_to_teachers": str(total_payable),
                "total_settled": str(total_settled),
                "total_refunded": str(total_refunded),
                "pending_disbursements_count": qs.filter(status=TeacherPayoutLedger.Status.PAYABLE).count(),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="mark-settled")
    def mark_settled(self, request, pk=None):
        if not _is_admin_user(request.user):
            return Response({"detail": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)
        ledger = self.get_object()
        ledger.status = TeacherPayoutLedger.Status.SETTLED
        ledger.disbursed_at = timezone.now()
        ledger.disbursed_by = request.user
        ledger.payout_reference = request.data.get("payout_reference", ledger.payout_reference or "")
        ledger.payout_method = request.data.get("payout_method", ledger.payout_method or "BANK_TRANSFER")
        ledger.notes = request.data.get("notes", ledger.notes)
        ledger.save()

        try:
            Notification.objects.create(
                recipient=ledger.teacher.user,
                title="Ustaz Payout Disbursed",
                message=f"Your net payout of {ledger.teacher_net_amount} for Session #{str(ledger.booking_id)[:8]} has been disbursed to your account (Ref: {ledger.payout_reference or 'Direct Transfer'}).",
                notification_type=Notification.NotificationType.PAYMENT,
            )
        except Exception:
            pass

        return Response(self.get_serializer(ledger).data, status=status.HTTP_200_OK)


class AdminExcuseViewSet(viewsets.ModelViewSet):
    """
    Platform Owner / Admin viewset for reviewing emergency student/teacher excuses.
    """

    serializer_class = SessionExcuseSerializer
    permission_classes = [IsAuthenticated]
    queryset = SessionExcuse.objects.select_related(
        "booking",
        "submitted_by",
        "reviewed_by",
    ).all().order_by("-created_at")

    def get_queryset(self):
        if not _is_admin_user(self.request.user):
            return SessionExcuse.objects.none()
        qs = super().get_queryset()
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param.upper())
        return qs

    @action(detail=True, methods=["post"], url_path="review")
    def review(self, request, pk=None):
        if not _is_admin_user(request.user):
            return Response({"detail": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)
        excuse = self.get_object()
        decision = request.data.get("status", "").upper()
        review_notes = request.data.get("review_notes", "")

        if decision not in {SessionExcuse.Status.APPROVED, SessionExcuse.Status.REJECTED}:
            return Response(
                {"error": "Status must be APPROVED or REJECTED."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        excuse.status = decision
        excuse.reviewed_by = request.user
        excuse.reviewed_at = timezone.now()
        excuse.review_notes = review_notes
        excuse.save()

        # If approved, update attendance and notification
        if decision == SessionExcuse.Status.APPROVED:
            attendance, _ = SessionAttendance.objects.get_or_create(booking=excuse.booking)
            attendance.verdict = SessionAttendance.Verdict.EXCUSED
            attendance.settlement_status = SessionAttendance.SettlementStatus.CREDITED_RESCHEDULE
            attendance.save()

            ledger = getattr(excuse.booking, "payout_ledger", None)
            if ledger:
                ledger.status = TeacherPayoutLedger.Status.CANCELLED
                ledger.save(update_fields=["status", "updated_at"])

            # Notify user who submitted excuse
            Notification.objects.create(
                user=excuse.submitted_by,
                title="Excuse Approved",
                body=f"Your excuse for session on {excuse.booking.start_at} was approved by Admin. You are credited for a reschedule.",
            )
        else:
            Notification.objects.create(
                user=excuse.submitted_by,
                title="Excuse Declined",
                body=f"Your excuse for session on {excuse.booking.start_at} was declined. Note: {review_notes}",
            )

        return Response(self.get_serializer(excuse).data, status=status.HTTP_200_OK)


class SupportedBankViewSet(viewsets.ModelViewSet):
    """
    CRUD ViewSet for Supported Banks and Payment Providers.
    Admin users have full write access (create, update, delete).
    All authenticated users can list active banks for payout account selection.
    """

    serializer_class = SupportedBankSerializer
    lookup_field = "id"

    def get_permissions(self):
        if self.action in ["list", "retrieve"]:
            return [IsAuthenticated()]
        return [IsAuthenticated()]

    def get_queryset(self):
        user = self.request.user
        is_admin = (
            getattr(user, "is_staff", False)
            or getattr(user, "is_superuser", False)
            or (hasattr(user, "has_role") and user.has_role("ADMIN"))
        )
        if is_admin:
            return SupportedBank.objects.all().order_by("display_order", "name")
        return SupportedBank.objects.filter(is_active=True).order_by("display_order", "name")

    def perform_create(self, serializer):
        user = self.request.user
        is_admin = (
            getattr(user, "is_staff", False)
            or getattr(user, "is_superuser", False)
            or (hasattr(user, "has_role") and user.has_role("ADMIN"))
        )
        if not is_admin:
            raise PermissionDenied("Only administrative staff can add supported bank types.")

        p_type = serializer.validated_data.get("provider_type")
        if p_type not in [
            SupportedBank.ProviderType.BANK,
            SupportedBank.ProviderType.MOBILE_WALLET,
            SupportedBank.ProviderType.OTHER,
        ]:
            raise ValidationError(
                f"Invalid provider type: {p_type}. Only official bank and mobile wallet types are supported."
            )

        serializer.save()

    def perform_update(self, serializer):
        user = self.request.user
        is_admin = (
            getattr(user, "is_staff", False)
            or getattr(user, "is_superuser", False)
            or (hasattr(user, "has_role") and user.has_role("ADMIN"))
        )
        if not is_admin:
            raise PermissionDenied("Only administrative staff can modify supported bank types.")
        serializer.save()

    def perform_destroy(self, instance):
        user = self.request.user
        is_admin = (
            getattr(user, "is_staff", False)
            or getattr(user, "is_superuser", False)
            or (hasattr(user, "has_role") and user.has_role("ADMIN"))
        )
        if not is_admin:
            raise PermissionDenied("Only administrative staff can delete supported bank types.")
        instance.delete()


