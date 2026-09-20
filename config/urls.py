from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include, path, re_path
from django.views import defaults as default_views
from django.views.generic import TemplateView
from django.views.static import serve
from drf_spectacular.views import SpectacularAPIView
from drf_spectacular.views import SpectacularSwaggerView
from rest_framework.authtoken.views import obtain_auth_token

from risala_backend.utils.health import (
    AppVersionConfigView,
    HealthCheckView,
    trigger_sentry_test_error,
)


urlpatterns = [
    path("", TemplateView.as_view(template_name="pages/home.html"), name="home"),
    path(
        "about/",
        TemplateView.as_view(template_name="pages/about.html"),
        name="about",
    ),
    # Django Admin, use {% url 'admin:index' %}
    path(settings.ADMIN_URL, admin.site.urls),
    # User management
    path("users/", include("risala_backend.users.urls", namespace="users")),
    path("accounts/", include("allauth.urls")),
    # Your stuff: custom urls includes go here
    # ...
    # Media files
    *static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT),
]
urlpatterns += [
    re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
]

if settings.DEBUG:
    # Static file serving when using Gunicorn + Uvicorn for local web socket development
    urlpatterns += staticfiles_urlpatterns()


# ── V1 API URL PATTERNS ──────────────────────────────────────────────────────
api_v1_urlpatterns = [
    path("", include("config.api_router")),
    path("healthz/", HealthCheckView.as_view(), name="healthz-v1"),
    path("app/version/", AppVersionConfigView.as_view(), name="app-version-v1"),
    path("sentry-debug/", trigger_sentry_test_error, name="sentry-debug-v1"),
    path("auth-token/", obtain_auth_token, name="obtain_auth_token_v1"),
    path("auth/", include("dj_rest_auth.urls")),
    path("auth/registration/", include("dj_rest_auth.registration.urls")),
    path(
        "payments/",
        include("risala_backend.payments.urls", namespace="payments_v1"),
    ),
]

# API URLS
urlpatterns += [
    # Top-level Health Check Probe (Traefik / Cloudflare / Uptime monitors)
    path("healthz/", HealthCheckView.as_view(), name="healthz"),
    # Sentry Test Endpoint
    path("sentry-debug/", trigger_sentry_test_error, name="sentry-debug"),

    # Explicit Version 1 API namespace
    path("api/v1/", include((api_v1_urlpatterns, "v1"))),

    # Backward-Compatible Unversioned API (routes identically to v1)
    path("api/", include("config.api_router")),
    path("api/healthz/", HealthCheckView.as_view(), name="healthz-legacy"),
    path("api/app/version/", AppVersionConfigView.as_view(), name="app-version"),
    path("api/sentry-debug/", trigger_sentry_test_error, name="sentry-debug-legacy"),
    path("api/auth-token/", obtain_auth_token, name="obtain_auth_token"),
    path("api/auth/", include("dj_rest_auth.urls")),
    path("api/auth/registration/", include("dj_rest_auth.registration.urls")),
    path(
        "api/payments/",
        include("risala_backend.payments.urls", namespace="payments"),
    ),

    # OpenAPI Schema & Swagger Documentation
    path("api/schema/", SpectacularAPIView.as_view(), name="api-schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="api-schema"),
        name="api-docs",
    ),
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="api-v1-schema"),
    path(
        "api/v1/docs/",
        SpectacularSwaggerView.as_view(url_name="api-v1-schema"),
        name="api-v1-docs",
    ),
]

if settings.DEBUG:
    # This allows the error pages to be debugged during development, just visit
    # these url in browser to see how these error pages look like.
    urlpatterns += [
        path(
            "400/",
            default_views.bad_request,
            kwargs={"exception": Exception("Bad Request!")},
        ),
        path(
            "403/",
            default_views.permission_denied,
            kwargs={"exception": Exception("Permission Denied")},
        ),
        path(
            "404/",
            default_views.page_not_found,
            kwargs={"exception": Exception("Page not Found")},
        ),
        path("500/", default_views.server_error),
    ]
    if "debug_toolbar" in settings.INSTALLED_APPS:
        import debug_toolbar

        urlpatterns = [
            path("__debug__/", include(debug_toolbar.urls)),
            *urlpatterns,
        ]
