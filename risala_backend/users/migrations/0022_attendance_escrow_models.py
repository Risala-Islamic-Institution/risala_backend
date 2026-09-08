# Generated migration for SessionAttendance, AttendanceHeartbeat, TeacherPayoutLedger, SessionExcuse, and TeacherProfile audition fields

from decimal import Decimal
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0021_backfill_roles_and_profiles'),
    ]

    operations = [
        # 1. Add audition fields to TeacherProfile
        migrations.AddField(
            model_name='teacherprofile',
            name='audition_notes',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='teacherprofile',
            name='recitation_score',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),

        # 2. Create SessionAttendance model
        migrations.CreateModel(
            name='SessionAttendance',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('teacher_joined_at', models.DateTimeField(blank=True, null=True)),
                ('teacher_left_at', models.DateTimeField(blank=True, null=True)),
                ('teacher_minutes_present', models.PositiveIntegerField(default=0)),
                ('teacher_heartbeat_count', models.PositiveIntegerField(default=0)),
                ('student_joined_at', models.DateTimeField(blank=True, null=True)),
                ('student_left_at', models.DateTimeField(blank=True, null=True)),
                ('student_minutes_present', models.PositiveIntegerField(default=0)),
                ('student_heartbeat_count', models.PositiveIntegerField(default=0)),
                ('verdict', models.CharField(choices=[('PENDING', 'Pending / In Progress'), ('VERIFIED_COMPLETE', 'Verified Complete (Both Attended)'), ('TEACHER_ABSENT', 'Teacher Absent (No-Show)'), ('STUDENT_ABSENT', 'Student Absent (No-Show)'), ('PARTIAL_DISPUTED', 'Partial / Disputed'), ('EXCUSED', 'Excused & Rescheduled')], default='PENDING', max_length=30)),
                ('settlement_status', models.CharField(choices=[('HELD_IN_ESCROW', 'Held in Escrow'), ('RELEASED_TO_TEACHER', 'Released to Teacher'), ('REFUNDED_TO_STUDENT', 'Refunded to Student'), ('CREDITED_RESCHEDULE', 'Credited for Reschedule')], default='HELD_IN_ESCROW', max_length=30)),
                ('admin_notes', models.TextField(blank=True, default='')),
                ('evaluated_at', models.DateTimeField(blank=True, null=True)),
                ('booking', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='attendance', to='users.sessionbooking')),
            ],
            options={
                'verbose_name': 'Session Attendance',
                'verbose_name_plural': 'Session Attendances',
                'ordering': ['-created_at'],
            },
        ),

        # 3. Create AttendanceHeartbeat model
        migrations.CreateModel(
            name='AttendanceHeartbeat',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('role', models.CharField(max_length=20)),
                ('event_type', models.CharField(choices=[('JOIN', 'User Joined Room'), ('PING', 'Periodic Heartbeat (every 2-3 min)'), ('LEAVE', 'User Left Room')], default='PING', max_length=20)),
                ('is_mic_active', models.BooleanField(default=True)),
                ('is_camera_active', models.BooleanField(default=True)),
                ('other_participant_detected', models.BooleanField(default=False)),
                ('attendance', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='heartbeats', to='users.sessionattendance')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['created_at'],
            },
        ),

        # 4. Create TeacherPayoutLedger model
        migrations.CreateModel(
            name='TeacherPayoutLedger',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('gross_amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
                ('platform_fee_percent', models.DecimalField(decimal_places=2, default=Decimal('10.00'), max_digits=5)),
                ('platform_fee_amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
                ('teacher_net_amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
                ('status', models.CharField(choices=[('HELD', 'Held in Escrow'), ('PAYABLE', 'Payable to Teacher'), ('SETTLED', 'Settled / Disbursed'), ('REFUNDED', 'Refunded to Student'), ('CANCELLED', 'Cancelled')], default='HELD', max_length=20)),
                ('disbursed_at', models.DateTimeField(blank=True, null=True)),
                ('notes', models.TextField(blank=True, default='')),
                ('booking', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='payout_ledger', to='users.sessionbooking')),
                ('teacher', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='payout_ledgers', to='users.teacherprofile')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),

        # 5. Create SessionExcuse model
        migrations.CreateModel(
            name='SessionExcuse',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('reason', models.CharField(max_length=255)),
                ('explanation', models.TextField()),
                ('status', models.CharField(choices=[('PENDING', 'Pending Admin Review'), ('APPROVED', 'Approved (Reschedule Allowed)'), ('REJECTED', 'Rejected')], default='PENDING', max_length=20)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('review_notes', models.TextField(blank=True, default='')),
                ('booking', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='excuses', to='users.sessionbooking')),
                ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reviewed_excuses', to=settings.AUTH_USER_MODEL)),
                ('submitted_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
