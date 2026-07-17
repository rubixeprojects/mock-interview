# Generated migration for Team model restructuring
# This migration adds new fields to Team table for team preferences

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('Submissions', '0016_passwordresetotp_last_resent_at_and_more'),
    ]

    operations = [
        # Step 1: Add new fields to existing Team table
        migrations.AddField(
            model_name='team',
            name='id',
            field=models.BigAutoField(primary_key=True, serialize=False),
            preserve_default=False,
        ),
        
        migrations.AddField(
            model_name='team',
            name='team_preference',
            field=models.CharField(
                choices=[('individual', 'Individual'), ('i_have_team', 'I have a team'), ('assign_team', 'Assign a team')],
                default='individual',
                max_length=20
            ),
        ),
        
        migrations.AddField(
            model_name='team',
            name='preference_emails',
            field=models.JSONField(blank=True, default=list, help_text='List of teammate emails (max 3)', null=True),
        ),
        
        migrations.AddField(
            model_name='team',
            name='course',
            field=models.CharField(default='', max_length=120),
        ),
        
        migrations.AddField(
            model_name='team',
            name='created_at',
            field=models.DateTimeField(blank=True, help_text='Timestamp when team_id was assigned', null=True),
        ),
        
        migrations.AddField(
            model_name='team',
            name='email_sent',
            field=models.BooleanField(default=False, help_text='Whether team formation email was sent'),
        ),
        
        migrations.AddField(
            model_name='team',
            name='preference_submitted_at',
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        
        # Step 2: Alter team_id to be nullable and unique
        migrations.AlterField(
            model_name='team',
            name='team_id',
            field=models.CharField(blank=True, max_length=50, null=True, unique=True),
        ),
        
        # Step 3: Alter student FK
        migrations.AlterField(
            model_name='team',
            name='student',
            field=models.ForeignKey(db_column='email', on_delete=django.db.models.deletion.CASCADE, related_name='team_preferences', to='Submissions.studentidentity'),
        ),
        
        # Step 4: Add indexes
        migrations.AddIndex(
            model_name='team',
            index=models.Index(fields=['course'], name='submissions_team_course_idx'),
        ),
        migrations.AddIndex(
            model_name='team',
            index=models.Index(fields=['team_id'], name='submissions_team_id_idx'),
        ),
        migrations.AddIndex(
            model_name='team',
            index=models.Index(fields=['student', 'course'], name='submissions_team_student_course_idx'),
        ),
    ]
