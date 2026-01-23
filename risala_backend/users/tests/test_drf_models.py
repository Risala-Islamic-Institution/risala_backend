"""
Tests for User, Role, and Profile models.
Based on Risala_doc class diagram.
"""
import pytest
import uuid
from django.utils import timezone

from risala_backend.users.models import (
    User,
    Role,
    Permission,
    UserRole,
    TeacherProfile,
    StudentProfile,
)


@pytest.mark.django_db
class TestUserModel:
    """Tests for the User model."""

    def test_user_creation(self):
        """Test basic user creation."""
        user = User.objects.create_user(
            email="test@risala.com",
            username="testuser",
            password="testpass123",
            full_name="Test User",
        )
        assert user.email == "test@risala.com"
        assert user.username == "testuser"
        assert user.full_name == "Test User"
        assert user.check_password("testpass123")

    def test_user_has_uuid_pk(self):
        """Test that User uses UUID as primary key."""
        user = User.objects.create_user(
            email="uuid@risala.com",
            username="uuiduser",
            password="testpass123",
        )
        assert isinstance(user.pk, uuid.UUID)

    def test_user_has_timestamps(self):
        """Test that User has created_at and updated_at."""
        user = User.objects.create_user(
            email="timestamp@risala.com",
            username="timestampuser",
            password="testpass123",
        )
        assert user.created_at is not None
        assert user.updated_at is not None

    def test_user_default_values(self):
        """Test default values for user fields."""
        user = User.objects.create_user(
            email="defaults@risala.com",
            username="defaultsuser",
            password="testpass123",
        )
        assert user.is_active is True
        assert user.is_suspended is False
        assert user.timezone == "UTC"
        assert user.preferred_language == "en"


@pytest.mark.django_db
class TestRoleModel:
    """Tests for the Role model."""

    def test_role_creation(self):
        """Test Role creation."""
        role = Role.objects.create(
            name=Role.RoleName.STUDENT,
            description="Student role for learners",
        )
        assert role.name == "STUDENT"
        assert isinstance(role.pk, uuid.UUID)

    def test_user_role_assignment(self):
        """Test Many-to-Many User-Role relationship."""
        user = User.objects.create_user(
            email="roletest@risala.com",
            username="roleuser",
            password="testpass123",
        )
        role = Role.objects.create(name=Role.RoleName.USTAZ)
        
        UserRole.objects.create(user=user, role=role)
        
        assert user.roles.count() == 1
        assert user.has_role("USTAZ")

    def test_user_multiple_roles(self):
        """Test user can have multiple roles (e.g., both Teacher and Student)."""
        user = User.objects.create_user(
            email="multirole@risala.com",
            username="multiroleuser",
            password="testpass123",
        )
        student_role = Role.objects.create(name=Role.RoleName.STUDENT)
        teacher_role = Role.objects.create(name=Role.RoleName.USTAZ)
        
        UserRole.objects.create(user=user, role=student_role)
        UserRole.objects.create(user=user, role=teacher_role)
        
        assert user.roles.count() == 2
        assert user.has_role("STUDENT")
        assert user.has_role("USTAZ")


@pytest.mark.django_db
class TestPermissionModel:
    """Tests for the Permission model."""

    def test_permission_creation(self):
        """Test Permission creation."""
        permission = Permission.objects.create(
            code="CREATE_COURSE",
            description="Can create courses",
            scope=Permission.Scope.COURSE,
        )
        assert permission.code == "CREATE_COURSE"
        assert permission.scope == "COURSE"


@pytest.mark.django_db
class TestTeacherProfile:
    """Tests for TeacherProfile model."""

    def test_teacher_profile_creation(self):
        """Test TeacherProfile creation."""
        user = User.objects.create_user(
            email="teacher@risala.com",
            username="teacher",
            password="testpass123",
        )
        profile = TeacherProfile.objects.create(
            user=user,
            biography="Experienced Quran teacher",
            specialization=TeacherProfile.Specialization.TAJWEED,
            hourly_rate=25.00,
        )
        assert profile.user == user
        assert profile.specialization == "TAJWEED"
        assert profile.verification_status == "PENDING"

    def test_teacher_profile_auto_creation_on_role(self):
        """Test that TeacherProfile is auto-created when USTAZ role is assigned."""
        user = User.objects.create_user(
            email="autoteacher@risala.com",
            username="autoteacher",
            password="testpass123",
        )
        role = Role.objects.create(name=Role.RoleName.USTAZ)
        UserRole.objects.create(user=user, role=role)
        
        assert hasattr(user, "teacher_profile")
        assert user.teacher_profile is not None


@pytest.mark.django_db
class TestStudentProfile:
    """Tests for StudentProfile model."""

    def test_student_profile_creation(self):
        """Test StudentProfile creation."""
        user = User.objects.create_user(
            email="student@risala.com",
            username="student",
            password="testpass123",
        )
        profile = StudentProfile.objects.create(
            user=user,
            learning_goals="Learn Tajweed",
            current_level=StudentProfile.CurrentLevel.BEGINNER,
        )
        assert profile.user == user
        assert profile.current_level == "BEGINNER"
        assert profile.enrollment_status == "ACTIVE"

    def test_student_profile_auto_creation_on_role(self):
        """Test that StudentProfile is auto-created when STUDENT role is assigned."""
        user = User.objects.create_user(
            email="autostudent@risala.com",
            username="autostudent",
            password="testpass123",
        )
        role = Role.objects.create(name=Role.RoleName.STUDENT)
        UserRole.objects.create(user=user, role=role)
        
        assert hasattr(user, "student_profile")
        assert user.student_profile is not None
