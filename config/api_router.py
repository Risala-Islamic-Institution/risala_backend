from django.conf import settings
from rest_framework.routers import DefaultRouter
from rest_framework.routers import SimpleRouter

from risala_backend.users.api.views import (
	UserViewSet,
	TeacherProfileViewSet,
	TeacherAvailabilityViewSet,
	SessionBookingViewSet,
)

router = DefaultRouter() if settings.DEBUG else SimpleRouter()

router.register("users", UserViewSet)
router.register("teachers", TeacherProfileViewSet, basename="teachers")
router.register("availability", TeacherAvailabilityViewSet, basename="availability")
router.register("bookings", SessionBookingViewSet, basename="bookings")


app_name = "api"
urlpatterns = router.urls
