# Risala Backend - Production Operations & Services Architecture Guide

This guide details the enterprise production tooling, dual-route API versioning, monitoring services, and operational best practices for the Risala Backend.

---

## 1. Dual-Route API Versioning

To ensure 100% backward compatibility for existing web frontend clients and deployed mobile builds while providing a modern versioned structure for future expansions, the backend implements **Dual Routing**:

| Route Pattern | Purpose | Target Clients |
|:---|:---|:---|
| `/api/v1/...` | Canonical Version 1 API | New mobile builds, API integrations |
| `/api/...` | Backward-Compatible Alias | Web frontend, existing mobile installations |
| `/api/v1/docs/` | Swagger / OpenAPI UI for v1 | Developers & API consumers |
| `/api/docs/` | Legacy Swagger / OpenAPI UI | Developers & API consumers |

### How to Introduce `/api/v2/` in Future Expansions
When your upcoming big expansion introduces breaking database model changes or modified response contracts:
1. Create `config/api_v2_router.py` with the new ViewSets.
2. Add `path("api/v2/", include((api_v2_patterns, "v2")))` in `config/urls.py`.
3. Add `"v2"` to `REST_FRAMEWORK["ALLOWED_VERSIONS"]`.
4. Existing v1 clients on `/api/v1/` and `/api/` continue running completely unaffected!

---

## 2. Health Check Probe (`/healthz/`)

An enterprise-grade health check probe is available at:
- `GET /healthz/` (Root load-balancer probe)
- `GET /api/v1/healthz/`

### Response Contract (HTTP 200 OK)
```json
{
  "status": "healthy",
  "environment": "production",
  "timestamp": "2026-09-20T12:00:00Z",
  "api_version": "1.0.0",
  "components": {
    "database": "connected",
    "cache": "connected"
  }
}
```
If the database connection is lost, it immediately returns **HTTP 503 Service Unavailable**, allowing Cloudflare, Traefik, or AWS ALB to remove unhealthy nodes from traffic pools.

### Recommended Free Uptime Monitor
Configure **UptimeRobot** (or **BetterStack**):
- URL: `https://api.risala.org/healthz/`
- Interval: Every 60 seconds
- Alerts: Telegram, SMS, or Email when down.

---

## 3. Remote Kill-Switch & Mobile Version Control Plane

Endpoint: `GET /api/v1/app/version/` and `GET /api/app/version/`

Configured via environment variables in `.envs/.production/.django`:
```ini
MOBILE_APP_LATEST_VERSION=1.0.0
MOBILE_APP_MIN_VERSION=1.0.0
MOBILE_APP_FORCE_UPDATE=false
MOBILE_APP_RELEASE_NOTES=Official v1.0.0 release of Risala Islamic Institution Mobile Platform.
MOBILE_APP_DOWNLOAD_URL=https://play.google.com/store/apps/details?id=com.example.risala_mobile
```
- Set `MOBILE_APP_FORCE_UPDATE=true` or bump `MOBILE_APP_MIN_VERSION` when an old app version has a critical security flaw or deprecated API dependencies. The mobile app will immediately display the blocking royal screen preventing access to backend endpoints.

---

## 4. Error Tracking with Sentry

Sentry is pre-wired in `config/settings/production.py`.

To activate in production:
1. Create a project at [sentry.io](https://sentry.io).
2. Set in `.envs/.production/.django`:
   ```ini
   SENTRY_DSN=https://xxxxxxxxxxxxxxxx@o0.ingest.sentry.io/0000000
   SENTRY_ENVIRONMENT=production
   SENTRY_TRACES_SAMPLE_RATE=0.2
   ```
3. Sentry will automatically record:
   - All unhandled 500 exceptions with local variable scopes.
   - Slow SQL queries exceeding 200ms.
   - Failed background Celery jobs.

---

## 5. Background Task Monitoring with Flower

Flower provides a real-time web dashboard for Celery background workers.

In your production Docker Compose setup (`docker-compose.production.yml`):
- Flower runs on port `5555`.
- Configured in `.envs/.production/.django`:
  ```ini
  CELERY_FLOWER_USER=risala_admin
  CELERY_FLOWER_PASSWORD=YourSecurePasswordHere
  ```
- Access at: `https://flower.yourdomain.com` or `http://your-server-ip:5555`.
- Features: Real-time worker task graphs, retry failed jobs, task execution latency histograms.

---

## 6. DRF Rate Limiting & DDoS Defense

Configured in `config/settings/base.py`:
- **Anonymous rate limit**: `120 requests/minute` (prevents scraper bots).
- **Authenticated user limit**: `600 requests/minute`.
- **Auth endpoint burst rate**: `10 requests/minute` (mitigates credential stuffing on `/api/v1/auth/login/`).
