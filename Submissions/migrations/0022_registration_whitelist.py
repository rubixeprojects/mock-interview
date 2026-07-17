# Generated migration for RegistrationWhitelist model
# This migration creates the registration whitelist table for controlling course access

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Submissions', '0021_add_status_updated_at_column'),
    ]

    operations = [
        migrations.CreateModel(
            name='RegistrationWhitelist',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(db_column='email', max_length=255)),
                ('course', models.CharField(db_column='course', max_length=120)),
                ('added_at', models.DateTimeField(
                    auto_now_add=True,
                    db_column='added_at',
                    help_text='Timestamp when entry was added'
                )),
            ],
            options={
                'db_table': 'submissions_registration_whitelist',
            },
        ),
        migrations.AddConstraint(
            model_name='registrationwhitelist',
            constraint=models.UniqueConstraint(
                fields=['email', 'course'],
                name='uniq_whitelist_email_course'
            ),
        ),
        migrations.AddIndex(
            model_name='registrationwhitelist',
            index=models.Index(
                fields=['email'],
                name='submissions__email_123456_idx'
            ),
        ),
        migrations.AddIndex(
            model_name='registrationwhitelist',
            index=models.Index(
                fields=['course'],
                name='submissions__course_123456_idx'
            ),
        ),
    ]
