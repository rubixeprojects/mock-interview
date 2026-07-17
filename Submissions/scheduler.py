"""
APScheduler configuration for Django.
Runs scheduled tasks automatically when Django starts.
For EKS: Set RUN_SCHEDULER=False (scheduler runs as CronJobs instead)
"""

import logging
import os
from apscheduler.schedulers.background import BackgroundScheduler
from django.core.management import call_command
from datetime import datetime

logger = logging.getLogger(__name__)


def start_scheduler():
    """
    Initialize and start the APScheduler scheduler.
    Only runs if RUN_SCHEDULER environment variable is 'True'.

    For EKS deployment, keep RUN_SCHEDULER=False and use Kubernetes CronJobs instead.
    """
    run_scheduler = os.getenv('RUN_SCHEDULER', 'False').lower() == 'true'

    if not run_scheduler:
        logger.info("⏸️  APScheduler disabled (RUN_SCHEDULER=False) - Use Kubernetes CronJobs instead")
        return

    try:
        from django_apscheduler.jobstores import DjangoJobStore

        scheduler = BackgroundScheduler()
        scheduler.add_jobstore(DjangoJobStore(), "default")

        # Schedule the evaluation email task to run every 12 hours
        scheduler.add_job(
            send_pending_evaluation_emails,
            trigger="interval",
            hours=12,
            id="send_pending_evaluation_emails",
            name="Send Pending Evaluation Emails",
            replace_existing=True,
        )

        # Sync ReadyForClientPick status for students whose capstones are all
        # submitted/evaluated but whose status was never advanced (e.g. they
        # never visited the evaluations page).  Runs every 6 hours.
        scheduler.add_job(
            sync_ready_for_client_pick,
            trigger="interval",
            hours=6,
            id="sync_ready_for_client_pick",
            name="Sync ReadyForClientPick Status",
            replace_existing=True,
        )

        scheduler.start()
        logger.info("✅ APScheduler started successfully with DjangoJobStore — 2 jobs scheduled")
        logger.info("  📧 Eval email job: every 12 hours")
        logger.info("  🔄 ReadyForClientPick sync: every 6 hours")
    except Exception as e:
        logger.error(f"❌ Error starting scheduler: {str(e)}")


def _cache_job_result(cache_key, status, summary, error=None):
    """Persist the last run result to Django cache (DB-backed, survives restarts)."""
    try:
        from django.core.cache import cache
        from django.utils import timezone
        cache.set(cache_key, {
            "status": status,
            "ran_at": timezone.now().isoformat(),
            "summary": summary,
            "error": error,
        }, timeout=None)  # no expiry — always keep the last result
    except Exception as e:
        logger.warning(f"Could not write cron result to cache: {e}")


def send_pending_evaluation_emails():
    """
    Task: Process visible evaluations and send emails.
    Runs periodically (every 12 hours by default).
    """
    logger.info("⏰ Starting evaluation email processing task...")
    cache_key = "cron:eval_emails:last_run"
    try:
        from Submissions.services.email_service import process_visible_evaluations
        result = process_visible_evaluations()
        summary = f"Sent: {result['sent']}  Failed: {result['failed']}"
        if result.get("failures"):
            summary += "\nFailures:\n" + "\n".join(
                f"  ID={f['id']} {f['team']} {f['project']}: {f['reason']}"
                for f in result["failures"]
            )
        status = "success" if result["failed"] == 0 else ("error" if result["sent"] == 0 else "partial")
        logger.info(f"✅ {summary}")
        _cache_job_result(cache_key, status, summary)
    except Exception as e:
        logger.error(f"❌ Error in send_pending_evaluation_emails task: {str(e)}")
        _cache_job_result(cache_key, "error", "Task failed", error=str(e))


def sync_ready_for_client_pick():
    """
    Task: Advance students from CapStoneProjectsAssigned → ReadyForClientPick
    when all required capstones are submitted/evaluated and no client project
    has been picked yet.

    The evaluations view does this on page load, but students who never visit
    that page stay stuck at CapStoneProjectsAssigned indefinitely.  This job
    catches them automatically every 6 hours.
    """
    logger.info("⏰ Starting ReadyForClientPick sync...")
    cache_key = "cron:ready_for_client_pick:last_run"
    try:
        from django.db import connection
        from django.utils import timezone

        capstone_required = {"CDS": 4, "AIE": 4, "CDE": 2, "CDA": 2}

        updated_total = 0
        with connection.cursor() as cur:
            for course, required in capstone_required.items():
                cur.execute("""
                    UPDATE submissions_student s
                    JOIN submissions_team t ON s.TeamID = t.team_id
                    SET s.status = 'ReadyForClientPick',
                        s.status_updated_at = %s
                    WHERE s.status = 'CapStoneProjectsAssigned'
                      AND s.course = %s
                      AND t.team_id LIKE 'PTID-%%'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM submissions_team_project tp2
                          JOIN submissions_project_registry pr2
                            ON pr2.ProjectID = tp2.ProjectID
                          WHERE tp2.TeamID = t.team_id
                            AND pr2.ProjectType = 'Client'
                      )
                      AND (
                          SELECT COUNT(DISTINCT tp.ProjectID)
                          FROM submissions_team_project tp
                          JOIN submissions_project_registry pr
                            ON pr.ProjectID = tp.ProjectID
                          WHERE tp.TeamID = t.team_id
                            AND pr.ProjectType = 'Capstone'
                            AND tp.status IN ('submitted', 'evaluated')
                      ) >= %s
                """, [timezone.now(), course, required])
                updated_total += cur.rowcount

        summary = f"Advanced {updated_total} student(s) to ReadyForClientPick"
        logger.info(f"✅ {summary}")
        _cache_job_result(cache_key, "success", summary)
    except Exception as e:
        logger.error(f"❌ Error in sync_ready_for_client_pick: {str(e)}")
        _cache_job_result(cache_key, "error", "Task failed", error=str(e))


def import_students_daily():
    """
    Task: Import student data from Google Sheets daily.
    Runs once per day at 2:00 AM UTC.
    """
    logger.info("⏰ Starting daily student import from Google Sheets...")
    cache_key = "cron:student_import:last_run"
    try:
        from io import StringIO
        out = StringIO()
        call_command('import_students_from_sheets', '--from-date=2023-01-01', stdout=out)
        output = out.getvalue().strip()
        # Keep last 500 chars of output as the summary
        summary = output[-500:] if len(output) > 500 else (output or "Completed — no output")
        logger.info("✅ Daily student import completed")
        _cache_job_result(cache_key, "success", summary)
    except Exception as e:
        logger.error(f"❌ Error in import_students_daily task: {str(e)}")
        _cache_job_result(cache_key, "error", "Task failed", error=str(e))
