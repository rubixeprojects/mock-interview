"""
Django management command to import student data from Google Sheets.

Workflow:
1. Authenticate via service account
2. For each sheet (CDS, CDE, CDA, AIE):
   - Download data from Google Sheets
   - Parse rows, filter by date (>= 2023-01-01)
   - Extract team_id, skip if null
   - Extract course code from team_id
   - Handle multiple email columns (pick first non-null)
   - Upsert StudentIdentity → Team → Student enrollment
3. Log summary

Usage:
    python manage.py import_students_from_sheets --from-date=2023-01-01
    python manage.py import_students_from_sheets --from-date=2024-01-01 --dry-run
"""

import re
import os
import logging
from datetime import datetime
from io import BytesIO

import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import transaction
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2 import service_account

from Submissions.models import StudentIdentity, Team, Student

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Import student data from Google Sheets"

    def add_arguments(self, parser):
        parser.add_argument(
            "--service-account",
            type=str,
            default=os.path.join(settings.BASE_DIR, "projectevalaccess-6e8603cceaea.json"),
            help="Path to service account JSON file",
        )
        parser.add_argument(
            "--from-date",
            type=str,
            default="2023-01-01",
            help="Import data from this date onwards (YYYY-MM-DD)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run without saving to database",
        )

    def handle(self, *args, **options):
        service_account_path = options["service_account"]
        from_date_str = options["from_date"]
        dry_run = options["dry_run"]

        # Parse from_date
        try:
            from_date = datetime.strptime(from_date_str, "%Y-%m-%d")
        except ValueError:
            raise CommandError(f"Invalid date format: {from_date_str}. Use YYYY-MM-DD")

        self.stdout.write(f"📅 Filtering data from: {from_date.date()}")
        self.stdout.write(f"🔒 Service account: {service_account_path}")
        if dry_run:
            self.stdout.write("⚠️  DRY RUN MODE - No changes will be saved")

        # Sheet configurations
        sheets_config = {
            "CDS": {"id": "1Aik-84bHlJ4Bm0aOww7nbtWxGEMmgr7QritJWxdOo9c", "sheet_name": None},
            "CDE": {"id": "1YAUzc9yf3fV9jWI_oUXurJ_m4nx8wEWdWLjBMrVbxFk", "sheet_name": None},
            "CDA": {"id": "1LpjqK2vMco0uwWfmyNkj26IP4daau2K-xv_Jk-T6X44", "sheet_name": "Form Responses 1"},
            "AIE": {"id": "1Y2kCjRrYel7t8atG5TeMhB2z09375xHIlvTgS9UPPZE", "sheet_name": None},
        }

        try:
            # Authenticate with Google
            creds = service_account.Credentials.from_service_account_file(
                service_account_path,
                scopes=["https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets"],
            )
            service = build("drive", "v3", credentials=creds)
            self.stdout.write("✅ Google Drive authenticated")
            # Store creds for later use in Sheets API
            self._creds = creds

        except FileNotFoundError:
            raise CommandError(f"Service account file not found: {service_account_path}")
        except Exception as e:
            raise CommandError(f"Authentication failed: {str(e)}")

        # Process each sheet
        total_stats = {
            "rows_processed": 0,
            "rows_skipped": 0,
            "identities_created": 0,
            "identities_updated": 0,
            "teams_created": 0,
            "teams_updated": 0,
            "students_created": 0,
            "students_updated": 0,
        }

        for course_code, sheet_config in sheets_config.items():
            self.stdout.write(f"\n📊 Processing {course_code} sheet...")
            try:
                stats = self._process_sheet(
                    service, course_code, sheet_config["id"], sheet_config["sheet_name"], from_date, dry_run, self._creds
                )
                # Aggregate stats
                for key in total_stats:
                    total_stats[key] += stats.get(key, 0)
                self.stdout.write(f"✅ {course_code} completed successfully")
            except Exception as e:
                self.stdout.write(f"❌ {course_code} failed: {str(e)}")
                logger.exception(f"Error processing {course_code} sheet")
                continue

        # Print summary
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("📈 IMPORT SUMMARY")
        self.stdout.write("=" * 60)
        self.stdout.write(f"Rows processed: {total_stats['rows_processed']}")
        self.stdout.write(f"Rows skipped: {total_stats['rows_skipped']}")
        self.stdout.write(
            f"StudentIdentity created: {total_stats['identities_created']}"
        )
        self.stdout.write(
            f"StudentIdentity updated: {total_stats['identities_updated']}"
        )
        self.stdout.write(f"Team created: {total_stats['teams_created']}")
        self.stdout.write(f"Team updated: {total_stats['teams_updated']}")
        self.stdout.write(f"Student created: {total_stats['students_created']}")
        self.stdout.write(f"Student updated: {total_stats['students_updated']}")
        self.stdout.write("=" * 60)

        if dry_run:
            self.stdout.write(
                "⚠️  DRY RUN - No data was saved to database"
            )

    def _process_sheet(self, service, course_code, sheet_id, sheet_name, from_date, dry_run, creds):
        """
        Download and process a single Google Sheet.
        
        Args:
            service: Google Drive service
            course_code: Course code (CDS, CDE, etc.)
            sheet_id: Google Sheet ID
            sheet_name: Optional sheet tab name (if sheet has multiple tabs)
            from_date: Filter data from this date onwards
            dry_run: If True, don't save to database
            creds: Google credentials for Sheets API
        """
        stats = {
            "rows_processed": 0,
            "rows_skipped": 0,
            "identities_created": 0,
            "identities_updated": 0,
            "teams_created": 0,
            "teams_updated": 0,
            "students_created": 0,
            "students_updated": 0,
        }

        # Download CSV from Google Sheets
        self.stdout.write(f"  ⬇️  Downloading {course_code} sheet...")
        try:
            if sheet_name:
                # Use Sheets API to get specific sheet by name
                sheets_service = build("sheets", "v4", credentials=creds)
                result = sheets_service.spreadsheets().values().get(
                    spreadsheetId=sheet_id,
                    range=f"'{sheet_name}'",
                ).execute()
                
                values = result.get('values', [])
                if not values or len(values) < 2:
                    self.stdout.write(f"  ⚠️  Sheet '{sheet_name}' is empty, skipping")
                    return stats
                
                # First row has mixed data: timestamp in first cell, then actual headers
                # Row 0: ['12/8/2025 21:32:22', 'Email Address', 'Name', 'Email', ...]
                # We need to reconstruct: timestamp column should be separate
                headers = values[0]  # This includes timestamp as first "header"
                data_rows = values[1:]
                
                # Create DataFrame with the mixed headers first
                df = pd.DataFrame(data_rows)
                
                # Now fix the columns - the first column is timestamp, rest are actual headers
                if len(headers) > 0:
                    # Rename columns: first is 'timestamp', rest follow the headers[1:]
                    new_columns = ['timestamp'] + list(headers[1:])
                    # Pad with None if needed
                    while len(new_columns) < len(df.columns):
                        new_columns.append(f'col_{len(new_columns)}')
                    df.columns = new_columns[:len(df.columns)]
            else:
                # Default export (first sheet)
                request = service.files().export_media(fileId=sheet_id, mimeType="text/csv")
                fh = BytesIO()
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                fh.seek(0)
                df = pd.read_csv(fh)
            
            self.stdout.write(f"  ✅ Downloaded {len(df)} rows")
            
            if len(df) == 0:
                self.stdout.write(f"  ⚠️  No data found in {course_code} sheet")
                return stats
                
        except Exception as e:
            raise Exception(f"Failed to download sheet: {str(e)}")

        # Process rows
        for idx, row in df.iterrows():
            stats["rows_processed"] += 1

            # Extract timestamp
            timestamp_str = None
            
            # Try to get from 'timestamp' column (for Sheets API)
            if 'timestamp' in row.index:
                timestamp_str = row['timestamp']
            # Fallback to first column by position (for CSV)
            else:
                try:
                    timestamp_str = row.iloc[0]
                except:
                    pass
            
            # Clean up
            if pd.notna(timestamp_str):
                timestamp_str = str(timestamp_str).strip()
            else:
                timestamp_str = None
            
            if not timestamp_str:
                stats["rows_skipped"] += 1
                continue

            try:
                row_date = datetime.strptime(timestamp_str, "%m/%d/%Y %H:%M:%S")
            except (ValueError, TypeError) as e:
                stats["rows_skipped"] += 1
                continue

            # Filter by date
            if row_date < from_date:
                stats["rows_skipped"] += 1
                # Debug: show first 5 old dates
                if stats["rows_skipped"] <= 5:
                    self.stdout.write(f"    ⏰ Filtered out old date: {row_date.date()}")
                continue

            # Extract team_id
            team_id = self._extract_team_id(row)
            if not team_id:
                stats["rows_skipped"] += 1
                continue

            # Extract course from team_id (override the sheet's course if found in team_id)
            extracted_course = self._extract_course_from_team_id(team_id)
            if extracted_course:
                # Use course extracted from team_id (more reliable)
                final_course = extracted_course
            else:
                # Fallback to the sheet's course code
                final_course = course_code

            # Extract email (check multiple columns)
            email = self._extract_email(row)
            if not email:
                stats["rows_skipped"] += 1
                continue

            # Extract name
            name = self._extract_name(row)
            if not name:
                stats["rows_skipped"] += 1
                continue

            # Extract phone
            phone = self._extract_phone(row)

            # Upsert data
            if not dry_run:
                try:
                    upsert_stats = self._upsert_student_data(
                        email, name, phone, final_course, team_id
                    )
                    for key in upsert_stats:
                        stats[key] += upsert_stats[key]
                except Exception as e:
                    self.stdout.write(
                        f"  ❌ Row {idx}: Error upserting {email}: {str(e)}"
                    )
                    logger.exception(f"Error upserting row {idx}")

        return stats

    def _extract_team_id(self, row):
        """Extract team_id from various column name variations.
        Only accepts valid team IDs in PTID-XXX-...-### format.
        """
        # Define all known column name variations
        column_variations = [
            "Project Team Details",  # CDS
            "Team id",               # CDE, CDA
            "Team ID",               # CDE, CDA (uppercase)
            "Project Team ID",       # AIE
            "project team details",
            "TeamID",
            "team_id",
        ]
        
        # Try exact match first
        for col_name in column_variations:
            if col_name in row.index:
                val = row[col_name]
                if pd.notna(val):
                    val_str = str(val).strip()
                    if val_str and val_str.startswith("PTID-"):
                        return val_str
        
        # Case-insensitive fallback
        row_lower = {k.lower(): v for k, v in row.items()}
        for col_name in column_variations:
            col_lower = col_name.lower()
            if col_lower in row_lower:
                val = row_lower[col_lower]
                if pd.notna(val):
                    val_str = str(val).strip()
                    if val_str and val_str.startswith("PTID-"):
                        return val_str
        
        # Pattern matching as last resort - look for PTID- in any column
        for val in row.iloc[:15]:  # Check first 15 columns
            if pd.notna(val):
                val_str = str(val).strip()
                if val_str.startswith("PTID-"):
                    return val_str
        
        return None

    def _extract_course_from_team_id(self, team_id):
        """
        Extract course code from team_id.
        Patterns: 
        - PTID-CDS-... → CDS
        - PTID-CDE-... → CDE  
        - PTID-CDA-... → CDA
        - PTID-AI-... → AIE (2-letter code for AIE)
        """
        # Try 3-letter course code first (CDS, CDE, CDA)
        match = re.search(r"PTID-([A-Z]{3})-", team_id)
        if match:
            return match.group(1)
        
        # Try 2-letter course code (AI → AIE)
        match = re.search(r"PTID-([A-Z]{2})-", team_id)
        if match:
            code = match.group(1)
            # Map 2-letter codes to actual course codes
            mapping = {"AI": "AIE"}
            return mapping.get(code)
        
        return None

    def _extract_email(self, row):
        """
        Extract email from row, checking multiple email columns.
        Columns to check (in order): Email Address, Email, email
        """
        for col_name in ["Email Address", "Email", "email"]:
            if col_name in row.index:
                val = row[col_name]
                if pd.notna(val) and str(val).strip():
                    email = str(val).strip().lower()
                    # Basic email validation
                    if "@" in email and "." in email:
                        return email
        return None

    def _extract_name(self, row):
        """Extract name from 'Name' column."""
        for col_name in ["Name", "name"]:
            if col_name in row.index:
                val = row[col_name]
                if pd.notna(val) and str(val).strip():
                    return str(val).strip()
        return None

    def _extract_phone(self, row):
        """Extract and clean phone number."""
        for col_name in ["Phone number", "Phone", "phone"]:
            if col_name in row.index:
                val = row[col_name]
                if pd.notna(val):
                    # Clean: remove spaces, hyphens, etc.
                    phone = str(val).strip().replace(" ", "").replace("-", "")
                    if phone and len(phone) >= 10:
                        return phone
        return None

    @transaction.atomic
    def _upsert_student_data(self, email, name, phone, course, team_id):
        """
        Atomically upsert StudentIdentity, Team, and Student records.
        
        Handles multiple students in same team:
        - First student creates the Team and owns it
        - Subsequent students reuse the same Team (don't create duplicates)
        """
        stats = {
            "identities_created": 0,
            "identities_updated": 0,
            "teams_created": 0,
            "teams_updated": 0,
            "students_created": 0,
            "students_updated": 0,
        }

        # 1. Upsert StudentIdentity
        identity, created = StudentIdentity.objects.update_or_create(
            email=email, defaults={"name": name, "phone": phone}
        )
        stats["identities_created" if created else "identities_updated"] += 1

        # 2. Get or Create Team (keyed by team_id as primary key)
        team, created = Team.objects.get_or_create(
            team_id=team_id,
            defaults={"student": identity, "team_preference": "individual", "team_count": 1, "course": course},
        )
        if not created:
            # Team already exists, increment member count
            team.team_count += 1
            team.save()
        stats["teams_created" if created else "teams_updated"] += 1

        # 3. Upsert Student enrollment
        student, created = Student.objects.update_or_create(
            identity=identity,
            course=course,
            defaults={"status": "Registered", "team": team},
        )
        stats["students_created" if created else "students_updated"] += 1

        return stats
