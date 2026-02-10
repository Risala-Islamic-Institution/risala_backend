from rest_framework import status
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin, CreateModelMixin, UpdateModelMixin, DestroyModelMixin
from rest_framework.viewsets import GenericViewSet
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from risala_backend.courses.models import Course, CourseModule, Lesson, Enrollment
from django.db.models import Q
from risala_backend.courses.api.serializers import (
    CourseSerializer,
    CourseModuleSerializer,
    LessonSerializer,
    EnrollmentSerializer,
    EnrollmentUpdateSerializer,
    LessonProgressSerializer,
    CertificateSerializer,
    QuizQuestionSerializer,
    QuizAttemptSerializer,
    CourseReviewSerializer,
    CourseAnnouncementSerializer,
    CourseQuestionSerializer,
    CourseAnswerSerializer,
)
from risala_backend.courses.models import LessonProgress, Certificate, QuizQuestion, QuizAttempt, CourseReview, CourseAnnouncement, CourseQuestion, CourseAnswer


class CourseViewSet(ListModelMixin, RetrieveModelMixin, CreateModelMixin, UpdateModelMixin, GenericViewSet):
    serializer_class = CourseSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "slug"

    def get_queryset(self):
        user = self.request.user
        qs = Course.objects.all().select_related("created_by__user")
        if hasattr(user, "teacher_profile"):
            # Teacher sees own courses
            return qs.filter(created_by=user.teacher_profile)
        # Students see published courses and any courses they are enrolled in
        student_profile = getattr(user, "student_profile", None)
        if student_profile:
            return qs.filter(Q(is_published=True) | Q(enrollments__student=student_profile)).distinct()
        # Anonymous or other roles: only published
        return qs.filter(is_published=True)

    @action(detail=True, methods=["post"], url_path="publish")
    def publish(self, request, slug=None):
        course = self.get_object()
        teacher_profile = getattr(request.user, "teacher_profile", None)
        if not teacher_profile or course.created_by != teacher_profile:
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        course.is_published = True
        course.save(update_fields=["is_published", "updated_at"])
        serializer = self.get_serializer(course)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="unpublish")
    def unpublish(self, request, slug=None):
        course = self.get_object()
        teacher_profile = getattr(request.user, "teacher_profile", None)
        if not teacher_profile or course.created_by != teacher_profile:
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        course.is_published = False
        course.save(update_fields=["is_published", "updated_at"])
        serializer = self.get_serializer(course)
        return Response(serializer.data)


class CourseModuleViewSet(ListModelMixin, CreateModelMixin, UpdateModelMixin, DestroyModelMixin, RetrieveModelMixin, GenericViewSet):
    serializer_class = CourseModuleSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = CourseModule.objects.select_related("course")
        course_id = self.request.query_params.get("course_id")
        
        if hasattr(user, "teacher_profile"):
            qs = qs.filter(course__created_by=user.teacher_profile)
        elif hasattr(user, "student_profile"):
             # Students can see modules of courses they are enrolled in OR published courses
             qs = qs.filter(Q(course__is_published=True) | Q(course__enrollments__student=user.student_profile)).distinct()
        else:
             return CourseModule.objects.none()

        if course_id:
            qs = qs.filter(course__id=course_id)
        return qs

    def perform_create(self, serializer):
        # Additional check: ensure course belongs to teacher
        course = serializer.validated_data.get("course")
        teacher_profile = getattr(self.request.user, "teacher_profile", None)
        if not teacher_profile or course.created_by != teacher_profile:
             raise serializers.ValidationError("You can only add modules to your own courses.")
        serializer.save()


class LessonViewSet(ListModelMixin, CreateModelMixin, UpdateModelMixin, DestroyModelMixin, RetrieveModelMixin, GenericViewSet):
    serializer_class = LessonSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = Lesson.objects.select_related("module__course")
        module_id = self.request.query_params.get("module_id")

        if hasattr(user, "teacher_profile"):
            qs = qs.filter(module__course__created_by=user.teacher_profile)
        elif hasattr(user, "student_profile"):
             qs = qs.filter(Q(module__course__is_published=True) | Q(module__course__enrollments__student=user.student_profile)).distinct()
        else:
             return Lesson.objects.none()

        if module_id:
            qs = qs.filter(module__id=module_id)
        return qs

    def perform_create(self, serializer):
        module = serializer.validated_data.get("module")
        teacher_profile = getattr(self.request.user, "teacher_profile", None)
        if not teacher_profile or module.course.created_by != teacher_profile:
             raise serializers.ValidationError("You can only add lessons to your own courses.")
        serializer.save()


class EnrollmentViewSet(ListModelMixin, RetrieveModelMixin, CreateModelMixin, UpdateModelMixin, GenericViewSet):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if hasattr(user, "student_profile"):
            return Enrollment.objects.filter(student=user.student_profile).select_related("course", "course__created_by__user")
        if hasattr(user, "teacher_profile"):
            # Teachers can view enrollments for their courses
            return Enrollment.objects.filter(course__created_by=user.teacher_profile).select_related("course", "student__user")
        return Enrollment.objects.none()

    def get_serializer_class(self):
        if self.action in ["update", "partial_update"]:
            return EnrollmentUpdateSerializer
        return EnrollmentSerializer

    def create(self, request, *args, **kwargs):
        # Prevent duplicate enrollment and ensure published
        course_id = request.data.get("course_id")
        if not course_id:
            return Response({"detail": "course_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        # Reuse serializer validation
        return super().create(request, *args, **kwargs)


class LessonProgressViewSet(ListModelMixin, RetrieveModelMixin, CreateModelMixin, UpdateModelMixin, GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = LessonProgressSerializer

    def get_queryset(self):
        user = self.request.user
        qs = LessonProgress.objects.select_related("enrollment__student__user", "enrollment__course", "lesson")
        enrollment_id = self.request.query_params.get("enrollment_id")
        if hasattr(user, "student_profile"):
            qs = qs.filter(enrollment__student=user.student_profile)
        elif hasattr(user, "teacher_profile"):
            qs = qs.filter(enrollment__course__created_by=user.teacher_profile)
        else:
            return LessonProgress.objects.none()
        if enrollment_id:
            qs = qs.filter(enrollment__id=enrollment_id)
        return qs


class CertificateViewSet(ListModelMixin, RetrieveModelMixin, GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = CertificateSerializer

    def get_queryset(self):
        user = self.request.user
        qs = Certificate.objects.select_related("enrollment__student__user", "enrollment__course")
        if hasattr(user, "student_profile"):
            return qs.filter(enrollment__student=user.student_profile)
        if hasattr(user, "teacher_profile"):
            return qs.filter(enrollment__course__created_by=user.teacher_profile)
        return Certificate.objects.none()


class QuizQuestionViewSet(ListModelMixin, CreateModelMixin, UpdateModelMixin, DestroyModelMixin, RetrieveModelMixin, GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = QuizQuestionSerializer

    def get_queryset(self):
        lesson_id = self.request.query_params.get("lesson_id")
        # Ensure only authorized users see questions (teacher or student taking quiz)
        # For simplicity, filtering by lesson_id is key.
        qs = QuizQuestion.objects.all()
        if lesson_id:
            qs = qs.filter(lesson__id=lesson_id)
        return qs


class QuizAttemptViewSet(CreateModelMixin, ListModelMixin, RetrieveModelMixin, GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = QuizAttemptSerializer

    def get_queryset(self):
        user = self.request.user
        qs = QuizAttempt.objects.select_related("enrollment__student__user", "lesson")
        if hasattr(user, "student_profile"):
            return qs.filter(enrollment__student=user.student_profile)
        if hasattr(user, "teacher_profile"):
            return qs.filter(enrollment__course__created_by=user.teacher_profile)
        return QuizAttempt.objects.none()


class CourseReviewViewSet(CreateModelMixin, ListModelMixin, RetrieveModelMixin, GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = CourseReviewSerializer

    def get_queryset(self):
        user = self.request.user
        qs = CourseReview.objects.select_related("enrollment__student__user", "enrollment__course")
        if hasattr(user, "student_profile"):
            return qs.filter(enrollment__student=user.student_profile)
        if hasattr(user, "teacher_profile"):
            return qs.filter(enrollment__course__created_by=user.teacher_profile)
        return CourseReview.objects.none()


class CourseAnnouncementViewSet(CreateModelMixin, ListModelMixin, RetrieveModelMixin, GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = CourseAnnouncementSerializer

    def get_queryset(self):
        user = self.request.user
        qs = CourseAnnouncement.objects.select_related("course")
        course_id = self.request.query_params.get("course_id")
        
        if hasattr(user, "teacher_profile"):
            qs = qs.filter(course__created_by=user.teacher_profile)
        elif hasattr(user, "student_profile"):
             # Students see published course announcements
             qs = qs.filter(course__is_published=True)
        else:
             qs = qs.filter(course__is_published=True) # Fallback

        if course_id:
            qs = qs.filter(course__id=course_id)
        return qs


class CourseQuestionViewSet(CreateModelMixin, ListModelMixin, RetrieveModelMixin, GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = CourseQuestionSerializer

    def get_queryset(self):
        user = self.request.user
        qs = CourseQuestion.objects.select_related("course", "student__user")
        course_id = self.request.query_params.get("course_id")
        if hasattr(user, "student_profile"):
            qs = qs.filter(student=user.student_profile)
        elif hasattr(user, "teacher_profile"):
            qs = qs.filter(course__created_by=user.teacher_profile)
        else:
            return CourseQuestion.objects.none()
        if course_id:
            qs = qs.filter(course__id=course_id)
        return qs


class CourseAnswerViewSet(CreateModelMixin, ListModelMixin, RetrieveModelMixin, GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = CourseAnswerSerializer

    def get_queryset(self):
        user = self.request.user
        qs = CourseAnswer.objects.select_related("question__course", "teacher__user")
        if hasattr(user, "student_profile"):
            return qs.filter(question__student=user.student_profile)
        if hasattr(user, "teacher_profile"):
            return qs.filter(teacher=user.teacher_profile)
        return CourseAnswer.objects.none()
