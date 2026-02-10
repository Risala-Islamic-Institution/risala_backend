import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
django.setup()

from risala_backend.courses.models import Course
from risala_backend.users.models import User, TeacherProfile

def create_course():
    try:
        # Ensure we have a teacher
        u, _ = User.objects.get_or_create(email="teacher@example.com", defaults={"full_name": "Debug Teacher"})
        t, _ = TeacherProfile.objects.get_or_create(user=u)
        
        print(f"Creating course for teacher {t}")
        c = Course.objects.create(
            title="Debug Course",
            description="Testing creation",
            created_by=t
        )
        print(f"Course created: {c.id} - {c.slug}")
    except Exception as e:
        print(f"Failed to create course: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    create_course()
