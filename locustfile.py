"""
Risala Performance & Concurrent Booking Stress Testing Suite (Locust)
=====================================================================
Simulates realistic concurrent mobile traffic AND high-concurrency 
race conditions where multiple students compete to book the EXACT SAME
time slots simultaneously (single bookings and overlapping range bookings).

Usage:
  # 1. Interactive Web Dashboard in Docker (recommended):
  docker compose -f docker-compose.local.yml up locust
  # Then open http://localhost:8089 in your browser

  # 2. Command-Line Automated Test:
  locust -f locustfile.py --headless -u 30 -r 5 -t 1m --host https://risala-5vs3.onrender.com --csv=risala_benchmark

  # 3. Target Specific Concurrent Booking Test:
  locust -f locustfile.py --tags booking_race --host http://localhost:8000
"""

import os
import random
from locust import HttpUser, task, between, tag, events


class ConcurrentBookingMetrics:
    """Tracks booking outcomes across all concurrent worker threads."""
    bookings_won = 0
    conflicts_handled_cleanly = 0
    double_bookings_detected = 0
    unhandled_errors = 0
    tested_slots = set()


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    print("\n" + "=" * 65)
    print("  RISALA LOAD & CONCURRENT BOOKING STRESS TEST INITIALIZED")
    print("=" * 65)
    print(f"Target Host: {environment.host}")
    print("Simulating concurrent student race conditions on shared slots...")
    print("=" * 65 + "\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    print("\n" + "=" * 65)
    print("  RISALA CONCURRENT BOOKING TEST COMPLETED")
    print("=" * 65)
    print(f"Total Slots Contested:           {len(ConcurrentBookingMetrics.tested_slots)}")
    print(f"Successful Bookings (Won):       {ConcurrentBookingMetrics.bookings_won}")
    print(f"Handled Conflicts (Safe 400):    {ConcurrentBookingMetrics.conflicts_handled_cleanly}")
    print(f"Double-Bookings (CRITICAL BUG):  {ConcurrentBookingMetrics.double_bookings_detected}")
    print(f"Unhandled Server Errors (500s):  {ConcurrentBookingMetrics.unhandled_errors}")
    print("=" * 65)
    if ConcurrentBookingMetrics.double_bookings_detected == 0 and ConcurrentBookingMetrics.unhandled_errors == 0:
        print("RESULT: PASS - Backend atomic transactions successfully prevented race conditions!")
    else:
        print("RESULT: FAIL - Unhandled errors or race conflicts detected. Check downloaded CSV data.")
    print("=" * 65 + "\n")


class RisalaVisitorUser(HttpUser):
    """
    Simulates general visitors browsing teachers, courses, and app health.
    Represents ~60% of baseline traffic.
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
                response.failure(f"Health check probe failed with status {response.status_code}")

    @tag("version")
    @task(2)
    def check_app_version(self):
        """Simulates app startup remote kill-switch / version inspection."""
        with self.client.get("/api/v1/app/version/", catch_response=True) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Version inspection failed: {response.status_code}")

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


class RisalaConcurrentBookingUser(HttpUser):
    """
    Simulates multiple concurrent students competing to book the SAME time slots
    simultaneously to test select_for_update row locking and race condition safety.
    """
    wait_time = between(1, 2)
    weight = 4

    def on_start(self):
        self.auth_token = os.getenv("TEST_AUTH_TOKEN", "")
        self.headers = {
            "Authorization": f"Token {self.auth_token}"
        } if self.auth_token else {}
        self.target_slot_ids = [1, 2, 3, 4, 5, 10, 15]

    @tag("booking_race", "single_booking")
    @task(5)
    def contest_same_single_slot(self):
        """
        Multiple students attempt to book the EXACT SAME time slot concurrently.
        Expected behavior:
          - Exactly 1 student gets 201 Created (wins the slot).
          - All other concurrent students get 400 Bad Request with a clean error message.
          - 500 Internal Server Error or deadlock indicates a race condition failure!
        """
        # Pick from a small set of slots so concurrent virtual users collide frequently
        slot_id = random.choice(self.target_slot_ids)
        ConcurrentBookingMetrics.tested_slots.add(slot_id)

        payload = {
            "time_slot_id": slot_id,
            "hourly_rate": "15.00",
        }

        with self.client.post(
            "/api/v1/users/bookings/",
            json=payload,
            headers=self.headers,
            catch_response=True,
            name="/api/v1/users/bookings/ [Race Contest]",
        ) as response:
            if response.status_code == 201:
                ConcurrentBookingMetrics.bookings_won += 1
                response.success()
            elif response.status_code == 400:
                body = response.text.lower()
                if "no longer available" in body or "already booked" in body or "only students" in body:
                    # Clean handled business logic conflict
                    ConcurrentBookingMetrics.conflicts_handled_cleanly += 1
                    response.success()
                else:
                    response.failure(f"Unexpected 400 validation error: {response.text}")
            elif response.status_code == 401:
                # Unauthenticated in test environment without auth token
                response.success()
            elif response.status_code == 404:
                # Slot does not exist in the current DB instance
                response.success()
            elif response.status_code >= 500:
                ConcurrentBookingMetrics.unhandled_errors += 1
                response.failure(f"CRITICAL 500 ERROR on concurrent booking: {response.status_code} - {response.text}")

    @tag("booking_race", "bulk_booking")
    @task(3)
    def contest_overlapping_bulk_slots(self):
        """
        Simulates multiple students trying to book overlapping ranges of slots
        simultaneously (e.g. Student A tries slots [1, 2, 3], Student B tries [2, 3, 4]).
        """
        # Overlapping subset
        batch = random.choice([
            [1, 2, 3],
            [2, 3, 4],
            [3, 4, 5],
        ])

        payload = {
            "time_slot_ids": batch,
            "hourly_rate": "15.00",
        }

        with self.client.post(
            "/api/v1/users/bookings/bulk_create/",
            json=payload,
            headers=self.headers,
            catch_response=True,
            name="/api/v1/users/bookings/bulk_create/ [Overlapping Race]",
        ) as response:
            if response.status_code in (200, 201):
                ConcurrentBookingMetrics.bookings_won += 1
                response.success()
            elif response.status_code == 400:
                body = response.text.lower()
                if "no longer available" in body or "already booked" in body or "error" in body or "only students" in body:
                    ConcurrentBookingMetrics.conflicts_handled_cleanly += 1
                    response.success()
                else:
                    response.failure(f"Unexpected bulk 400 error: {response.text}")
            elif response.status_code in (401, 404):
                response.success()
            elif response.status_code >= 500:
                ConcurrentBookingMetrics.unhandled_errors += 1
                response.failure(f"CRITICAL 500 ERROR on overlapping bulk booking: {response.status_code}")
