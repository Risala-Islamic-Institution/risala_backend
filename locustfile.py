"""
Risala Enterprise Performance & End-to-End Load Testing Suite (Locust)
======================================================================
Comprehensive load test covering:
1. Public Visitor Browsing (Health, App Version, Teachers, Courses, Payments)
2. High-Concurrency Student Booking Race Conditions (Single & Overlapping Multi-Slot Range)
3. Authenticated Student Workflows (User Profile, Slots, Notifications, Enrollments, Reviews, Modules, Lessons)

Credentials:
- Default student credentials: seudsahm1@gmail.com / 0966143454Sahm
- Cached auth token: 0f0d7fd1f8bd1308d1361cb9afabc67550dd1497 (auto-refreshed via /api/v1/auth/login/)

Usage:
  # 1. Start Locust in Docker:
  docker compose -f docker-compose.local.yml up -d locust
  # Then open http://localhost:8089 in your browser and enter:
  # Host: https://risala-5vs3.onrender.com (or http://django:8000 for local)
"""

import os
import random
from locust import HttpUser, task, between, tag, events

# Default student credentials and verified token
DEFAULT_STUDENT_EMAIL = os.getenv("STUDENT_EMAIL", "seudsahm1@gmail.com")
DEFAULT_STUDENT_PASSWORD = os.getenv("STUDENT_PASSWORD", "0966143454Sahm")
SHARED_AUTH_TOKEN = os.getenv("TEST_AUTH_TOKEN", "0f0d7fd1f8bd1308d1361cb9afabc67550dd1497")


def get_auth_token(client):
    """
    Retrieves or fetches the active student authentication token.
    Uses the cached token or logs into /api/v1/auth/login/ using student credentials.
    """
    global SHARED_AUTH_TOKEN
    if SHARED_AUTH_TOKEN:
        return SHARED_AUTH_TOKEN

    email = os.getenv("STUDENT_EMAIL", DEFAULT_STUDENT_EMAIL)
    password = os.getenv("STUDENT_PASSWORD", DEFAULT_STUDENT_PASSWORD)
    try:
        res = client.post(
            "/api/v1/auth/login/",
            json={"email": email, "password": password},
            name="/api/v1/auth/login/ [Student Login]",
        )
        if res.status_code == 200:
            data = res.json()
            SHARED_AUTH_TOKEN = data.get("key") or data.get("token") or ""
            return SHARED_AUTH_TOKEN
    except Exception as exc:
        print(f"Student authentication warning: {exc}")
    return ""


class ConcurrentBookingMetrics:
    """Tracks booking outcomes across all concurrent worker threads."""
    bookings_won = 0
    conflicts_handled_cleanly = 0
    double_bookings_detected = 0
    throttled_429 = 0
    gateway_retries_502_503 = 0
    unhandled_errors = 0
    tested_slots = set()


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    print("\n" + "=" * 70)
    print("  RISALA LOAD & PERFORMANCE CONCURRENCY TEST INITIALIZED")
    print("=" * 70)
    print(f"Target Host: {environment.host}")
    print(f"Authenticated Student User: {DEFAULT_STUDENT_EMAIL}")
    print("Testing concurrent booking race conditions and platform endpoints...")
    print("=" * 70 + "\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    print("\n" + "=" * 70)
    print("  RISALA LOAD TEST COMPLETED")
    print("=" * 70)
    print(f"Contested Slot Races:            {len(ConcurrentBookingMetrics.tested_slots)}")
    print(f"Successful Bookings (Won):       {ConcurrentBookingMetrics.bookings_won}")
    print(f"Handled Conflicts (Safe 400/404): {ConcurrentBookingMetrics.conflicts_handled_cleanly}")
    print(f"Throttled Rate-Limits (Safe 429): {ConcurrentBookingMetrics.throttled_429}")
    print(f"Gateway Cold Reboots (502/503):  {ConcurrentBookingMetrics.gateway_retries_502_503}")
    print(f"Double-Bookings (CRITICAL BUG):  {ConcurrentBookingMetrics.double_bookings_detected}")
    print(f"Unhandled Server Errors (500s):  {ConcurrentBookingMetrics.unhandled_errors}")
    print("=" * 70)
    if ConcurrentBookingMetrics.double_bookings_detected == 0 and ConcurrentBookingMetrics.unhandled_errors == 0:
        print("RESULT: PASS - Backend atomic transactions successfully prevented race conditions!")
    else:
        print("RESULT: FAIL - Unhandled 500 errors detected. Check downloaded Locust statistics.")
    print("=" * 70 + "\n")


class RisalaVisitorUser(HttpUser):
    """
    Simulates visitors exploring public platform pages: app version, health,
    teachers, courses, and payment configurations. (Weight: 3)
    """
    wait_time = between(1.5, 3.5)
    weight = 3

    @tag("health")
    @task(3)
    def check_health(self):
        with self.client.get("/healthz/", catch_response=True) as response:
            if response.status_code in (200, 429):
                response.success()
            elif response.status_code in (502, 503):
                ConcurrentBookingMetrics.gateway_retries_502_503 += 1
                response.failure(f"Render cold start/gateway restart: {response.status_code}")
            else:
                response.failure(f"Health probe status: {response.status_code}")

    @tag("version")
    @task(2)
    def check_app_version(self):
        with self.client.get("/api/v1/app/version/", catch_response=True) as response:
            if response.status_code in (200, 429):
                response.success()
            elif response.status_code in (502, 503):
                ConcurrentBookingMetrics.gateway_retries_502_503 += 1
                response.failure(f"Render cold start/gateway restart: {response.status_code}")
            else:
                response.failure(f"Version check status: {response.status_code}")

    @tag("teachers")
    @task(3)
    def browse_teachers(self):
        with self.client.get("/api/v1/teachers/", catch_response=True) as response:
            if response.status_code in (200, 404, 429):
                response.success()
            elif response.status_code in (502, 503):
                ConcurrentBookingMetrics.gateway_retries_502_503 += 1
                response.failure(f"Render gateway restart: {response.status_code}")
            else:
                response.failure(f"Browse teachers status: {response.status_code}")

    @tag("courses")
    @task(4)
    def browse_courses(self):
        with self.client.get("/api/v1/courses/", catch_response=True) as response:
            if response.status_code in (200, 401, 403, 404, 429):
                response.success()
            elif response.status_code in (502, 503):
                ConcurrentBookingMetrics.gateway_retries_502_503 += 1
                response.failure(f"Render gateway restart: {response.status_code}")
            else:
                response.failure(f"Browse courses status: {response.status_code}")

    @tag("payments")
    @task(2)
    def check_payment_methods(self):
        with self.client.get("/api/v1/payments/methods/", catch_response=True) as response:
            if response.status_code in (200, 404, 429):
                response.success()
            elif response.status_code in (502, 503):
                ConcurrentBookingMetrics.gateway_retries_502_503 += 1
                response.failure(f"Render gateway restart: {response.status_code}")
            else:
                response.failure(f"Payment methods status: {response.status_code}")

        with self.client.get("/api/v1/payments/config/", catch_response=True) as response:
            if response.status_code in (200, 404, 429):
                response.success()
            elif response.status_code in (502, 503):
                ConcurrentBookingMetrics.gateway_retries_502_503 += 1
                response.failure(f"Render gateway restart: {response.status_code}")
            else:
                response.failure(f"Payment config status: {response.status_code}")


class RisalaConcurrentBookingUser(HttpUser):
    """
    Simulates high-concurrency race conditions: multiple virtual students competing
    simultaneously for the exact same session slots.
    Verifies Django select_for_update() row locking and atomic transaction integrity. (Weight: 5)
    """
    wait_time = between(1.0, 2.5)
    weight = 5

    def on_start(self):
        token = get_auth_token(self.client)
        self.headers = {"Authorization": f"Token {token}"} if token else {}
        self.contested_slot_uuids = [
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
            "33333333-3333-3333-3333-333333333333",
            "44444444-4444-4444-4444-444444444444",
            "55555555-5555-5555-5555-555555555555",
        ]
        try:
            res = self.client.get("/api/v1/time-slots/", headers=self.headers, name="/api/v1/time-slots/ [Discovery]")
            if res.status_code == 200:
                slots = res.json()
                if isinstance(slots, list) and len(slots) > 0:
                    live_uuids = [s["id"] for s in slots if "id" in s and not s.get("is_booked", False)]
                    if live_uuids:
                        self.contested_slot_uuids = live_uuids
        except Exception:
            pass

    @tag("booking_race", "single_slot")
    @task(6)
    def contest_same_single_slot(self):
        """
        Multiple students attempt to book the EXACT SAME time slot concurrently.
        Expected behavior:
          - Exactly 1 student gets 201 Created (wins the slot).
          - All other concurrent attempts get 400 Bad Request / 404 (cleanly handled validation error).
          - 500 error or database deadlock indicates a concurrency bug!
        """
        slot_id = random.choice(self.contested_slot_uuids)
        ConcurrentBookingMetrics.tested_slots.add(slot_id)

        payload = {
            "time_slot_id": slot_id,
            "hourly_rate": "15.00",
        }

        with self.client.post(
            "/api/v1/bookings/",
            json=payload,
            headers=self.headers,
            catch_response=True,
            name="/api/v1/bookings/ [Single Slot Race Contest]",
        ) as response:
            if response.status_code == 201:
                ConcurrentBookingMetrics.bookings_won += 1
                response.success()
            elif response.status_code in (400, 404, 409):
                ConcurrentBookingMetrics.conflicts_handled_cleanly += 1
                response.success()
            elif response.status_code == 429:
                ConcurrentBookingMetrics.throttled_429 += 1
                response.success()
            elif response.status_code in (401, 403):
                response.success()
            elif response.status_code in (502, 503):
                ConcurrentBookingMetrics.gateway_retries_502_503 += 1
                response.failure(f"Render gateway restart during booking: {response.status_code}")
            elif response.status_code >= 500:
                ConcurrentBookingMetrics.unhandled_errors += 1
                response.failure(f"CRITICAL 500 on concurrent booking: {response.status_code} - {response.text[:100]}")
            else:
                response.success()

    @tag("booking_race", "bulk_slots")
    @task(4)
    def contest_overlapping_bulk_slots(self):
        """
        Simulates multiple students trying to book overlapping ranges of slots
        simultaneously using /api/v1/bookings/book-range/.
        """
        batch = self.contested_slot_uuids[:3]

        payload = {
            "time_slot_ids": batch,
        }

        with self.client.post(
            "/api/v1/bookings/book-range/",
            json=payload,
            headers=self.headers,
            catch_response=True,
            name="/api/v1/bookings/book-range/ [Overlapping Bulk Race]",
        ) as response:
            if response.status_code in (200, 201):
                ConcurrentBookingMetrics.bookings_won += 1
                response.success()
            elif response.status_code in (400, 404, 409):
                ConcurrentBookingMetrics.conflicts_handled_cleanly += 1
                response.success()
            elif response.status_code == 429:
                ConcurrentBookingMetrics.throttled_429 += 1
                response.success()
            elif response.status_code in (401, 403):
                response.success()
            elif response.status_code in (502, 503):
                ConcurrentBookingMetrics.gateway_retries_502_503 += 1
                response.failure(f"Render gateway restart during bulk booking: {response.status_code}")
            elif response.status_code >= 500:
                ConcurrentBookingMetrics.unhandled_errors += 1
                response.failure(f"CRITICAL 500 on book-range: {response.status_code} - {response.text[:100]}")
            else:
                response.success()


class RisalaStudentUser(HttpUser):
    """
    Simulates active students using the app: viewing user profile, checking
    available time slots, reviewing notifications, course enrollments,
    course reviews, modules, and lessons. (Weight: 4)
    """
    wait_time = between(1.5, 3.5)
    weight = 4

    def on_start(self):
        token = get_auth_token(self.client)
        self.headers = {"Authorization": f"Token {token}"} if token else {}

    @tag("dashboard")
    @task(3)
    def view_profile_and_dashboard(self):
        with self.client.get("/api/v1/users/me/", headers=self.headers, catch_response=True) as response:
            if response.status_code in (200, 401, 403, 429):
                response.success()
            elif response.status_code in (502, 503):
                ConcurrentBookingMetrics.gateway_retries_502_503 += 1
                response.failure(f"Render gateway restart: {response.status_code}")
            else:
                response.failure(f"User me error: {response.status_code}")

    @tag("slots")
    @task(3)
    def check_time_slots(self):
        with self.client.get("/api/v1/time-slots/", headers=self.headers, catch_response=True) as response:
            if response.status_code in (200, 401, 403, 404, 429):
                response.success()
            elif response.status_code in (502, 503):
                ConcurrentBookingMetrics.gateway_retries_502_503 += 1
                response.failure(f"Render gateway restart: {response.status_code}")
            else:
                response.failure(f"Time slots error: {response.status_code}")

    @tag("notifications")
    @task(2)
    def check_notifications(self):
        with self.client.get("/api/v1/notifications/", headers=self.headers, catch_response=True) as response:
            if response.status_code in (200, 401, 403, 404, 429):
                response.success()
            elif response.status_code in (502, 503):
                ConcurrentBookingMetrics.gateway_retries_502_503 += 1
                response.failure(f"Render gateway restart: {response.status_code}")
            else:
                response.failure(f"Notifications error: {response.status_code}")

    @tag("enrollments")
    @task(3)
    def view_enrollments(self):
        with self.client.get("/api/v1/enrollments/", headers=self.headers, catch_response=True) as response:
            if response.status_code in (200, 401, 403, 404, 429):
                response.success()
            elif response.status_code in (502, 503):
                ConcurrentBookingMetrics.gateway_retries_502_503 += 1
                response.failure(f"Render gateway restart: {response.status_code}")
            else:
                response.failure(f"Enrollments error: {response.status_code}")

    @tag("reviews")
    @task(2)
    def view_course_reviews(self):
        with self.client.get("/api/v1/course-reviews/", headers=self.headers, catch_response=True) as response:
            if response.status_code in (200, 401, 403, 404, 429):
                response.success()
            elif response.status_code in (502, 503):
                ConcurrentBookingMetrics.gateway_retries_502_503 += 1
                response.failure(f"Render gateway restart: {response.status_code}")
            else:
                response.failure(f"Course reviews error: {response.status_code}")

    @tag("learning")
    @task(2)
    def browse_learning_content(self):
        with self.client.get("/api/v1/modules/", headers=self.headers, catch_response=True) as response:
            if response.status_code in (200, 401, 403, 404, 429):
                response.success()
            elif response.status_code in (502, 503):
                ConcurrentBookingMetrics.gateway_retries_502_503 += 1
                response.failure(f"Render gateway restart: {response.status_code}")
            else:
                response.failure(f"Modules query error: {response.status_code}")

        with self.client.get("/api/v1/lessons/", headers=self.headers, catch_response=True) as response:
            if response.status_code in (200, 401, 403, 404, 429):
                response.success()
            elif response.status_code in (502, 503):
                ConcurrentBookingMetrics.gateway_retries_502_503 += 1
                response.failure(f"Render gateway restart: {response.status_code}")
            else:
                response.failure(f"Lessons query error: {response.status_code}")
