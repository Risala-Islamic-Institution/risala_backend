"""
Risala Performance & Load Testing Suite (Locust)
=================================================
Simulates realistic concurrent mobile traffic against the Risala backend.

Usage:
  # 1. Interactive Web Dashboard (recommended):
  locust -f locustfile.py --host http://localhost:8000
  # Then open http://localhost:8089 in your browser

  # 2. Headless Quick Test (terminal):
  locust -f locustfile.py --headless -u 20 -r 2 -t 30s --host https://risala-5vs3.onrender.com

  # 3. Stress Test (50 concurrent users):
  locust -f locustfile.py --headless -u 50 -r 5 -t 1m --host https://risala-5vs3.onrender.com
"""

import os
from locust import HttpUser, task, between, tag


class RisalaVisitorUser(HttpUser):
    """
    Simulates anonymous or guest visitors browsing the mobile app.
    Represents ~70% of typical traffic (finding teachers, browsing courses, health probes).
    """
    wait_time = between(1, 3)
    weight = 3

    @tag("health")
    @task(3)
    def check_health(self):
        """Monitors system uptime and database/cache latency."""
        with self.client.get("/healthz/", catch_response=True) as response:
            if response.status_code == 200 and "status" in response.text:
                response.success()
            else:
                response.failure(f"Health check failed: {response.status_code}")

    @tag("version")
    @task(2)
    def check_app_version(self):
        """Simulates app startup remote kill-switch / version check."""
        with self.client.get("/api/v1/app/version/", catch_response=True) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Version check failed: {response.status_code}")

    @tag("browse")
    @task(4)
    def browse_teachers(self):
        """Browses list of active teachers with filtering."""
        with self.client.get("/api/v1/teachers/", catch_response=True) as response:
            if response.status_code in (200, 404):
                response.success()
            else:
                response.failure(f"Browse teachers error: {response.status_code}")

    @tag("courses")
    @task(2)
    def browse_courses(self):
        """Browses Quran and Islamic study courses."""
        with self.client.get("/api/v1/courses/", catch_response=True) as response:
            if response.status_code in (200, 404):
                response.success()
            else:
                response.failure(f"Browse courses error: {response.status_code}")


class RisalaStudentUser(HttpUser):
    """
    Simulates registered students interacting with dashboard, attendance, and bookings.
    """
    wait_time = between(2, 5)
    weight = 2

    def on_start(self):
        """Optional: Configure auth header if TEST_AUTH_TOKEN is provided."""
        self.auth_token = os.getenv("TEST_AUTH_TOKEN", "")
        self.headers = {
            "Authorization": f"Token {self.auth_token}"
        } if self.auth_token else {}

    @tag("student")
    @task(3)
    def view_dashboard(self):
        """Loads student dashboard and user profile."""
        with self.client.get("/api/v1/users/me/", headers=self.headers, catch_response=True) as response:
            # If no auth token provided, 401 is expected behavior
            if response.status_code in (200, 401):
                response.success()
            else:
                response.failure(f"User profile error: {response.status_code}")

    @tag("slots")
    @task(2)
    def view_available_time_slots(self):
        """Queries available time slots for booking."""
        with self.client.get("/api/v1/users/time-slots/", headers=self.headers, catch_response=True) as response:
            if response.status_code in (200, 401, 404):
                response.success()
            else:
                response.failure(f"Time slots query error: {response.status_code}")

    @tag("notifications")
    @task(1)
    def view_notifications(self):
        """Polls for unread session notifications."""
        with self.client.get("/api/v1/users/notifications/", headers=self.headers, catch_response=True) as response:
            if response.status_code in (200, 401, 404):
                response.success()
            else:
                response.failure(f"Notifications query error: {response.status_code}")
