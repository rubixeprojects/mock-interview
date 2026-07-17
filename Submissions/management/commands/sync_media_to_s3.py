"""
Django management command to sync local media files to S3 bucket.
Usage: python manage.py sync_media_to_s3
"""

import os
import boto3
from django.core.management.base import BaseCommand
from django.conf import settings
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Sync local media files to S3 bucket'

    def add_arguments(self, parser):
        parser.add_argument(
            '--folder',
            type=str,
            default=None,
            help='Specific subfolder to sync (e.g. Evaluations, Submissions). Omit to sync all of MEDIA_ROOT.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show files to be synced without actually uploading',
        )

    def handle(self, *args, **options):
        if not settings.USE_S3:
            self.stdout.write(
                self.style.ERROR('❌ USE_S3 is False. S3 sync disabled.')
            )
            return

        folder = options['folder']
        dry_run = options['dry_run']

        media_root = Path(settings.MEDIA_ROOT)

        if folder:
            source_folder = media_root / folder
            if not source_folder.exists():
                self.stdout.write(
                    self.style.ERROR(f'❌ Folder not found: {source_folder}')
                )
                return
            scan_roots = [source_folder]
        else:
            # Sync Evaluations and Submissions subdirectories
            scan_roots = []
            for sub in ['Evaluations', 'Submissions']:
                p = media_root / sub
                if p.exists():
                    scan_roots.append(p)
                else:
                    self.stdout.write(self.style.WARNING(f'⚠️  Skipping missing folder: {p}'))
            if not scan_roots:
                self.stdout.write(self.style.ERROR(f'❌ No sync targets found under {media_root}'))
                return

        try:
            s3_client = boto3.client(
                's3',
                region_name=settings.AWS_S3_REGION_NAME
            )

            self.stdout.write(f'📂 Sync targets: {", ".join(str(r.relative_to(media_root)) for r in scan_roots)}')

            # Collect all files across all scan roots
            files_to_upload = []
            for root in scan_roots:
                for file_path in root.rglob('*'):
                    if file_path.is_file():
                        files_to_upload.append(file_path)
            
            if not files_to_upload:
                self.stdout.write(
                    self.style.WARNING(f'⚠️  No files found in {folder}/')
                )
                return

            self.stdout.write(
                self.style.SUCCESS(f'📁 Found {len(files_to_upload)} files to sync')
            )

            if dry_run:
                self.stdout.write(self.style.WARNING('🔍 DRY RUN MODE - No files will be uploaded\n'))
                for file_path in files_to_upload:
                    rel_path = file_path.relative_to(media_root)
                    s3_key = f'media/{rel_path.as_posix()}'
                    file_size = file_path.stat().st_size / (1024 * 1024)  # Size in MB
                    self.stdout.write(f'  → {s3_key} ({file_size:.2f} MB)')
                return

            # Upload files to S3
            uploaded = 0
            failed = 0
            
            for file_path in files_to_upload:
                try:
                    rel_path = file_path.relative_to(media_root)
                    s3_key = f'media/{rel_path.as_posix()}'
                    file_size = file_path.stat().st_size / (1024 * 1024)
                    
                    # Upload to S3
                    s3_client.upload_file(
                        str(file_path),
                        settings.AWS_STORAGE_BUCKET_NAME,
                        s3_key,
                        ExtraArgs={
                            'ACL': 'private',
                            'ContentType': self._get_content_type(file_path)
                        }
                    )
                    
                    self.stdout.write(
                        f'  ✅ {s3_key} ({file_size:.2f} MB)'
                    )
                    uploaded += 1
                    
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f'  ❌ Failed to upload {rel_path}: {str(e)}')
                    )
                    failed += 1

            # Summary
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS(f'✅ Uploaded: {uploaded} files'))
            if failed > 0:
                self.stdout.write(self.style.ERROR(f'❌ Failed: {failed} files'))
            
            self.stdout.write(
                self.style.SUCCESS(f'🎉 Sync complete!')
            )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'❌ Error: {str(e)}')
            )

    def _get_content_type(self, file_path):
        """Determine MIME type based on file extension"""
        ext = file_path.suffix.lower()
        mime_types = {
            '.pdf': 'application/pdf',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.doc': 'application/msword',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.xls': 'application/vnd.ms-excel',
            '.txt': 'text/plain',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
        }
        return mime_types.get(ext, 'application/octet-stream')
