"""
Management command to process pending evaluations and send emails.
Can be scheduled with cron or task scheduler.

Usage: python manage.py send_pending_evaluation_emails

NOTE: Team formation emails are sent separately by assign_team_ids command
      when team IDs are first assigned, not here.
"""

from django.core.management.base import BaseCommand
from Submissions.services.email_service import process_visible_evaluations


class Command(BaseCommand):
    help = "Process visible evaluations and send evaluation emails"

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING("🚀 Starting email processing...\n"))
        
        # Process evaluation emails only
        # (Team formation emails are sent by assign_team_ids command)
        self.stdout.write(self.style.WARNING("📧 Processing evaluation emails..."))
        eval_count = process_visible_evaluations()
        self.stdout.write(
            self.style.SUCCESS(
                f"✅ Successfully processed {eval_count} evaluations\n"
            )
        )
        
        self.stdout.write(
            self.style.SUCCESS(
                f"✨ Total emails sent: {eval_count}"
            )
        )
