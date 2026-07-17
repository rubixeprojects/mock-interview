from django.apps import AppConfig


class SubmissionsConfig(AppConfig):
    name = 'Submissions'
    
    def ready(self):
        """
        Initialize scheduler and signals when Django app is ready.
        This runs once when Django starts.
        """
        # Register signals for model sync
        try:
            from . import signals  # noqa: F401
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Could not load signals: {str(e)}")
        
        # Start scheduler
        try:
            from .scheduler import start_scheduler
            start_scheduler()
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Could not start scheduler: {str(e)}")

