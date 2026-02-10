from rest_framework import serializers
from risala_backend.courses.models import Course, CourseModule, Lesson, Enrollment, LessonProgress, Certificate, QuizQuestion, QuizAttempt, QuizAnswer, CourseReview, CourseAnnouncement, CourseQuestion, CourseAnswer
from risala_backend.users.models import TeacherProfile, StudentProfile
from risala_backend.users.api.serializers import UserSerializer
from risala_backend.users.models import Notification


class LessonSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lesson
        fields = [
            "id",
            "title",
            "lesson_type",
            "content_reference",
            "duration_minutes",
            "requires_attendance",
            "is_free_preview",
            "is_free_preview",
            "order",
            "start_marker",
            "end_marker",
        ]
        read_only_fields = ["id"]


class CourseModuleSerializer(serializers.ModelSerializer):
    lessons = LessonSerializer(many=True, read_only=True)

    class Meta:
        model = CourseModule
        fields = [
            "id",
            "title",
            "order_index",
            "learning_objectives",
            "estimated_duration",
            "is_mandatory",
            "is_mandatory",
            "file",
            "lessons",
        ]
        read_only_fields = ["id", "lessons"]


class CourseSerializer(serializers.ModelSerializer):
    created_by = UserSerializer(source="created_by.user", read_only=True)
    modules = CourseModuleSerializer(many=True, read_only=True)

    class Meta:
        model = Course
        fields = [
            "id",
            "title",
            "slug",
            "description",
            "category",
            "level",
            "duration_type",
            "total_weeks",
            "syllabus",
            "prerequisites",
            "created_by",
            "thumbnail",
            "is_published",
            "price",
            "modules",
            "created_at",
        ]
        read_only_fields = ["id", "slug", "created_by", "modules", "created_at"]

    def create(self, validated_data):
        request = self.context.get("request")
        teacher_profile = getattr(request.user, "teacher_profile", None) if request else None
        if not teacher_profile:
            raise serializers.ValidationError("Only teachers can create courses.")
        validated_data["created_by"] = teacher_profile
        return super().create(validated_data)

    def update(self, instance, validated_data):
        request = self.context.get("request")
        teacher_profile = getattr(request.user, "teacher_profile", None) if request else None
        if not teacher_profile or instance.created_by != teacher_profile:
            raise serializers.ValidationError("Only the owner teacher can update this course.")
        return super().update(instance, validated_data)


class EnrollmentSerializer(serializers.ModelSerializer):
    course = CourseSerializer(read_only=True)
    course_id = serializers.PrimaryKeyRelatedField(
        queryset=Course.objects.filter(is_published=True), source="course", write_only=True
    )

    class Meta:
        model = Enrollment
        fields = [
            "id",
            "course",
            "course_id",
            "status",
            "progress_percent",
            "created_at",
        ]
        read_only_fields = ["id", "status", "progress_percent", "created_at", "course"]

    def create(self, validated_data):
        request = self.context.get("request")
        student_profile = getattr(request.user, "student_profile", None) if request else None
        if not student_profile:
            raise serializers.ValidationError("Only students can enroll.")
        validated_data["student"] = student_profile
        enrollment = super().create(validated_data)
        # Notify teacher about new enrollment
        teacher_user = getattr(enrollment.course.created_by, "user", None)
        if teacher_user:
            Notification.objects.create(
                user=teacher_user,
                title="New course enrollment",
                body=f"A student enrolled in {enrollment.course.title}.",
            )
        return enrollment


class EnrollmentUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Enrollment
        fields = ["status", "progress_percent"]
        read_only_fields = []

    def update(self, instance, validated_data):
        request = self.context.get("request")
        student_profile = getattr(request.user, "student_profile", None) if request else None
        if not student_profile or instance.student != student_profile:
            raise serializers.ValidationError("Only the enrolled student can update progress.")
        # Only allow progress/status transitions for the student
        return super().update(instance, validated_data)


class LessonProgressSerializer(serializers.ModelSerializer):
    enrollment_id = serializers.PrimaryKeyRelatedField(queryset=Enrollment.objects.all(), source="enrollment", write_only=True)
    lesson_id = serializers.PrimaryKeyRelatedField(queryset=Lesson.objects.all(), source="lesson", write_only=True)

    class Meta:
        model = LessonProgress
        fields = [
            "id",
            "enrollment_id",
            "lesson_id",
            "is_completed",
            "completed_at",
            "score",
            "time_spent_minutes",
        ]
        read_only_fields = ["id", "completed_at"]

    def create(self, validated_data):
        request = self.context.get("request")
        student_profile = getattr(request.user, "student_profile", None) if request else None
        enrollment = validated_data.get("enrollment")
        if not student_profile or not enrollment or enrollment.student != student_profile:
            raise serializers.ValidationError("Only the enrolled student can update lesson progress.")

        # Upsert behavior: if a record exists, update it.
        lesson = validated_data.get("lesson")
        instance, created = LessonProgress.objects.get_or_create(enrollment=enrollment, lesson=lesson, defaults=validated_data)
        if not created:
            for k, v in validated_data.items():
                setattr(instance, k, v)
            instance.save()
        return instance

    def update(self, instance, validated_data):
        request = self.context.get("request")
        student_profile = getattr(request.user, "student_profile", None) if request else None
        if not student_profile or instance.enrollment.student != student_profile:
            raise serializers.ValidationError("Only the enrolled student can update lesson progress.")
        return super().update(instance, validated_data)


class CertificateSerializer(serializers.ModelSerializer):
    course_title = serializers.CharField(source="enrollment.course.title", read_only=True)
    course_slug = serializers.CharField(source="enrollment.course.slug", read_only=True)

    class Meta:
        model = Certificate
        fields = [
            "id",
            "enrollment",
            "issued_at",
            "code",
            "course_title",
            "course_slug",
        ]
        read_only_fields = ["id", "enrollment", "issued_at", "code", "course_title", "course_slug"]


class QuizQuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuizQuestion
        fields = [
            "id",
            "text",
            "option_a",
            "option_b",
            "option_c",
            "option_d",
        ]
        read_only_fields = ["id"]


class QuizAttemptSerializer(serializers.ModelSerializer):
    answers = serializers.ListField(child=serializers.DictField(), write_only=True)

    class Meta:
        model = QuizAttempt
        fields = [
            "id",
            "enrollment",
            "lesson",
            "score",
            "is_passed",
            "submitted_at",
            "answers",
        ]
        read_only_fields = ["id", "score", "is_passed", "submitted_at"]

    def validate(self, attrs):
        enrollment = attrs.get("enrollment")
        lesson = attrs.get("lesson")
        request = self.context.get("request")
        student_profile = getattr(request.user, "student_profile", None) if request else None
        if not enrollment or not lesson:
            raise serializers.ValidationError("enrollment and lesson are required")
        if not student_profile or enrollment.student != student_profile:
            raise serializers.ValidationError("Only the enrolled student can submit quizzes.")
        if lesson.lesson_type != Lesson.LessonType.QUIZ:
            raise serializers.ValidationError("Lesson is not a quiz.")
        return attrs

    def create(self, validated_data):
        answers = validated_data.pop("answers", [])
        attempt = QuizAttempt.objects.create(**validated_data)
        # Evaluate answers
        total = 0
        correct = 0
        for item in answers:
            qid = item.get("question_id")
            sel = item.get("selected_option")
            if not qid or not sel:
                continue
            try:
                q = QuizQuestion.objects.get(id=qid, lesson=attempt.lesson)
            except QuizQuestion.DoesNotExist:
                continue
            total += 1
            QuizAnswer.objects.create(attempt=attempt, question=q, selected_option=str(sel).upper()[:1])
            if str(sel).upper()[:1] == q.correct_option:
                correct += 1
        score_percent = int(round((correct / max(1, total)) * 100))
        attempt.score = score_percent
        pass_required = attempt.lesson.pass_percent
        attempt.is_passed = score_percent >= pass_required
        attempt.save(update_fields=["score", "is_passed"])
        # If passed, mark lesson complete for enrollment
        if attempt.is_passed:
            LessonProgress.objects.get_or_create(enrollment=attempt.enrollment, lesson=attempt.lesson, defaults={"is_completed": True})
            try:
                lp = LessonProgress.objects.get(enrollment=attempt.enrollment, lesson=attempt.lesson)
                lp.is_completed = True
                lp.save()
            except Exception:
                pass
        return attempt


class CourseReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseReview
        fields = ["id", "enrollment", "rating", "comment", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate(self, attrs):
        enrollment = attrs.get("enrollment")
        request = self.context.get("request")
        student_profile = getattr(request.user, "student_profile", None) if request else None
        if not enrollment or not student_profile or enrollment.student != student_profile:
            raise serializers.ValidationError("Only the enrolled student can review the course.")
        return attrs


class CourseAnnouncementSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseAnnouncement
        fields = ["id", "course", "title", "body", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate(self, attrs):
        course = attrs.get("course")
        request = self.context.get("request")
        teacher_profile = getattr(request.user, "teacher_profile", None) if request else None
        if not course or not teacher_profile or course.created_by != teacher_profile:
            raise serializers.ValidationError("Only the course owner can post announcements.")
        return attrs


class CourseQuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseQuestion
        fields = ["id", "course", "student", "body", "created_at"]
        read_only_fields = ["id", "student", "created_at"]

    def create(self, validated_data):
        request = self.context.get("request")
        student_profile = getattr(request.user, "student_profile", None) if request else None
        if not student_profile:
            raise serializers.ValidationError("Only students can ask questions.")
        validated_data["student"] = student_profile
        return super().create(validated_data)


class CourseAnswerSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseAnswer
        fields = ["id", "question", "teacher", "body", "created_at"]
        read_only_fields = ["id", "teacher", "created_at"]

    def create(self, validated_data):
        request = self.context.get("request")
        teacher_profile = getattr(request.user, "teacher_profile", None) if request else None
        question = validated_data.get("question")
        if not teacher_profile or not question or question.course.created_by != teacher_profile:
            raise serializers.ValidationError("Only the course owner can answer questions.")
        validated_data["teacher"] = teacher_profile
        return super().create(validated_data)
