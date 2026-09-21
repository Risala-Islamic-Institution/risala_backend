"""
Management command to create benchmark student & teacher with active time slots.
Generates an authenticated DRF Token for high-concurrency stress testing in Locust.
"""

from datetime import timedelta
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone
from rest_framework.authtoken.models import Token

from risala_backend.users.models import (
    Role,
    User,
    UserRole,
    StudentProfile,
    TeacherProfile,
    TimeSlot,
)


class Command(BaseCommand):
    help = "Creates a benchmark student, teacher, and future time slots for concurrency load testing."

    def handle(self, *args, **options):
        # 1. Ensure Roles Exist
        student_role, _ = Role.objects.get_or_create(name=Role.RoleName.STUDENT)
        teacher_role, _ = Role.objects.get_or_create(name=Role.RoleName.USTAZ)

        # 2. Setup Benchmark Student
        student_email = "benchmark_student@risala.com"
        student_password = "BenchmarkPassword123!"
        student_user, _ = User.objects.get_or_create(
            email=student_email,
            defaults={
                "username": "benchmark_student",
                "full_name": "Benchmark LoadTest Student",
                "is_active": True,
            },
        )
        student_user.set_password(student_password)
        student_user.save()
        UserRole.objects.get_or_create(user=student_user, role=student_role)
        student_profile, _ = StudentProfile.objects.get_or_create(user=student_user)

        # 3. Create or Get DRF Token for Student
        token, _ = Token.objects.get_or_create(user=student_user)

        # 4. Setup Benchmark Teacher
        teacher_email = "benchmark_teacher@risala.com"
        teacher_user, _ = User.objects.get_or_create(
            email=teacher_email,
            defaults={
                "username": "benchmark_teacher",
                "full_name": "Ustaz Benchmark Teacher",
                "is_active": True,
            },
        )
        teacher_user.set_password("BenchmarkTeacher123!")
        teacher_user.save()
        UserRole.objects.get_or_create(user=teacher_user, role=teacher_role)
        teacher_profile, _ = TeacherProfile.objects.get_or_create(
            user=teacher_user,
            defaults={
                "specialization": "TAJWEED",
                "hourly_rate": Decimal("20.00"),
                "verification_status": "VERIFIED",
                "profile_visibility": True,
            },
        )

        # 5. Create 10 Future Time Slots for Concurrency Testing
        now = timezone.now()
        base_time = (now + timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
        created_slot_ids = []

        for i in range(10):
            start = base_time + timedelta(hours=i * 2)
            end = start + timedelta(hours=1)
            slot, created = TimeSlot.objects.get_or_create(
                teacher=teacher_profile,
                start_time=start,
                defaults={
                    "end_time": end,
                    "is_booked": False,
                    "allowed_booking_type": TimeSlot.BookingType.BOTH,
                },
            )
            # Reset if already booked in a previous run
            if slot.is_booked:
                slot.is_booked = False
                slot.booking = None
                slot.save(update_fields=["is_booked", "booking", "updated_at"])

            created_slot_ids.append(str(slot.id))

        self.stdout.write(self.style.SUCCESS("=" * 65))
        self.stdout.write(self.style.SUCCESS("  BENCHMARK DATA INITIALIZED SUCCESSFULLY!"))
        self.stdout.write(self.style.SUCCESS("=" * 65))
        self.stdout.write(f"Student Email:    {student_email}")
        self.stdout.write(f"Student Password: {student_password}")
        self.stdout.write(self.style.WARNING(f"STUDENT AUTH TOKEN: {token.key}"))
        self.stdout.write(f"Contested Slot IDs: {', '.join(created_slot_ids[:5])}")
        self.stdout.write(self.style.SUCCESS("=" * 65))
        self.stdout.write("To test in Locust:")
        self.stdout.write(f"Set environment variable: TEST_AUTH_TOKEN={token.key}")
        self.stdout.write("Or paste this token directly into the Locust Web UI.")
        self.stdout.write(self.style.SUCCESS("=" * 65))
