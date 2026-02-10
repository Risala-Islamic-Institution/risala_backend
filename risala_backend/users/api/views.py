"""
API Views for User and Profile models.
"""
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin, UpdateModelMixin, CreateModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework import viewsets
from rest_framework.decorators import action
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from django.utils import timezone

from risala_backend.users.models import User, TeacherProfile, StudentProfile, TeacherAvailability, SessionBooking, Notification

from .serializers import (
    UserSerializer,
    TeacherProfileSerializer,
    StudentProfileSerializer,
    TeacherAvailabilitySerializer,
    SessionBookingSerializer,
    NotificationSerializer,
)


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
            serializer = TeacherProfileSerializer(user.teacher_profile, context={"request": request})
            return Response({"type": "teacher", "profile": serializer.data})
        
        # Check if user is a student
        if hasattr(user, "student_profile"):
            serializer = StudentProfileSerializer(user.student_profile, context={"request": request})
            return Response({"type": "student", "profile": serializer.data})
        
        return Response(
            {"detail": "No profile found. Please complete registration."},
            status=status.HTTP_404_NOT_FOUND
        )


class TeacherProfileViewSet(RetrieveModelMixin, ListModelMixin, UpdateModelMixin, GenericViewSet):
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


class TeacherAvailabilityViewSet(CreateModelMixin, ListModelMixin, UpdateModelMixin, GenericViewSet):
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
            status__in=[SessionBooking.Status.PENDING, SessionBooking.Status.CONFIRMED]
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
                    day_start = datetime(cur.year, cur.month, cur.day, avail.start_time.hour, avail.start_time.minute, tzinfo=tz)
                    day_end = datetime(cur.year, cur.month, cur.day, avail.end_time.hour, avail.end_time.minute, tzinfo=tz)
                    # Slice into slot_minutes
                    slot_start = day_start
                    while slot_start + timedelta(minutes=slot_minutes) <= day_end:
                        slot_end = slot_start + timedelta(minutes=slot_minutes)
                        # Exclude overlaps with bookings
                        overlap = bookings.filter(start_at__lt=slot_end, end_at__gt=slot_start).exists()
                        if not overlap and slot_start > now.replace(tzinfo=tz):
                            slots.append({
                                "start_at": slot_start.isoformat(),
                                "end_at": slot_end.isoformat(),
                            })
                        slot_start = slot_end
                cur += timedelta(days=1)

        return Response({"slots": sorted(slots, key=lambda s: s["start_at"])})


class SessionBookingViewSet(CreateModelMixin, ListModelMixin, UpdateModelMixin, GenericViewSet):
    serializer_class = SessionBookingSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if hasattr(user, "student_profile"):
            return SessionBooking.objects.filter(student=user.student_profile).order_by("start_at")
        if hasattr(user, "teacher_profile"):
            return SessionBooking.objects.filter(teacher=user.teacher_profile).order_by("start_at")
        return SessionBooking.objects.none()

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        booking = self.get_object()

        # Only the owning student can cancel
        if not hasattr(request.user, "student_profile") or booking.student != request.user.student_profile:
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        if booking.status == SessionBooking.Status.CANCELLED:
            return Response({"detail": "Booking already cancelled."}, status=status.HTTP_200_OK)

        if booking.start_at <= timezone.now():
            return Response({"detail": "Cannot cancel a booking that has started or passed."}, status=status.HTTP_400_BAD_REQUEST)

        booking.status = SessionBooking.Status.CANCELLED
        booking.save(update_fields=["status", "updated_at"])
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

    @action(detail=True, methods=["post"], url_path="confirm")
    def confirm(self, request, pk=None):
        booking = self.get_object()
        teacher_profile = getattr(request.user, "teacher_profile", None)

        if not teacher_profile or booking.teacher != teacher_profile:
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        if booking.status == SessionBooking.Status.CANCELLED:
            return Response({"detail": "Booking already cancelled."}, status=status.HTTP_400_BAD_REQUEST)

        if booking.status == SessionBooking.Status.CONFIRMED:
            serializer = self.get_serializer(booking)
            return Response(serializer.data, status=status.HTTP_200_OK)

        booking.status = SessionBooking.Status.CONFIRMED
        booking.save(update_fields=["status", "updated_at"])
        student_user = getattr(booking.student, "user", None)
        if student_user:
            Notification.objects.create(
                user=student_user,
                title="Booking confirmed",
                body=f"Your booking on {booking.start_at} was confirmed by the teacher.",
                related_booking=booking,
            )
        serializer = self.get_serializer(booking)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="decline")
    def decline(self, request, pk=None):
        booking = self.get_object()
        teacher_profile = getattr(request.user, "teacher_profile", None)

        if not teacher_profile or booking.teacher != teacher_profile:
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        if booking.status == SessionBooking.Status.CANCELLED:
            return Response({"detail": "Booking already cancelled."}, status=status.HTTP_400_BAD_REQUEST)

        booking.status = SessionBooking.Status.CANCELLED
        booking.save(update_fields=["status", "updated_at"])
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


class NotificationViewSet(ListModelMixin, UpdateModelMixin, GenericViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user).order_by("-created_at")

    @action(detail=True, methods=["post"], url_path="read")
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        if not notification.is_read:
            notification.is_read = True
            notification.save(update_fields=["is_read", "updated_at"])
        serializer = self.get_serializer(notification)
        return Response(serializer.data, status=status.HTTP_200_OK)
