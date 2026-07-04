"""
API Serializers for User and Profile models.
"""

import logging

from dj_rest_auth.registration.serializers import RegisterSerializer
from dj_rest_auth.serializers import LoginSerializer
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework import serializers as drf_serializers

from risala_backend.users.models import (BookingOrder, Notification, Role,
                                         SessionBooking, StudentProfile,
                                         TeacherAvailability, TeacherProfile,
                                         User, UserRole)


class RoleSerializer(serializers.ModelSerializer):
    """Serializer for Role model."""

    class Meta:
        model = Role
        fields = ["id", "name", "description"]


class UserSerializer(serializers.ModelSerializer):
    """Serializer for User model."""

    roles = RoleSerializer(many=True, read_only=True)
    primary_role = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "full_name",
            "phone_number",
            "gender",
            "date_of_birth",
            "country",
            "user_timezone",
            "preferred_language",
            "is_active",
            "roles",
            "primary_role",
            "created_at",
        ]
        read_only_fields = ["id", "is_active", "created_at"]

    def get_primary_role(self, obj):
        role = obj.get_primary_role()
        return role.name if role else None


class CustomRegisterSerializer(RegisterSerializer):
    """Registration serializer aligned to documented actors."""

    # Documented platform roles; external services are not user-facing.
    ROLE_CHOICES = [
        ("STUDENT", "Learner / Student"),
        ("USTAZ", "Instructor / Ustaz"),
        ("ADMIN", "System Administrator"),
        ("FINANCE", "Finance Administrator"),
        ("SUPPORT", "Support Staff"),
    ]

    PUBLIC_SIGNUP_ROLES = {"STUDENT", "USTAZ"}

    role = serializers.ChoiceField(choices=ROLE_CHOICES, default="STUDENT")
    full_name = serializers.CharField(max_length=255, required=False, allow_blank=True)

    def validate_role(self, value):
        # Only learners and instructors can self-register; staff roles are provisioned internally.
        if value not in self.PUBLIC_SIGNUP_ROLES:
            raise serializers.ValidationError(
                "Only learner (STUDENT) and instructor (USTAZ) self-registration is allowed."
            )
        return value

    def get_cleaned_data(self):
        data = super().get_cleaned_data()
        data["role"] = self.validated_data.get("role", "STUDENT")
        data["full_name"] = self.validated_data.get("full_name", "")
        return data

    def validate(self, attrs):
        """Wrap parent validation to log incoming data on failure for debugging."""
        try:
            return super().validate(attrs)
        except serializers.ValidationError as exc:
            logger = logging.getLogger(__name__)
            try:
                incoming = getattr(self, "initial_data", None)
            except Exception:
                incoming = None
            logger.warning(
                "Registration validation failed. incoming=%s attrs=%s errors=%s",
                incoming,
                attrs,
                getattr(exc, "detail", str(exc)),
            )
            raise

    def custom_signup(self, request, user):
        """Assign role and create the appropriate profile after registration."""
        role_name = self.validated_data.get("role", "STUDENT")
        full_name = self.validated_data.get("full_name", "")

        if full_name:
            user.full_name = full_name
            user.save(update_fields=["full_name"])

        role, _ = Role.objects.get_or_create(
            name=role_name,
            defaults={"description": f"{role_name} role"},
        )
        UserRole.objects.get_or_create(user=user, role=role)

        # Create role-specific profile per the documented model
        if role_name == "STUDENT":
            StudentProfile.objects.get_or_create(user=user)
        elif role_name == "USTAZ":
            TeacherProfile.objects.get_or_create(user=user)


class TeacherProfileSerializer(serializers.ModelSerializer):
    """Serializer for TeacherProfile."""

    user = UserSerializer(read_only=True)

    class Meta:
        model = TeacherProfile
        fields = [
            "id",
            "user",
            "biography",
            "qualifications",
            "years_of_experience",
            "teaching_languages",
            "teaching_level",
            "specialization",
            "hourly_rate",
            "rating_average",
            "total_students",
            "verification_status",
            "profile_visibility",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "rating_average",
            "total_students",
            "verification_status",
            "created_at",
        ]


class StudentProfileSerializer(serializers.ModelSerializer):
    """Serializer for StudentProfile."""

    user = UserSerializer(read_only=True)

    class Meta:
        model = StudentProfile
        fields = [
            "id",
            "user",
            "learning_goals",
            "current_level",
            "preferred_schedule",
            "guardian_contact",
            "enrollment_status",
            "joined_at",
        ]
        read_only_fields = ["id", "enrollment_status", "joined_at"]


class CustomLoginSerializer(LoginSerializer):
    """Allow login with either email or username and password."""

    # Align with allauth/dj-rest-auth expectations
    username_field = "username"

    # Accept both fields; only one is required by validation logic.
    email = drf_serializers.EmailField(required=False, allow_blank=True)

    def validate(self, attrs):
        # If email provided, map to username field for allauth compatibility when using email auth.
        email = attrs.get("email")
        username = attrs.get(self.username_field)
        if email and not username:
            attrs[self.username_field] = email
        return super().validate(attrs)


class TeacherAvailabilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = TeacherAvailability
        fields = [
            "id",
            "teacher",
            "day_of_week",
            "start_time",
            "end_time",
            "timezone",
            "is_active",
            "created_at",
        ]
        read_only_fields = ["id", "teacher", "created_at"]

    def validate(self, attrs):
        request = self.context.get("request")
        teacher_profile = (
            getattr(request.user, "teacher_profile", None) if request else None
        )

        # Derive teacher and current values for both create and update flows
        teacher = self.instance.teacher if self.instance else teacher_profile
        day_of_week = attrs.get(
            "day_of_week", getattr(self.instance, "day_of_week", None)
        )
        start_time = attrs.get("start_time", getattr(self.instance, "start_time", None))
        end_time = attrs.get("end_time", getattr(self.instance, "end_time", None))
        timezone = attrs.get("timezone", getattr(self.instance, "timezone", "UTC"))

        if not teacher:
            raise serializers.ValidationError("Only teachers can create availability.")

        if start_time and end_time and end_time <= start_time:
            raise serializers.ValidationError("end_time must be after start_time.")

        if day_of_week is not None and start_time and end_time:
            duplicate_qs = TeacherAvailability.objects.filter(
                teacher=teacher,
                day_of_week=day_of_week,
                start_time=start_time,
                end_time=end_time,
                timezone=timezone,
            )
            if self.instance:
                duplicate_qs = duplicate_qs.exclude(id=self.instance.id)
            if duplicate_qs.exists():
                raise serializers.ValidationError(
                    "This availability block already exists for the selected day/time/timezone."
                )

        # Preserve teacher for create flow
        attrs["teacher"] = teacher
        return attrs

    def create(self, validated_data):
        try:
            return super().create(validated_data)
        except IntegrityError:
            raise serializers.ValidationError(
                "This availability block already exists for the selected day/time/timezone."
            )


class SessionBookingSerializer(serializers.ModelSerializer):
    teacher_name = serializers.SerializerMethodField()
    student_name = serializers.SerializerMethodField()
    time_slot_id = serializers.UUIDField(write_only=True, required=False)

    class Meta:
        model = SessionBooking
        fields = [
            "id",
            "teacher",
            "student",
            "start_at",
            "end_at",
            "status",
            "hourly_rate",
            "created_at",
            "teacher_name",
            "student_name",
            "order",
            "time_slot_id",
            "jitsi_room_url",
        ]
        read_only_fields = [
            "id",
            "student",
            "status",
            "created_at",
            "teacher",
            "start_at",
            "end_at",
            "jitsi_room_url",
        ]

    def get_teacher_name(self, obj):
        teacher_user = getattr(obj.teacher, "user", None)
        return teacher_user.full_name or teacher_user.username if teacher_user else None

    def get_student_name(self, obj):
        student_user = getattr(obj.student, "user", None)
        return student_user.full_name or student_user.username if student_user else None

    def validate(self, attrs):
        # Allow updates to bypass this if they aren't changing the time slot
        if not self.instance:
            time_slot_id = attrs.get("time_slot_id")
            if not time_slot_id:
                raise serializers.ValidationError({"time_slot_id": "This field is required for new bookings."})
            
            try:
                # We do NOT use select_for_update here because it's a read-only validation. 
                # The actual lock happens in the view's perform_create.
                time_slot = TimeSlot.objects.get(id=time_slot_id)
            except TimeSlot.DoesNotExist:
                raise serializers.ValidationError({"time_slot_id": "Time slot not found."})

            if time_slot.is_booked or time_slot.booking_id:
                raise serializers.ValidationError("This time slot is already booked.")
            
            if time_slot.allowed_booking_type == TimeSlot.BookingType.RANGE:
                raise serializers.ValidationError("This time slot only allows range bookings.")

            attrs["teacher"] = time_slot.teacher
            attrs["start_at"] = time_slot.start_time
            attrs["end_at"] = time_slot.end_time
            # Store the time slot in attrs to be used in create()
            attrs["_time_slot"] = time_slot
            
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        student_profile = getattr(request.user, "student_profile", None)
        if not student_profile:
            raise serializers.ValidationError("Only students can create bookings.")
        
        # Pop the time slot before saving the booking
        time_slot = validated_data.pop("_time_slot", None)
        validated_data.pop("time_slot_id", None)
        
        validated_data["student"] = student_profile
        # Default to requested; teacher approval is required before confirmation.
        validated_data.setdefault("status", SessionBooking.Status.REQUESTED)
        
        with transaction.atomic():
            if time_slot:
                # Lock the row to prevent race conditions
                locked_slot = TimeSlot.objects.select_for_update().get(id=time_slot.id)
                if locked_slot.is_booked or locked_slot.booking_id:
                    raise ValidationError("This time slot is no longer available.")
                
                booking = super().create(validated_data)
                
                # Mark the slot as booked
                locked_slot.is_booked = True
                locked_slot.booking = booking
                locked_slot.save(update_fields=["is_booked", "booking", "updated_at"])
            else:
                # Fallback if no time slot is provided (e.g., custom bookings outside of slots)
                booking = super().create(validated_data)
        
        teacher_user = getattr(booking.teacher, "user", None)
        if teacher_user:
            Notification.objects.create(
                user=teacher_user,
                title="New booking request",
                body=f"A student requested a session on {booking.start_at}.",
                related_booking=booking,
            )
        return booking


class BookingOrderSerializer(serializers.ModelSerializer):
    """Serializer for BookingOrder (package)."""

    class Meta:
        model = BookingOrder
        fields = [
            "id",
            "student",
            "teacher",
            "total_amount",
            "currency",
            "status",
            "stripe_checkout_id",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "student",
            "status",
            "stripe_checkout_id",
            "created_at",
        ]


class WeeklySlotSerializer(serializers.Serializer):
    day_of_week = serializers.IntegerField(min_value=0, max_value=6)
    start_time = serializers.TimeField()
    end_time = serializers.TimeField()


class BookingPackageSerializer(serializers.Serializer):
    """Serializer for creating a package of recurring bookings."""

    teacher_id = serializers.UUIDField()
    weekly_slots = WeeklySlotSerializer(many=True)
    duration_weeks = serializers.IntegerField(min_value=1, max_value=52)
    start_date = serializers.DateField(required=False)

    def validate_start_date(self, value):
        if value < timezone.now().date():
            raise serializers.ValidationError("Start date cannot be in the past.")
        return value


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            "id",
            "title",
            "body",
            "is_read",
            "created_at",
            "related_booking",
        ]
        read_only_fields = ["id", "created_at", "related_booking"]


from rest_framework import serializers

from risala_backend.users.models import TimeSlot


class TimeSlotSerializer(serializers.ModelSerializer):
    class Meta:
        model = TimeSlot
        fields = [
            "id",
            "start_time",
            "end_time",
            "duration_minutes",
            "is_booked",
            "allowed_booking_type",
            "booking",
            "batch_id",
            "batch_start_date",
            "batch_end_date",
        ]
        read_only_fields = ["id", "is_booked", "booking"]

class RangeBookingRequestSerializer(serializers.Serializer):
    """Serializer for requesting a range booking from existing time slots."""
    time_slot_ids = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
    )
    end_date = serializers.DateField(required=False, allow_null=True)

class DayPatternSerializer(serializers.Serializer):
    day_of_week = serializers.IntegerField()
    selected_times = serializers.ListField(child=serializers.TimeField())


class BulkSlotCreateSerializer(serializers.Serializer):
    duration_minutes = serializers.IntegerField()
    day_patterns = DayPatternSerializer(many=True)
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    skip_months = serializers.ListField(child=serializers.CharField(), required=False)
    overwrite = serializers.BooleanField(required=False, default=False)
    timezone_offset_minutes = serializers.IntegerField(required=False, allow_null=True)
    allowed_booking_type = serializers.ChoiceField(choices=["SINGLE", "RANGE", "BOTH"], default="BOTH")


class BulkSlotDeleteSerializer(serializers.Serializer):
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
