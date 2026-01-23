import pytest
from risala_backend.users.models import User
import uuid

@pytest.mark.django_db
class TestUserModel:
    def test_user_has_uuid_pk(self, user: User):
        """Test that the user model uses UUID as primary key."""
        assert isinstance(user.pk, uuid.UUID)

    def test_user_has_timestamps(self, user: User):
        """Test that the user model has created_at and updated_at fields."""
        assert hasattr(user, "created_at")
        assert hasattr(user, "updated_at")

    def test_user_has_role_field(self, user: User):
        """Test that the user model has a role field."""
        assert hasattr(user, "role")
        
    def test_role_choices(self):
        """Test that the User model has the correct role choices."""
        assert User.Role.ADMIN == "ADMIN"
        assert User.Role.STUDENT == "STUDENT"
        assert User.Role.INSTRUCTOR == "INSTRUCTOR"
        assert User.Role.SUPPORT == "SUPPORT"
        assert User.Role.FINANCE == "FINANCE"
