"""Management command to ensure production platform admin user exists."""

from django.core.management.base import BaseCommand
from risala_backend.users.models import Role, User, UserRole


class Command(BaseCommand):
    """Command to configure admin superuser credentials."""

    help = "Ensures production admin user exists"

    def handle(self, *args, **options):
        email = "admin@gmail.com"
        password = "0966143454Sahm"

        user, _ = User.objects.get_or_create(
            email=email,
            defaults={
                "username": "admin",
                "full_name": "Platform Administrator",
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
            },
        )
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.set_password(password)
        user.save()

        try:
            admin_role, _ = Role.objects.get_or_create(name="ADMIN")
            UserRole.objects.get_or_create(
                user=user,
                role=admin_role,
            )
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Role assignment note: {e}"))

        msg = f"Successfully configured admin user: {email}"
        self.stdout.write(self.style.SUCCESS(msg))
