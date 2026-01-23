from django.contrib import admin
from .models import Course, CourseModule, Lesson


class LessonInline(admin.TabularInline):
    model = Lesson
    extra = 1


class CourseModuleInline(admin.TabularInline):
    model = CourseModule
    extra = 1


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ["title", "created_by", "category", "level", "is_published", "price", "created_at"]
    list_filter = ["is_published", "category", "level", "created_at"]
    search_fields = ["title", "description", "created_by__user__email"]
    prepopulated_fields = {"slug": ("title",)}
    inlines = [CourseModuleInline]


@admin.register(CourseModule)
class CourseModuleAdmin(admin.ModelAdmin):
    list_display = ["title", "course", "order_index", "is_mandatory"]
    list_filter = ["course", "is_mandatory"]
    inlines = [LessonInline]


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ["title", "module", "lesson_type", "order", "duration_minutes"]
    list_filter = ["lesson_type", "module__course"]
