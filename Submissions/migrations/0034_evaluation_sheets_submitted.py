from django.db import migrations, models, connection


def add_column(apps, schema_editor):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME   = 'submissions_evaluation'
              AND COLUMN_NAME  = 'sheets_submitted'
        """)
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "ALTER TABLE submissions_evaluation "
                "ADD COLUMN sheets_submitted TINYINT(1) NOT NULL DEFAULT 0"
            )


def remove_column(apps, schema_editor):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME   = 'submissions_evaluation'
              AND COLUMN_NAME  = 'sheets_submitted'
        """)
        if cursor.fetchone()[0] > 0:
            cursor.execute(
                "ALTER TABLE submissions_evaluation DROP COLUMN sheets_submitted"
            )


class Migration(migrations.Migration):

    dependencies = [
        ("Submissions", "0033_admin_temp_access"),
    ]

    operations = [
        # RunPython handles the DB change idempotently (skips if column exists)
        migrations.RunPython(add_column, remove_column),
        # SeparateDatabaseAndState updates Django's model state without touching DB
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AddField(
                    model_name="evaluation",
                    name="sheets_submitted",
                    field=models.BooleanField(
                        default=False,
                        db_column="sheets_submitted",
                        help_text="True when the evaluation response was sent via Google Forms (not the portal mailer)",
                    ),
                ),
            ],
        ),
    ]
