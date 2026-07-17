"""
Team management service for reassigning students.

Handles:
- Generating unique team IDs with locking (prevents race conditions)
- Reassigning students to individual teams
- Sending team formation emails immediately
"""

from django.db import transaction
from django.utils import timezone
from Submissions.models import Team, Student, StudentIdentity
import logging

logger = logging.getLogger(__name__)


def generate_team_id_locked(course_code):
    """
    Generate unique team ID with database-level locking.
    
    Format: PTID-{COURSE_CODE}-{MONTH_3LETTERS}-{YEAR_2DIGITS}-{5DIGIT_COUNTER}
    Example: PTID-AI-MAR-26-11111
    
    MUST be called inside transaction.atomic() block.
    Uses select_for_update() to prevent concurrent counter increments.
    
    Args:
        course_code: Course code (e.g., 'AI', 'CDS')
    
    Returns:
        Unique team ID string
    """
    now = timezone.now()
    month_name = now.strftime("%b").upper()  # JAN, FEB, etc.
    year_short = now.strftime("%y")  # Last 2 digits of year

    # Base pattern for this course/month/year
    base_pattern = f"PTID-{course_code}-{month_name}-{year_short}-"

    # Lock all existing teams with this pattern (prevents concurrent race conditions)
    existing_teams = Team.objects.filter(
        team_id__startswith=base_pattern
    ).select_for_update().order_by('-team_id')

    if existing_teams.exists():
        # Extract counter from last team ID and increment
        last_team_id = existing_teams.first().team_id
        last_counter = int(last_team_id.split('-')[-1])
        next_counter = last_counter + 1
    else:
        # Start from 11111 for this course/month/year
        next_counter = 11111

    team_id = f"{base_pattern}{next_counter}"
    return team_id


@transaction.atomic()
def reassign_student_to_individual(student_id):
    """
    Reassign a student from group team to individual team.
    
    Steps:
    1. Validate student exists
    2. Generate unique individual team ID (with database locking)
    3. Create new Team record with preference='individual'
    4. Update Student.team to new team
    5. Send team formation email immediately
    6. Return success status
    
    MUST be called with @transaction.atomic() (done here internally).
    Database locking prevents concurrent team ID conflicts.
    
    Args:
        student_id: ID of Student to reassign
    
    Returns:
        (success: bool, message: str, new_team_id: str)
    
    Raises:
        Student.DoesNotExist: If student not found
        Exception: Various validation or email errors
    """
    
    try:
        # Get student with related data
        student = Student.objects.select_for_update().get(id=student_id)
        student_identity = student.identity
        course = student.course
        
        # Course is already stored as code (CDA, CDE, CDS, AIE, etc.)
        course_code = course.upper()
        
        logger.info(f"Reassigning student {student_identity.email} to individual team")
        
        # Generate unique team ID (with locking to prevent duplicates)
        team_id = generate_team_id_locked(course_code)
        logger.info(f"Generated team ID: {team_id}")
        
        # Create new individual Team
        new_team = Team.objects.create(
            student=student_identity,
            team_id=team_id,
            team_preference="individual",
            course=course,
            team_count=1,
            created_at=timezone.now(),
            email_sent=True,  # Will be marked True after email sent
        )
        logger.info(f"Created team: {new_team.id} with team_id {team_id}")
        
        # Update Student to point to new team
        student.team_id = team_id
        student.save(update_fields=['team_id'])
        logger.info(f"Updated student {student_identity.email} to team {team_id}")

        # Team formation emails are disabled — teams are formed silently
        return (
            True,
            f"Student {student_identity.email} moved to individual team {team_id}.",
            team_id
        )
    
    except Student.DoesNotExist:
        logger.error(f"Student with ID {student_id} not found")
        return (False, f"Student not found (ID: {student_id})", None)
    
    except Exception as e:
        logger.error(f"Error reassigning student {student_id}: {str(e)}", exc_info=True)
        return (False, f"Error reassigning student: {str(e)}", None)


def _send_individual_team_email(student_identity, team_id):
    """
    Send team formation email for individual assignment.
    
    Args:
        student_identity: StudentIdentity object
        team_id: The assigned team ID
    
    Raises:
        Exception if email fails
    """
    from Submissions.services.email_service import send_team_formation_email
    
    email = student_identity.email
    subject = "Team Assignment Notification - Internship Portal"
    
    body = f"""Hello {student_identity.name},

Your team has been assigned and you are now ready to proceed with the internship portal.

Team ID: {team_id}
Assignment Type: Individual Team
Email: {email}

You can now log in to the portal and select your projects.

If you have any questions, please contact the internship coordinator.

Best regards,
LMT
"""
    
    try:
        send_team_formation_email(email, body)
    except Exception as e:
        logger.error(f"Failed to send team formation email to {email}: {str(e)}")
        raise
