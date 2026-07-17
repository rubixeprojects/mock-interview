from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "No-op: Elasticsearch has been removed. Search uses SQL LIKE queries."

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING(
            "Elasticsearch has been removed from this project. "
            "Search is now handled via SQL LIKE queries on the database. "
            "No action needed."
        ))
