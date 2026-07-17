# Generated migration to add missing status_updated_at column
# This is a safety migration in case 0019 didn't fully apply

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Submissions', '0020_cache_configuration'),
    ]

    operations = [
        migrations.AddField(
            model_name='student',
            name='status_updated_at',
            field=models.DateTimeField(blank=True, db_column='status_updated_at', help_text='Timestamp when student status was last updated', null=True),
        ),
    ]
