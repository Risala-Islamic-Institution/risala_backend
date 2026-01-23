"""
API Views for User and Profile models.
"""
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin, UpdateModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet
from rest_framework.permissions import IsAuthenticated

from risala_backend.users.models import User, TeacherProfile, StudentProfile

from .serializers import UserSerializer, TeacherProfileSerializer, StudentProfileSerializer


class UserViewSet(RetrieveModelMixin, ListModelMixin, UpdateModelMixin, GenericViewSet):
    """ViewSet for User operations."""
    serializer_class = UserSerializer
    queryset = User.objects.all()
    lookup_field = "username"
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Filter queryset to only show current user's data."""
        if self.request.user.is_authenticated:
            return self.queryset.filter(id=self.request.user.id)
        return self.queryset.none()

    @action(detail=False, methods=["get"])
    def me(self, request):
        """Get current authenticated user's data."""
        serializer = UserSerializer(request.user, context={"request": request})
        return Response(status=status.HTTP_200_OK, data=serializer.data)
    
    @action(detail=False, methods=["get"])
    def profile(self, request):
        """Get current user's profile (Teacher or Student)."""
        user = request.user
        
        # Check if user is a teacher
        if hasattr(user, "teacher_profile"):
            serializer = TeacherProfileSerializer(user.teacher_profile, context={"request": request})
            return Response({"type": "teacher", "profile": serializer.data})
        
        # Check if user is a student
        if hasattr(user, "student_profile"):
            serializer = StudentProfileSerializer(user.student_profile, context={"request": request})
            return Response({"type": "student", "profile": serializer.data})
        
        return Response(
            {"detail": "No profile found. Please complete registration."},
            status=status.HTTP_404_NOT_FOUND
        )


class TeacherProfileViewSet(RetrieveModelMixin, ListModelMixin, UpdateModelMixin, GenericViewSet):
    """ViewSet for TeacherProfile operations."""
    serializer_class = TeacherProfileSerializer
    queryset = TeacherProfile.objects.filter(profile_visibility=True)
    lookup_field = "id"
    
    def get_queryset(self):
        """Show all visible teacher profiles for browsing."""
        queryset = TeacherProfile.objects.filter(profile_visibility=True)
        
        # Filter by specialization
        specialization = self.request.query_params.get("specialization")
        if specialization:
            queryset = queryset.filter(specialization=specialization)
        
        # Filter by verification status
        verified = self.request.query_params.get("verified")
        if verified == "true":
            queryset = queryset.filter(verification_status="VERIFIED")
        
        return queryset
