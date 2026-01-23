"""
API Serializers for User and Profile models.
"""
from rest_framework import serializers
from dj_rest_auth.registration.serializers import RegisterSerializer
from dj_rest_auth.serializers import LoginSerializer
from rest_framework import serializers as drf_serializers

from risala_backend.users.models import (
    User,
    Role,
    UserRole,
    TeacherProfile,
    StudentProfile,
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
