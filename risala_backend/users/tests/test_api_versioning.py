from django.urls import resolve, reverse
from rest_framework.test import APIRequestFactory
from risala_backend.utils.health import HealthCheckView, AppVersionConfigView


def test_health_check_url_resolutions():
    """Verify health-check endpoint resolves on root, legacy /api/, and versioned /api/v1/."""
    assert reverse("healthz") == "/healthz/"
    assert resolve("/healthz/").func.view_class == HealthCheckView
    assert resolve("/api/healthz/").func.view_class == HealthCheckView
    assert resolve("/api/v1/healthz/").func.view_class == HealthCheckView


def test_app_version_url_resolutions():
    """Verify mobile kill-switch and version endpoint resolves on both /api/ and /api/v1/."""
    assert resolve("/api/app/version/").func.view_class == AppVersionConfigView
    assert resolve("/api/v1/app/version/").func.view_class == AppVersionConfigView


def test_dual_routing_api_viewsets():
    """Verify ViewSets are accessible on both /api/ and /api/v1/ namespaces."""
    match_legacy = resolve("/api/teachers/")
    match_v1 = resolve("/api/v1/teachers/")

    assert match_legacy.func.cls == match_v1.func.cls


def test_health_check_view_payload():
    """Verify HealthCheckView returns valid JSON contract."""
    factory = APIRequestFactory()
    request = factory.get("/healthz/")
    view = HealthCheckView.as_view()
    response = view(request)

    assert response.status_code in [200, 503]
    assert "status" in response.data
    assert "components" in response.data
    assert "database" in response.data["components"]
    assert "cache" in response.data["components"]


def test_app_version_config_view_payload():
    """Verify AppVersionConfigView returns required SemVer and kill-switch attributes."""
    factory = APIRequestFactory()
    request = factory.get("/api/v1/app/version/")
    view = AppVersionConfigView.as_view()
    response = view(request)

    assert response.status_code == 200
    assert "latest_version" in response.data
    assert "min_supported_version" in response.data
    assert "force_update" in response.data
    assert "download_url" in response.data
