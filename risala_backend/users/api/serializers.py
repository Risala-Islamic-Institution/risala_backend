"""
API Serializers for User and Profile models.
"""
from rest_framework import serializers
from dj_rest_auth.registration.serializers import RegisterSerializer
from dj_rest_auth.serializers import LoginSerializer
from rest_framework import serializers as drf_serializers
from django.db import IntegrityError

from risala_backend.users.models import (
    User,
    Role,
    UserRole,
    TeacherProfile,
    StudentProfile,
    TeacherAvailability,
    SessionBooking,
)


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
        read_only_fields = ["id", "rating_average", "total_students", "verification_status", "created_at"]


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
        teacher_profile = getattr(request.user, "teacher_profile", None) if request else None

        # Derive teacher and current values for both create and update flows
        teacher = self.instance.teacher if self.instance else teacher_profile
        day_of_week = attrs.get("day_of_week", getattr(self.instance, "day_of_week", None))
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
    class Meta:
        model = SessionBooking
        fields = [
            "id",
            "teacher",
            "student",
            "start_at",
            "end_at",
            "status",
            "created_at",
        ]
        read_only_fields = ["id", "student", "status", "created_at"]

    def validate(self, attrs):
        start_at = attrs.get("start_at")
        end_at = attrs.get("end_at")
        teacher = attrs.get("teacher")
        if not (start_at and end_at and teacher):
            raise serializers.ValidationError("teacher, start_at and end_at are required.")
        if end_at <= start_at:
            raise serializers.ValidationError("end_at must be after start_at.")
        # Prevent overlap with existing bookings
        overlaps = SessionBooking.objects.filter(
            teacher=teacher,
            status__in=[SessionBooking.Status.PENDING, SessionBooking.Status.CONFIRMED],
            start_at__lt=end_at,
            end_at__gt=start_at,
        ).exists()
        if overlaps:
            raise serializers.ValidationError("Selected time overlaps with an existing booking.")
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        student_profile = getattr(request.user, "student_profile", None)
        if not student_profile:
            raise serializers.ValidationError("Only students can create bookings.")
        validated_data["student"] = student_profile
        # Default to PENDING; confirmation could be a follow-up action
        validated_data.setdefault("status", SessionBooking.Status.PENDING)
        return super().create(validated_data)
