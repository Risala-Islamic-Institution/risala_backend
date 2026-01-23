import pytest
import uuid
from risala_backend.courses.models import Course, CourseModule, Lesson
from risala_backend.users.models import User, TeacherProfile, Role, UserRole


@pytest.mark.django_db
class TestCourseModel:
    def test_course_creation(self):
        """Test that a course can be created with required fields."""
        # Create user and teacher profile
        user = User.objects.create_user(
            email="instructor@test.com",
            username="instructor1",
            password="testpass123"
        )
        role = Role.objects.create(name=Role.RoleName.USTAZ)
        UserRole.objects.create(user=user, role=role)
        # Profile is auto-created by signal, but let's ensure it exists
        teacher_profile = TeacherProfile.objects.get(user=user)

        course = Course.objects.create(
            title="Foundations of Fiqh",
            description="Learn the fundamentals of Islamic jurisprudence.",
            created_by=teacher_profile,
            category=Course.Category.FIQH,
            level=Course.Level.BEGINNER
        )
        assert course.title == "Foundations of Fiqh"
        assert course.created_by == teacher_profile
        assert course.slug == "foundations-of-fiqh"

    def test_course_has_uuid_pk(self):
        """Test that Course uses UUID as primary key."""
        user = User.objects.create_user(
            email="instructor2@test.com",
            username="instructor2",
            password="testpass123"
        )
        role = Role.objects.create(name=Role.RoleName.USTAZ)
        UserRole.objects.create(user=user, role=role)
        teacher_profile = TeacherProfile.objects.get(user=user)

        course = Course.objects.create(
            title="Arabic Grammar",
            description="Master Arabic grammar.",
            created_by=teacher_profile,
        )
        assert isinstance(course.pk, uuid.UUID)


@pytest.mark.django_db
class TestCourseModuleModel:
    def test_module_belongs_to_course(self):
        """Test that a module is linked to a course."""
        user = User.objects.create_user(
            email="instructor4@test.com",
            username="instructor4",
            password="testpass123"
        )
        role = Role.objects.create(name=Role.RoleName.USTAZ)
        UserRole.objects.create(user=user, role=role)
        teacher_profile = TeacherProfile.objects.get(user=user)

        course = Course.objects.create(
            title="Seerah",
            description="Life of the Prophet.",
            created_by=teacher_profile,
        )
        module = CourseModule.objects.create(
            course=course,
            title="Early Life",
            order_index=1,
        )
        assert module.course == course
        assert module.order_index == 1


@pytest.mark.django_db
class TestLessonModel:
    def test_lesson_belongs_to_module(self):
        """Test that a lesson is linked to a module."""
        user = User.objects.create_user(
            email="instructor5@test.com",
            username="instructor5",
            password="testpass123"
        )
        role = Role.objects.create(name=Role.RoleName.USTAZ)
        UserRole.objects.create(user=user, role=role)
        teacher_profile = TeacherProfile.objects.get(user=user)

        course = Course.objects.create(
            title="Hadith Sciences",
            description="Study of Hadith.",
            created_by=teacher_profile,
        )
        module = CourseModule.objects.create(
            course=course,
            title="Introduction",
            order_index=1,
        )
        lesson = Lesson.objects.create(
            module=module,
            title="What is Hadith?",
            lesson_type=Lesson.LessonType.READING,
            order=1,
        )
        assert lesson.module == module
        assert lesson.title == "What is Hadith?"
