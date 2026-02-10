from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('courses', '0003_course_created_by_course_duration_type_course_level_and_more'),
        ('users', '0003_notification'),
    ]

    operations = [
        migrations.CreateModel(
            name='Enrollment',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('status', models.CharField(choices=[('ENROLLED', 'Enrolled'), ('COMPLETED', 'Completed'), ('CANCELLED', 'Cancelled')], default='ENROLLED', max_length=20)),
                ('progress_percent', models.PositiveIntegerField(default=0)),
                ('course', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='enrollments', to='courses.course')),
                ('student', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='enrollments', to='users.studentprofile')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AlterUniqueTogether(
            name='enrollment',
            unique_together={('course', 'student')},
        ),
    ]
