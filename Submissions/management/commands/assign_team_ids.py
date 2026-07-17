"""
Management command to assign team IDs to pending teams.

Handles 3 scenarios:
1. Individual: Single student team → assign immediately
2. I have a team: Wait until all teammates registered + NOC=True + same course
3. Assign a team: Group 4 students from same course (first-come-first-serve)

Team ID format: PTID-{COURSE_CODE}-{MONTH_3LETTERS}-{YEAR_2DIGITS}-{5DIGIT_COUNTER}
Example: PTID-AI-APR-23-11111 (starts from 11111)

Updates:
- Team table: team_id, created_at, email_sent=0
- Student table: team field with FK reference

After assignment, sends team formation emails via process_team_formation_emails()
"""

from django.core.management.base import BaseCommand
from django.db import transaction, IntegrityError
from django.utils import timezone
from datetime import datetime, timedelta
from Submissions.models import Team, StudentIdentity, Student, Course
from Submissions.services.team_id_service import generate_unified_team_id
from Submissions.services.email_service import process_team_formation_emails
import logging
import time
import random

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Assign team IDs to pending teams and update Student records"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without saving',
        )
        parser.add_argument(
            '--course',
            type=str,
            help='Only process teams for a specific course (e.g., AI, CDS)',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        course_filter = options.get('course', None)

        self.stdout.write(self.style.SUCCESS("🚀 Starting Team ID Assignment..."))
        if dry_run:
            self.stdout.write(self.style.WARNING("⚠️  DRY RUN MODE - No changes will be saved"))

        try:
            # Process each section independently to avoid large transaction locks
            # Step 1: Process Individual Teams
            self.stdout.write("\n📋 Processing INDIVIDUAL teams...")
            individual_count = self._process_individual_teams(dry_run, course_filter)
            self.stdout.write(self.style.SUCCESS(f"✅ {individual_count} individual teams assigned"))

            # DISABLED FOR NOW: Process "I have a team" Teams - NOT IN FUNCTION
            # self.stdout.write("\n📋 Processing 'I HAVE A TEAM' preferences...")
            # team_pref_count = self._process_team_preference(dry_run, course_filter)
            # self.stdout.write(self.style.SUCCESS(f"✅ {team_pref_count} team preferences ready for assignment"))

            # Step 3: Process "Assign a team" Teams
            self.stdout.write("\n📋 Processing 'ASSIGN A TEAM' preferences...")
            auto_assign_count = self._process_auto_assign(dry_run, course_filter)
            self.stdout.write(self.style.SUCCESS(f"✅ {auto_assign_count} auto-assign teams created"))

            # Step 4: Send team formation emails (only if not dry-run)
            if not dry_run:
                self.stdout.write("\n📧 Sending team formation emails...")
                email_count = process_team_formation_emails()
                self.stdout.write(self.style.SUCCESS(f"✅ {email_count} team formation emails sent"))

            if dry_run:
                self.stdout.write(self.style.WARNING("⚠️  DRY RUN - No changes were made"))
            else:
                self.stdout.write(self.style.SUCCESS("\n✅ All changes committed to database"))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Error during assignment: {str(e)}"))
            logger.exception("Team assignment failed")
            raise

    def _process_individual_teams(self, dry_run, course_filter):
        """Process individual (single-person) team preferences."""
        pending_teams = Team.objects.filter(
            team_preference="individual",
            team_id__isnull=True,
            student_submitted=True,
        )

        if course_filter:
            pending_teams = pending_teams.filter(course=course_filter)

        count = 0
        for team in pending_teams:
            # We cycle prefixes A, B, C randomly or use A by default. Data request says "add A ,B , C prefixes". We will use A for simplicity or cycle.
            # Let's randomly pick one of A, B, C
            import random
            prefix = random.choice(["A", "B", "C"])
            
            if dry_run:
                team_id = self._generate_team_id(team.course, prefix=prefix)
                self.stdout.write(f"  [DRY RUN] {team.student.email} → {team_id}")
                count += 1
            else:
                try:
                    with transaction.atomic():
                        enrollment = Student.objects.filter(
                            identity__email=team.student.email,
                            course=team.course
                        ).first()

                        if not enrollment:
                            logger.warning(f"No enrollment found for {team.student.email}")
                            self.stdout.write(f"  ⚠️ {team.student.email} - no enrollment found")
                            continue

                        actual_course = team.course if team.course else enrollment.course
                        
                        # Generate team ID with prefix
                        team_id = self._generate_team_id_locked(actual_course, prefix=prefix)

                        # Update Team record
                        team.team_id = team_id
                        team.course = actual_course  # Ensure course is set
                        team.created_at = timezone.now()
                        team.email_sent = False
                        team.save()

                        # Link student to team
                        enrollment.team_id = team_id
                        enrollment.status = "TeamIDGiven"
                        enrollment.save()

                        self.stdout.write(f"  ✓ {team.student.email} → {team_id}")
                        count += 1

                except IntegrityError as e:
                    if "team_id" in str(e):
                        # Race condition: another process created this team_id
                        # This is rare but possible - log and skip
                        logger.warning(f"Race condition creating individual team for {team.student.email}: {e}")
                        self.stdout.write(self.style.WARNING(
                            f"  ⚠️ {team.student.email} - race condition, skipping"
                        ))
                    else:
                        raise

        return count

    # DISABLED FOR NOW: _process_team_preference - NOT IN FUNCTION
    # def _process_team_preference(self, dry_run, course_filter):
    #     """
    #     Process 'I have a team' preferences.
    #     Assigns team ID only if ALL teammates are registered + NOC=True + same course.
    #     """
    #     pending_teams = Team.objects.filter(
    #         team_preference="i_have_team",
    #         team_id__isnull=True,
    #         preference_emails__isnull=False,
    #     )
    #
    #     if course_filter:
    #         pending_teams = pending_teams.filter(course=course_filter)
    #
    #     count = 0
    #     for team in pending_teams:
    #         # Get all teammate emails
    #         teammate_emails = team.preference_emails or []
    #         all_emails = [team.student.email] + teammate_emails
    #
    #         # Check if all teammates are ready
    #         ready = True
    #         reasons = []
    #
    #         for email in all_emails:
    #             ident = StudentIdentity.objects.filter(email=email).first()
    #
    #             if not ident:
    #                 ready = False
    #                 reasons.append(f"{email} not registered")
    #                 continue
    #
    #             if not ident.NOC_issued:
    #                 ready = False
    #                 reasons.append(f"{email} NOC not issued")
    #                 continue
    #
    #             # Check same course enrollment
    #             enrollment = Student.objects.filter(
    #                 identity=ident,
    #                 course=team.course
    #             ).first()
    #
    #             if not enrollment:
    #                 ready = False
    #                 reasons.append(f"{email} not enrolled in {team.course}")
    #
    #         if ready:
    #             # All teammates ready - assign team ID
    #             if dry_run:
    #                 # DRY RUN: Don't use transaction
    #                 team_id = self._generate_team_id(team.course)
    #                 self.stdout.write(f"  [DRY RUN] Team {team_id}: {', '.join(all_emails)}")
    #                 count += 1
    #             else:
    #                 # REAL RUN: Use transaction with locking
    #                 try:
    #                     with transaction.atomic():
    #                         # First: Collect all students and determine actual course
    #                         student_ids_to_update = []
    #                         actual_course = team.course
    #                         
    #                         for email in all_emails:
    #                             enrollment = Student.objects.filter(
    #                                 identity__email=email,
    #                                 course=team.course
    #                             ).first()
    #                             
    #                             if enrollment:
    #                                 student_ids_to_update.append(enrollment.id)
    #                                 # Trust student's course if Team.course is empty
    #                                 if not actual_course:
    #                                    actual_course = enrollment.course
    #                             else:
    #                                 logger.warning(f"No enrollment found for {email}")
    #
    #                         # Generate team ID with actual course
    #                         team_id = self._generate_team_id_locked(actual_course)
    #
    #                         team.team_id = team_id
    #                         team.course = actual_course  # Ensure course is set
    #                         team.created_at = timezone.now()
    #                         team.email_sent = False
    #                         team.save()
    #
    #                         # Batch update in one query
    #                        if student_ids_to_update:
    #                             Student.objects.filter(id__in=student_ids_to_update).update(
    #                                 team_id=team_id,
    #                                 status="TeamIDGiven"
    #                             )
    #
    #                         self.stdout.write(f"  ✓ Team {team_id}: {', '.join(all_emails)}")
    #                         count += 1
    #
    #                 except IntegrityError as e:
    #                     if "team_id" in str(e):
    #                         logger.warning(f"Race condition creating i_have_team for {team.student.email}: {e}")
    #                         self.stdout.write(self.style.WARNING(
    #                             f"  ⚠️ {team.student.email} - race condition, skipping"
    #                         ))
    #                     else:
    #                         raise
    #         else:
    #             # Not ready yet
    #             self.stdout.write(f"  ⏳ Team pending: {team.student.email} ({', '.join(reasons)})")
    #
    #     return count

    def _process_auto_assign(self, dry_run, course_filter):
        """
        Process 'Assign a team' preferences.
        Groups students from same course (4 per team) in first-come-first-serve order.
        Uses per-group transactions to avoid lock timeouts.
        """
        if course_filter:
            courses_to_process = [course_filter]
        else:
            # Get all courses with pending auto-assign teams
            courses_to_process = list(
                Team.objects.filter(
                    team_preference="assign_team",
                    team_id__isnull=True,
                    student_submitted=True,
                ).values_list('course', flat=True).distinct()
            )

        total_teams_created = 0

        for course_code in courses_to_process:
            # Get all pending auto-assign students for this course
            pending_students = Team.objects.filter(
                team_preference="assign_team",
                team_id__isnull=True,
                course=course_code,
                student_submitted=True,
            ).order_by("preference_submitted_at")

            # Group into teams of 4
            groups = []
            current_group = []

            for team_pref in pending_students:
                current_group.append(team_pref)

                if len(current_group) == 4:
                    groups.append(current_group)
                    current_group = []

            # Process residue (waiting up to 3 days)
            if current_group:
                # Find the oldest preference submitted
                oldest_pref = min(
                    current_group, 
                    key=lambda t: t.preference_submitted_at or timezone.now()
                )
                
                submitted_at = oldest_pref.preference_submitted_at or timezone.now()
                if timezone.now() - submitted_at >= timedelta(days=3):
                    groups.append(current_group)
                    current_group = []

            # Process complete groups (4 students OR residue >= 3 days) with per-group transactions
            for group in groups:
                if dry_run:
                    # DRY RUN: Just show what would be created
                    team_id = self._generate_team_id_safe(course_code)
                    team_emails = [t.student.email for t in group]
                    self.stdout.write(f"  [DRY RUN] Team {team_id}: {', '.join(team_emails)}")
                    total_teams_created += 1
                else:
                    # REAL RUN: Process group with per-group transaction + retry
                    success = self._process_group_with_retry(group, course_code)
                    if success:
                        total_teams_created += 1

            # Report incomplete groups (waiting for more students)
            if current_group:
                emails = [t.student.email for t in current_group]
                self.stdout.write(f"  ⏳ Incomplete group ({len(current_group)}/4): {', '.join(emails)}")

        return total_teams_created

    def _process_group_with_retry(self, group, course_code, max_retries=3):
        """
        Process a single group with retry logic for IntegrityError (duplicate team_id).
        Uses per-group atomic transaction to minimize lock scope.
        """
        for attempt in range(max_retries):
            try:
                with transaction.atomic():
                    # First: Collect all students and determine actual course
                    student_ids_to_update = []
                    actual_course = course_code
                    
                    for team_pref in group:
                        enrollment = Student.objects.filter(
                            identity__email=team_pref.student.email,
                            course=course_code
                        ).first()

                        if enrollment:
                            student_ids_to_update.append(enrollment.id)
                            # Trust student's course if course_code is empty
                            if not actual_course:
                                actual_course = enrollment.course
                        else:
                            logger.warning(f"No enrollment found for {team_pref.student.email}")

                    # Generate team ID with actual course
                    team_id = self._generate_team_id_locked(actual_course)

                    # ✅ Only ONE row represents the team
                    team_main = group[0]
                    team_main.team_id = team_id
                    team_main.course = actual_course  # Ensure course is set
                    team_main.created_at = timezone.now()
                    team_main.email_sent = False
                    team_main.save()

                    # ✅ Delete other Team rows to avoid orphaned records with team_id=NULL
                    # These were preference submissions, not actual teams
                    for i in range(1, len(group)):
                        team_other = group[i]
                        team_other.delete()

                    # Batch update all students in one query
                    if student_ids_to_update:
                        Student.objects.filter(id__in=student_ids_to_update).update(
                            team_id=team_id,
                            status="TeamIDGiven"
                        )

                    team_emails = [t.student.email for t in group]
                    self.stdout.write(f"  ✓ Team {team_id}: {', '.join(team_emails)}")
                    return True

            except IntegrityError as e:
                # Duplicate team_id - retry with new ID
                if "team_id" in str(e):
                    if attempt < max_retries - 1:
                        # Exponential backoff: 0.1s, 0.2s, 0.4s
                        wait_time = 0.1 * (2 ** attempt) + random.uniform(0, 0.05)
                        logger.warning(
                            f"Duplicate team_id detected, retrying (attempt {attempt+1}/{max_retries})..."
                        )
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.error(f"Failed to create team after {max_retries} retries: {e}")
                        self.stdout.write(self.style.ERROR(
                            f"  ⚠️ Could not assign team (duplicate team_id): {', '.join([t.student.email for t in group])}"
                        ))
                        return False
                else:
                    # Different integrity error - don't retry
                    logger.error(f"Integrity error (not team_id): {e}")
                    raise

            except Exception as e:
                logger.error(f"Unexpected error processing group: {e}")
                self.stdout.write(self.style.ERROR(f"  ⚠️ Error processing team: {str(e)}"))
                return False

        return False

    def _generate_team_id(self, course_code, prefix=""):
        return generate_unified_team_id(course_code, prefix=prefix)

    def _generate_team_id_locked(self, course_code, prefix=""):
        return generate_unified_team_id(course_code, prefix=prefix)

    def _generate_team_id_safe(self, course_code):
        """
        Generate team ID for dry-run mode (no locking needed since nothing is saved).
        """
        # For dry-run, we don't need actual unique IDs, just show the format
        return self._generate_team_id(course_code)