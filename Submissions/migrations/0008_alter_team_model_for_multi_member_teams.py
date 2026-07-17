# Generated custom migration for Team model refactoring

import django.db.models.deletion
from django.db import migrations, models


def migrate_team_schema(apps, schema_editor):
    """Custom migration using raw SQL to handle PK change"""
    # This requires manual SQL because Django can't easily change PK from email to team_id
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('Submissions', '0007_evaluation_email_sent_and_more'),
    ]

    operations = [
        migrations.RunPython(
            migrate_team_schema,
            migrations.RunPython.noop,
        ),
        # Just update the field definitions without changing the actual PK yet
        migrations.AlterField(
            model_name='team',
            name='student',
            field=models.ForeignKey(db_column='email', on_delete=django.db.models.deletion.CASCADE, related_name='teams', to='Submissions.studentidentity'),
        ),
    ]


