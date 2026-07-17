"""Django signals for keeping Submission and Evaluation in sync"""

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from .models import Evaluation, Submission
import logging

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Evaluation)
def sync_submission_when_reviewed(sender, instance, created, **kwargs):
    """
    Sync Submission status with Evaluation whenever Evaluation changes:
    1. When reviewed=True → set Submission.evaluated=True
    2. When email_sent=True → keep Submission.evaluated=True (email confirmed sent)
    
    This ensures Submission.evaluated stays in sync with Evaluation state.
    """
    try:
        # Find all Submissions for this evaluation's team and project
        # Submission -> TeamProject -> (Team, Project)
        submissions = Submission.objects.filter(
            team_project__team=instance.team,
            team_project__project=instance.project,
        )
        
        # Sync evaluated status based on email_sent OR reviewed status
        # (email_sent=True means reviewed was True and email was sent)
        if instance.email_sent:
            # Email sent confirms everything is done
            updated = submissions.filter(evaluated=False).update(evaluated=True)
            if updated:
                logger.info(
                    f"Synced {updated} Submission(s) to evaluated=True (email sent) "
                    f"for team={instance.team.team_id}, project={instance.project.project_id}"
                )
        elif instance.reviewed:
            # Reviewed but not emailed yet - still sync as evaluated for now
            updated = submissions.filter(evaluated=False).update(evaluated=True)
            if updated:
                logger.info(
                    f"Synced {updated} Submission(s) to evaluated=True (reviewed, awaiting email) "
                    f"for team={instance.team.team_id}, project={instance.project.project_id}"
                )
    
    except Exception as e:
        logger.error(f"Error syncing submission evaluated status: {str(e)}")


# ==================== CACHE INVALIDATION SIGNALS ====================

def register_cache_signals():
    """
    Register cache invalidation signals.
    Called from apps.py ready() method.
    """
    try:
        from .services.cache_service import register_cache_invalidation_signals
        register_cache_invalidation_signals()
        logger.info("✅ Cache invalidation signals initialized")
    except Exception as e:
        logger.warning(f"⚠️  Could not register cache signals: {str(e)}")


# Initialize cache signals when this module loads
register_cache_signals()

