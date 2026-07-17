from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Submissions', '0032_team_preference_max_length_500'),
    ]

    operations = [
        migrations.CreateModel(
            name='AdminTempAccess',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('student_email', models.EmailField(max_length=255)),
                ('generated_by', models.EmailField(max_length=255)),
                ('temp_password_hash', models.CharField(max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('is_used', models.BooleanField(default=False)),
                ('used_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'db_table': 'submissions_admin_temp_access',
            },
        ),
        migrations.AddIndex(
            model_name='admintempaccess',
            index=models.Index(fields=['student_email', 'is_used'], name='submissions_admin_temp_email_used_idx'),
        ),
    ]
