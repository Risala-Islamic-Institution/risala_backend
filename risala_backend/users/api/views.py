"""
API Views for User and Profile models.
"""

import uuid
from datetime import date, datetime, timedelta, timezone as py_timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.mixins import (CreateModelMixin, DestroyModelMixin,
                                   ListModelMixin, RetrieveModelMixin,
                                   UpdateModelMixin)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from risala_backend.users.models import (BookingOrder, Notification,
                                         SessionBooking, StudentProfile,
                                         TeacherAvailability, TeacherProfile,
                                         TimeSlot, User)

from .serializers import (BookingOrderSerializer, BookingPackageSerializer,
                          BulkSlotCreateSerializer, BulkSlotDeleteSerializer,
                          NotificationSerializer, RangeBookingRequestSerializer,
                          SessionBookingSerializer,
                          StudentProfileSerializer,
                          TeacherAvailabilitySerializer,
                          TeacherProfileSerializer, TimeSlotSerializer,
                          UserSerializer)


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


class TeacherProfileViewSet(
    RetrieveModelMixin, ListModelMixin, UpdateModelMixin, GenericViewSet
):
    """ViewSet for TeacherProfile operations."""

    serializer_class = TeacherProfileSerializer
    queryset = TeacherProfile.objects.filter(profile_visibility=True)
    lookup_field = "id"

    def get_queryset(self):
        """Show all visible teacher profiles for browsing."""
        queryset = TeacherProfile.objects.filter(profile_visibility=True)

        # Filter by specialization
        specialization = self.request.query_params.get("specialization")
        if specialization:
            queryset = queryset.filter(specialization=specialization)

        # Filter by verification status
        verified = self.request.query_params.get("verified")
        if verified == "true":
            queryset = queryset.filter(verification_status="VERIFIED")

        return queryset


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
    CreateModelMixin, ListModelMixin, UpdateModelMixin, GenericViewSet
):
    serializer_class = SessionBookingSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if hasattr(user, "student_profile"):
            return SessionBooking.objects.filter(student=user.student_profile).order_by(
                "start_at"
            )
        if hasattr(user, "teacher_profile"):
            return SessionBooking.objects.filter(teacher=user.teacher_profile).order_by(
                "start_at"
            )
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
            
            # Create Order
            order = BookingOrder.objects.create(
                student=student,
                teacher=teacher,
                total_amount=total_amount,
                currency="usd",
                status=BookingOrder.Status.REQUESTED,
            )
            
            # Save bookings and update slots
            for item in potential_bookings:
                booking = item["booking"]
                booking.order = order
                booking.save()
                
                slot = item["slot"]
                slot.is_booked = True
                slot.booking = booking
                slot.save(update_fields=["is_booked", "booking", "updated_at"])
                
        # Send notification to teacher
        teacher_user = getattr(teacher, "user", None)
        if teacher_user:
            Notification.objects.create(
                user=teacher_user,
                title="New Package Booking",
                body=f"A student requested a package of {len(potential_bookings)} sessions.",
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

        with transaction.atomic():
            order.status = BookingOrder.Status.APPROVED
            order.save()
            # Update all bookings to APPROVED (or RESERVED/PENDING waiting for payment)
            # Keeping them as REQUESTED or changing to APPROVED?
            # The flow is: Requested -> Approved -> Paid (Confirmed)
            # Let's set bookings to APPROVED so they are distinct from initial requests.
            order.bookings.update(status=SessionBooking.Status.APPROVED)

            # Notify student
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
        student_profile = getattr(request.user, "student_profile", None)

        if not teacher_profile and not student_profile:
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
            
        if teacher_profile and booking.teacher != teacher_profile:
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
            
        if student_profile and booking.student != student_profile:
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

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
            
            # TODO: Phase 11 Escrow Payout logic will be integrated here
            # For now, just mark the teacher's total earnings or let a separate signal/job handle it.

        serializer = self.get_serializer(booking)
        return Response(serializer.data, status=status.HTTP_200_OK)



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
                        cur_date += timedelta(days=1)
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
