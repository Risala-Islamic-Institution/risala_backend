from django.conf import settings
from rest_framework.routers import DefaultRouter
from rest_framework.routers import SimpleRouter

from risala_backend.users.api.views import (
	UserViewSet,
	TeacherProfileViewSet,
	TeacherAvailabilityViewSet,
	SessionBookingViewSet,
	NotificationViewSet,
)
from risala_backend.courses.api.views import CourseViewSet, CourseModuleViewSet, LessonViewSet, EnrollmentViewSet, LessonProgressViewSet, CertificateViewSet, QuizQuestionViewSet, QuizAttemptViewSet, CourseReviewViewSet, CourseAnnouncementViewSet, CourseQuestionViewSet, CourseAnswerViewSet

router = DefaultRouter() if settings.DEBUG else SimpleRouter()

router.register("users", UserViewSet)
router.register("teachers", TeacherProfileViewSet, basename="teachers")
router.register("availability", TeacherAvailabilityViewSet, basename="availability")
router.register("bookings", SessionBookingViewSet, basename="bookings")
router.register("notifications", NotificationViewSet, basename="notifications")
router.register("courses", CourseViewSet, basename="courses")
router.register("modules", CourseModuleViewSet, basename="modules")
router.register("lessons", LessonViewSet, basename="lessons")
router.register("enrollments", EnrollmentViewSet, basename="enrollments")
router.register("lesson-progress", LessonProgressViewSet, basename="lesson-progress")
router.register("certificates", CertificateViewSet, basename="certificates")
router.register("quiz-questions", QuizQuestionViewSet, basename="quiz-questions")
router.register("quiz-attempts", QuizAttemptViewSet, basename="quiz-attempts")
router.register("course-reviews", CourseReviewViewSet, basename="course-reviews")
router.register("course-announcements", CourseAnnouncementViewSet, basename="course-announcements")
router.register("course-questions", CourseQuestionViewSet, basename="course-questions")
router.register("course-answers", CourseAnswerViewSet, basename="course-answers")


app_name = "api"
urlpatterns = router.urls
