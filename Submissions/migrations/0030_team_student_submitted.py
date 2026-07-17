from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("Submissions", "0029_aie_two_phase_statuses"),
    ]

    operations = [
        migrations.AddField(
            model_name="team",
            name="student_submitted",
            field=models.BooleanField(
                default=False,
                db_column="student_submitted",
                help_text="Set True when student submits preference via the student portal",
            ),
        ),
    ]
