import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
django.setup()

from risala_backend.courses.api.serializers import CourseSerializer
from risala_backend.users.models import User, TeacherProfile
from rest_framework.exceptions import ValidationError
import time

teacher = TeacherProfile.objects.first()
if not teacher:
    print("No teacher found.")
else:
    class DummyRequest:
        user = teacher.user
        
    serializer = CourseSerializer(data={
        "title": f"New Course Draft {time.time()}",
        "description": "Description here...",
        "category": "QURAN",
        "price": "0.00",
        "is_published": False
    }, context={"request": DummyRequest()})
    
    if serializer.is_valid():
        try:
            instance = serializer.save()
            print("Successfully created!", instance.slug)
        except ValidationError as e:
            print("Validation Error on Save:", e.detail)
        except Exception as e:
            print("Other Exception on Save:", type(e), e)
    else:
        print("INVALID! Errors:", serializer.errors)
