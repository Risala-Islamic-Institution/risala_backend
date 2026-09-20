import logging
from django.conf import settings
from django.db import connection
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import AnonRateThrottle

logger = logging.getLogger(__name__)


class HealthCheckView(APIView):
    """
    Enterprise health-check probe for load balancers (Traefik, Cloudflare)
    and external uptime monitors (UptimeRobot, BetterStack).
    Returns HTTP 200 when healthy, or HTTP 503 if database connection fails.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, *args, **kwargs):
        is_healthy = True
        components = {}

        # 1. Probe Database Connection
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1;")
                cursor.fetchone()
            components["database"] = "connected"
        except Exception as exc:
            is_healthy = False
            components["database"] = f"error: {str(exc)}"
            logger.error("Health check database probe failed: %s", exc)

        # 2. Probe Cache (Redis)
        try:
            cache_key = "__health_probe__"
            cache.set(cache_key, "ok", timeout=10)
            if cache.get(cache_key) == "ok":
                components["cache"] = "connected"
            else:
                components["cache"] = "degraded"
        except Exception as exc:
            components["cache"] = f"unavailable: {str(exc)}"
            logger.warning("Health check cache probe degraded: %s", exc)

        payload = {
            "status": "healthy" if is_healthy else "unhealthy",
            "environment": getattr(settings, "ENVIRONMENT", "production" if not settings.DEBUG else "development"),
            "timestamp": timezone.now().isoformat(),
            "api_version": "1.0.0",
            "components": components,
        }

        http_status = status.HTTP_200_OK if is_healthy else status.HTTP_503_SERVICE_UNAVAILABLE
        return Response(payload, status=http_status)


class AppVersionConfigView(APIView):
    """
    Control-plane endpoint for Risala Mobile remote versioning,
    kill-switch enforcement, and in-app update checks.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, *args, **kwargs):
        config = {
            "latest_version": getattr(settings, "MOBILE_APP_LATEST_VERSION", "1.0.0"),
            "min_supported_version": getattr(settings, "MOBILE_APP_MIN_VERSION", "1.0.0"),
            "release_notes": getattr(
                settings,
                "MOBILE_APP_RELEASE_NOTES",
                "Official v1.0.0 release of Risala Islamic Institution Mobile Platform.",
            ),
            "force_update": getattr(settings, "MOBILE_APP_FORCE_UPDATE", False),
            "download_url": getattr(
                settings,
                "MOBILE_APP_DOWNLOAD_URL",
                "https://play.google.com/store/apps/details?id=com.example.risala_mobile",
            ),
        }
        return Response(config, status=status.HTTP_200_OK)


class AuthRateThrottle(AnonRateThrottle):
    """
    Strict rate-limiter for authentication endpoints to prevent brute-force attacks.
    """
    scope = "auth"
