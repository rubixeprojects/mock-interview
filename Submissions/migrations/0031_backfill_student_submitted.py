from django.db import migrations


def backfill_student_submitted(apps, schema_editor):
    """
    All Team rows that existed before the student_submitted column was added
    were created by students submitting their preference through the portal.
    Set student_submitted=True for all pending rows (no team_id assigned yet)
    so they are included in team formation.
    """
    Team = apps.get_model("Submissions", "Team")
    updated = Team.objects.filter(
        team_id__isnull=True,
        student_submitted=False,
    ).update(student_submitted=True)
    print(f"\n  Backfilled student_submitted=True for {updated} existing pending Team rows")


class Migration(migrations.Migration):

    dependencies = [
        ("Submissions", "0030_team_student_submitted"),
    ]

    operations = [
        migrations.RunPython(backfill_student_submitted, migrations.RunPython.noop),
    ]
