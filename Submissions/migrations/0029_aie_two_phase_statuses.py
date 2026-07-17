from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("Submissions", "0028_add_modified_identities_to_csvlog"),
    ]

    operations = [
        migrations.AlterField(
            model_name="student",
            name="status",
            field=models.CharField(
                choices=[
                    ("Registered", "Registered"),
                    ("TeamIDGiven", "TeamIDGiven"),
                    ("CapStoneProjectsAssigned", "CapStoneProjectsAssigned"),
                    ("ReadyForClientPick", "ReadyForClientPick"),
                    ("ClientProjectAssigned", "ClientProjectAssigned"),
                    ("AllCompleted", "AllCompleted"),
                    ("CDSCycleComplete", "CDSCycleComplete"),
                    ("AIECapstoneAssigned", "AIECapstoneAssigned"),
                    ("AIEReadyForClientPick", "AIEReadyForClientPick"),
                    ("AIEClientAssigned", "AIEClientAssigned"),
                ],
                db_column="status",
                default="Registered",
                max_length=40,
            ),
        ),
    ]
