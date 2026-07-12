from django.db import migrations


def backfill_roles_and_profiles(apps, schema_editor):
    """Reconcile roles and profiles for accounts created before role/profile
    assignment was wired into registration.

    Historically, registrations that went through dj-rest-auth's default
    serializer created a User with no Role and no profile. Such accounts show
    up in the app as students (empty roles -> student by default) but are
    rejected by student-only endpoints (booking, enrollment, ...) because they
    have no StudentProfile, producing an "only students can book" inconsistency.

    This migration makes the data consistent:
      * users that already hold the STUDENT / USTAZ role but are missing the
        matching profile get one created;
      * self-registered users left without any role (a pre-fix artifact) are
        treated as students -- matching how the app already presents them --
        and given a StudentProfile. Staff / superuser accounts are left alone.

    Note: the post_save signal that normally auto-creates profiles does not
    fire for the historical UserRole model used inside migrations, so profiles
    are created explicitly here.
    """
    User = apps.get_model("users", "User")
    Role = apps.get_model("users", "Role")
    UserRole = apps.get_model("users", "UserRole")
    StudentProfile = apps.get_model("users", "StudentProfile")
    TeacherProfile = apps.get_model("users", "TeacherProfile")

    student_role = None

    for user in User.objects.all():
        role_names = set(
            UserRole.objects.filter(user=user).values_list("role__name", flat=True)
        )

        # Role-less, self-registered accounts default to STUDENT (matches the
        # app, which shows an empty-roles user the student dashboard).
        if not role_names and not user.is_staff and not user.is_superuser:
            if student_role is None:
                student_role, _ = Role.objects.get_or_create(
                    name="STUDENT",
                    defaults={"description": "STUDENT role"},
                )
            UserRole.objects.get_or_create(user=user, role=student_role)
            role_names.add("STUDENT")

        # Ensure the profile matching each held role exists.
        if "STUDENT" in role_names:
            StudentProfile.objects.get_or_create(user=user)
        if "USTAZ" in role_names:
            TeacherProfile.objects.get_or_create(user=user)


def noop(apps, schema_editor):
    """Reverse migration: nothing to undo (creating profiles/roles is
    non-destructive and safe to keep)."""


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0020_alter_sessionbooking_status"),
    ]

    operations = [
        migrations.RunPython(backfill_roles_and_profiles, noop),
    ]
