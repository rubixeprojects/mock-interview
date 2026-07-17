# Generated migration file - indexes may have different names in production
# This migration handles field updates and skips index renames that don't exist

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Submissions', '0022_registration_whitelist'),
    ]

    operations = [
        migrations.AlterField(
            model_name='team',
            name='team_preference',
            field=models.CharField(choices=[('individual', 'Individual'), ('assign_team', 'Assign a team')], db_column='team_preference', default='individual', max_length=20),
        ),
    ]
