from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import CharField, TextChoices
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from risala_backend.utils.models import TimeStampedModel, UUIDModel


class User(AbstractUser, TimeStampedModel, UUIDModel):
    """
    Default custom user model for Risala_Backend.
    If adding fields that need to be filled at user signup,
    check forms.SignupForm and forms.SocialSignupForms accordingly.
    """
    class Role(TextChoices):
        ADMIN = "ADMIN", _("Admin")
        STUDENT = "STUDENT", _("Student")
        INSTRUCTOR = "INSTRUCTOR", _("Instructor")
        SUPPORT = "SUPPORT", _("Support")
        FINANCE = "FINANCE", _("Finance")

    # First and last name do not cover name patterns around the globe
    name = CharField(_("Name of User"), blank=True, max_length=255)
    first_name = None  # type: ignore[assignment]
    last_name = None  # type: ignore[assignment]
    
    role = CharField(
        max_length=50, 
        choices=Role.choices, 
        default=Role.STUDENT
    )

    def get_absolute_url(self) -> str:
        """Get URL for user's detail view.

        Returns:
            str: URL for user detail.

        """
        return reverse("users:detail", kwargs={"username": self.username})


class StudentProfile(TimeStampedModel, UUIDModel):
    """Profile for learners/students."""
    user = models.OneToOneField(
        User, 
        on_delete=models.CASCADE, 
        related_name="student_profile"
    )
    bio = models.TextField(blank=True, default="")
    learning_goals = models.TextField(blank=True, default="")

    def __str__(self):
        return f"StudentProfile: {self.user.username}"


class InstructorProfile(TimeStampedModel, UUIDModel):
    """Profile for instructors/teachers."""
    user = models.OneToOneField(
        User, 
        on_delete=models.CASCADE, 
        related_name="instructor_profile"
    )
    bio = models.TextField(blank=True, default="")
    qualifications = models.TextField(blank=True, default="")
    expertise = models.CharField(max_length=255, blank=True, default="")

    def __str__(self):
        return f"InstructorProfile: {self.user.username}"


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Auto-create profile based on user role."""
    if created:
        if instance.role == User.Role.STUDENT:
            StudentProfile.objects.create(user=instance)
        elif instance.role == User.Role.INSTRUCTOR:
            InstructorProfile.objects.create(user=instance)

