"""
Django Admin configuration for User and Profile models.
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import (
    User,
    Role,
    Permission,
    RolePermission,
    UserRole,
    TeacherProfile,
    StudentProfile,
    TeacherAvailability,
    SessionBooking,
)


class UserRoleInline(admin.TabularInline):
    model = UserRole
    extra = 1
    fk_name = "user"
    autocomplete_fields = ["role"]


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ["email", "username", "full_name", "is_active", "is_staff", "created_at"]
    list_filter = ["is_active", "is_staff", "is_superuser", "is_suspended"]
    search_fields = ["email", "username", "full_name"]
    ordering = ["-created_at"]
    inlines = [UserRoleInline]
    
    fieldsets = (
        (None, {"fields": ("email", "username", "password")}),
        (_("Personal Info"), {"fields": ("full_name", "phone_number", "gender", "date_of_birth", "country")}),
        # Field is named user_timezone in the model; keep admin aligned to avoid FieldError.
        (_("Preferences"), {"fields": ("user_timezone", "preferred_language")}),
        (_("Status"), {"fields": ("is_active", "is_staff", "is_superuser", "is_suspended", "suspension_reason")}),
        (_("Important dates"), {"fields": ("last_login", "date_joined", "last_login_at")}),
    )
    
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "username", "password1", "password2"),
        }),
    )


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ["name", "description", "created_at"]
    search_fields = ["name"]



@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ["code", "scope", "description"]
    list_filter = ["scope"]
    search_fields = ["code", "description"]


@admin.register(TeacherProfile)
class TeacherProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "specialization", "verification_status", "hourly_rate", "rating_average"]
    list_filter = ["verification_status", "specialization", "teaching_level"]
    search_fields = ["user__email", "user__full_name"]
    raw_id_fields = ["user", "verified_by"]


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "current_level", "enrollment_status", "joined_at"]
    list_filter = ["current_level", "enrollment_status"]
    search_fields = ["user__email", "user__full_name"]
    raw_id_fields = ["user"]


@admin.register(TeacherAvailability)
class TeacherAvailabilityAdmin(admin.ModelAdmin):
    list_display = ("teacher", "day_of_week", "start_time", "end_time", "timezone", "is_active")
    list_filter = ("teacher", "day_of_week", "timezone", "is_active")


@admin.register(SessionBooking)
class SessionBookingAdmin(admin.ModelAdmin):
    list_display = ("teacher", "student", "start_at", "end_at", "status")
    list_filter = ("teacher", "student", "status")
