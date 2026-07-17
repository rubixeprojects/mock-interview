"""
Management command to sync evaluation status between TeamProject and Submission tables.

Historical evaluations may have:
- TeamProject.status = "evaluated"
- Submission.evaluated = False  (out of sync!)

This command finds and fixes those inconsistencies.
"""

from django.core.management.base import BaseCommand
from django.db.models import Q
from Submissions.models import Submission, TeamProject, Evaluation
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sync evaluation status: mark Submissions as evaluated if their TeamProject is evaluated"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without saving',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)

        self.stdout.write(self.style.SUCCESS("🔄 Syncing evaluation status..."))
        if dry_run:
            self.stdout.write(self.style.WARNING("⚠️  DRY RUN MODE - No changes will be saved"))

        # Find all TeamProjects with status "evaluated"
        evaluated_team_projects = TeamProject.objects.filter(status="evaluated")
        self.stdout.write(f"Found {evaluated_team_projects.count()} evaluated TeamProjects")

        # Find their Submission records that are NOT marked as evaluated
        out_of_sync = Submission.objects.filter(
            team_project__in=evaluated_team_projects,
            evaluated=False
        )

        count = out_of_sync.count()
        self.stdout.write(self.style.WARNING(f"Found {count} out-of-sync Submission records"))

        if count == 0:
            self.stdout.write(self.style.SUCCESS("✅ All records are already in sync!"))
            return

        if not dry_run:
            # Update them
            out_of_sync.update(evaluated=True)
            self.stdout.write(self.style.SUCCESS(f"✅ Updated {count} Submission records to evaluated=True"))

            # Log which evaluations were fixed
            for submission in out_of_sync[:10]:  # Show first 10
                tp = submission.team_project
                self.stdout.write(
                    f"  ✓ {tp.team.team_id} | {tp.project.project_id} | {submission.student.identity.email}"
                )
            
            if count > 10:
                self.stdout.write(f"  ... and {count - 10} more")

            self.stdout.write(self.style.SUCCESS(f"\n✅ All {count} records synced successfully!"))
        else:
            self.stdout.write(self.style.WARNING(f"⚠️  DRY RUN - Would update {count} records"))
            for submission in out_of_sync[:5]:
                tp = submission.team_project
                self.stdout.write(
                    f"  → {tp.team.team_id} | {tp.project.project_id} | {submission.student.identity.email}"
                )
            if count > 5:
                self.stdout.write(f"  ... and {count - 5} more")
            self.stdout.write(self.style.WARNING("⚠️  DRY RUN - Rolling back changes"))
