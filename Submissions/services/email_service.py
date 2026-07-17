"""
Email service for sending evaluation reports to team members.
Supports both TEST and NORMAL modes.
"""

import os
import json
import boto3
import random
import string
import hashlib
from django.core.mail import send_mail, EmailMessage
from django.utils import timezone
from django.conf import settings
from Submissions.models import Evaluation, Student, PasswordResetOTP, Submission, TeamProject
from ..constants import CAPSTONE_REQUIRED
import logging

logger = logging.getLogger(__name__)

# Function to fetch secrets from AWS Secrets Manager (fallback to env vars)
def _get_secret(secret_name, region='ap-south-1'):
    """Fetch secret from AWS Secrets Manager, fallback to environment variables"""
    try:
        if os.getenv('AWS_ROLE_ARN'):  # Only try AWS if IRSA role is available
            client = boto3.client('secretsmanager', region_name=region)
            response = client.get_secret_value(SecretId=secret_name)
            return json.loads(response['SecretString'])
    except Exception as e:
        logger.warning(f"Could not fetch secret from AWS Secrets Manager: {e}")
    return {}

# Try to fetch secrets from AWS Secrets Manager (in K8s/EKS)
# Fallback to environment variables for K8s secrets/ConfigMap
_email_secrets = _get_secret('internship-portal-secrets')

# Dynamic functions to read settings at runtime (not cached at import time)
def _get_gmail_email():
    """Read GMAIL_EMAIL from AWS Secrets or environment at runtime."""
    return _email_secrets.get('GMAIL_USER') or os.getenv('GMAIL_EMAIL', '')

def _get_gmail_password():
    """Read GMAIL_PASSWORD from AWS Secrets or environment at runtime."""
    return _email_secrets.get('GMAIL_PASSWORD') or os.getenv('GMAIL_PASSWORD', '')

def _get_test_mode():
    """Read EMAIL_TEST_MODE from environment at runtime."""
    return os.getenv("EMAIL_TEST_MODE", "False").lower() == "true"

def _get_test_recipient():
    """Read EMAIL_TEST_RECIPIENT from environment at runtime."""
    return os.getenv("EMAIL_TEST_RECIPIENT", "")


def _get_team_member_emails(team, course):
    """
    Get all email addresses for team members in a specific course.
    Falls back to team-only query if the course filter returns no results
    (handles course name vs code mismatches or stale evaluation.course values).
    """
    team_members = Student.objects.filter(
        team=team,
        course=course
    ).select_related("identity")

    emails = [member.identity.email for member in team_members]

    if not emails:
        # Fallback: ignore course filter — all members of a team are in the same course anyway
        logger.warning(
            f"No members found for team {team.team_id} with course='{course}', "
            f"retrying without course filter"
        )
        team_members_all = Student.objects.filter(team=team).select_related("identity")
        emails = [member.identity.email for member in team_members_all]

    return list(set(emails))


def _evaluation_text_to_html(evaluation_text: str, team_id: str, project_id: str, course: str, grade: str) -> str:
    """
    Convert plain-text evaluation report into a styled HTML email body.
    Parses sections separated by '________________________________________',
    each with 'Present Implementation Comments:' and 'Scope for Improvement:' subsections.
    """
    import re

    SEPARATOR = "________________________________________"

    grade_colors = {
        "A": "#27ae60",
        "B": "#2980b9",
        "C": "#e67e22",
        "D": "#c0392b",
    }
    grade_bg = grade_colors.get((grade or "").upper(), "#7f8c8d")
    grade_display = (grade or "N/A").upper()

    raw_sections = evaluation_text.split(SEPARATOR)

    sections_html_parts = []
    for raw in raw_sections:
        raw = raw.strip()
        if not raw:
            continue

        # Skip standalone grade line
        if re.match(r'^(Overall\s+Grade|FINAL\s+GRADE)', raw, re.IGNORECASE):
            continue

        lines = raw.splitlines()
        heading = ""
        body_lines = []
        for i, line in enumerate(lines):
            if line.strip():
                heading = line.strip()
                body_lines = lines[i + 1:]
                break

        if not heading:
            continue

        body_text = "\n".join(body_lines).strip()

        # Split into "Present Implementation Comments" and "Scope for Improvement"
        impl_match = re.search(
            r'Present Implementation Comments?:(.*?)(?=Scope for Improvement:|$)',
            body_text, re.DOTALL | re.IGNORECASE
        )
        scope_match = re.search(
            r'Scope for Improvement?:(.*?)$',
            body_text, re.DOTALL | re.IGNORECASE
        )

        impl_text = impl_match.group(1).strip() if impl_match else body_text
        scope_text = scope_match.group(1).strip() if scope_match else ""

        def to_html_paras(text):
            """Convert newline-separated text to <p> tags."""
            paras = [p.strip() for p in text.split("\n") if p.strip()]
            return "".join(f'<p style="margin:0 0 8px;color:#444;font-size:14px;line-height:1.7;">{p}</p>' for p in paras)

        scope_block = ""
        if scope_text:
            scope_block = f"""
            <div style="background:#fff8f0;border-left:4px solid #e67e22;padding:12px 14px;border-radius:0 4px 4px 0;margin-top:14px;">
              <p style="margin:0 0 8px;font-size:11px;font-weight:bold;color:#e67e22;text-transform:uppercase;letter-spacing:0.8px;">Scope for Improvement</p>
              {to_html_paras(scope_text)}
            </div>"""

        sections_html_parts.append(f"""
        <div style="margin-bottom:20px;border:1px solid #e0e0e0;border-radius:6px;overflow:hidden;">
          <div style="background:#2c3e50;padding:12px 18px;">
            <h3 style="margin:0;font-size:13px;font-weight:bold;color:#ffffff;letter-spacing:0.6px;">{heading}</h3>
          </div>
          <div style="padding:16px 18px;">
            <p style="margin:0 0 8px;font-size:11px;font-weight:bold;color:#2c3e50;text-transform:uppercase;letter-spacing:0.8px;">Present Implementation</p>
            {to_html_paras(impl_text)}
            {scope_block}
          </div>
        </div>""")

    sections_combined = "\n".join(sections_html_parts)

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:20px;background:#f0f2f5;font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:680px;margin:0 auto;background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);">

    <!-- Header -->
    <div style="background:#1a1a2e;padding:28px 32px;">
      <h1 style="margin:0;font-size:20px;font-weight:bold;color:#ffffff;">Project Evaluation Results</h1>
      <p style="margin:8px 0 0;font-size:13px;color:#aaaaaa;">
        Team:&nbsp;<strong style="color:#ffffff;">{team_id}</strong>&nbsp;&nbsp;|&nbsp;&nbsp;
        Project:&nbsp;<strong style="color:#ffffff;">{project_id}</strong>&nbsp;&nbsp;|&nbsp;&nbsp;
        Course:&nbsp;<strong style="color:#ffffff;">{course}</strong>
      </p>
    </div>

    <!-- Grade banner -->
    <div style="background:{grade_bg};padding:16px 32px;text-align:center;">
      <span style="font-size:26px;font-weight:bold;color:#ffffff;letter-spacing:3px;">Overall Grade: {grade_display}</span>
    </div>

    <!-- Body -->
    <div style="padding:28px 32px;">
      <p style="margin:0 0 8px;font-size:15px;color:#333333;">Dear Team Member,</p>
      <p style="margin:0 0 28px;font-size:14px;color:#555555;line-height:1.6;">
        Please find below the detailed evaluation feedback for your project submission.
        Review each section carefully and use the improvement suggestions to guide your next steps.
      </p>

      {sections_combined}

      <!-- Footer -->
      <div style="border-top:1px solid #eeeeee;padding-top:18px;margin-top:8px;">
        <p style="margin:0;font-size:13px;color:#888888;line-height:1.8;">
          Best regards,<br>
          <strong style="color:#444444;">LMT</strong>
        </p>
      </div>
    </div>
  </div>
</body>
</html>"""


def send_evaluation_email(evaluation):
    """
    Send evaluation report email to all team members.
    Sends an HTML email with a plain-text fallback.

    Args:
        evaluation: Evaluation object

    Returns:
        True if sent successfully, False otherwise
    """
    try:
        from django.core.mail import EmailMultiAlternatives

        team = evaluation.team
        project = evaluation.project
        course = evaluation.course

        # Hard fail if no evaluation report path stored
        if not evaluation.evaluation_report:
            logger.error(f"No evaluation report for evaluation {evaluation.id}")
            return False

        # evaluation_report is a TextField that may contain either:
        #   (a) A file path  e.g. "Evaluations/TEAM/PROJ/ts_evaluation.txt"
        #   (b) Inline text  (multi-line, contains the separator line)
        # Detect by checking for newlines — file paths are always single-line.
        raw = (evaluation.evaluation_report or "").strip()
        if not raw:
            logger.error(f"No evaluation report content for evaluation {evaluation.id}")
            return False

        if "\n" in raw:
            # Already inline text
            evaluation_text = raw
        else:
            # Looks like a file path — read from disk
            evaluation_text = None
            try:
                from django.core.files.storage import default_storage
                with default_storage.open(raw, 'rb') as f:
                    evaluation_text = f.read().decode('utf-8')
            except Exception:
                try:
                    full_path = os.path.join(settings.MEDIA_ROOT, raw)
                    with open(full_path, 'r', encoding='utf-8') as f:
                        evaluation_text = f.read()
                except Exception as fallback_err:
                    logger.error(f"Could not read evaluation report file '{raw}': {fallback_err}")
                    return False

        if not evaluation_text or not evaluation_text.strip():
            logger.error(f"Evaluation report is empty for evaluation {evaluation.id}")
            return False

        # Determine recipients
        if _get_test_mode():
            test_recipient = _get_test_recipient()
            if not test_recipient:
                logger.error("EMAIL_TEST_MODE enabled but EMAIL_TEST_RECIPIENT not set in .env")
                return False
            recipients = [test_recipient]
            logger.info(f"[TEST MODE] Sending email to: {test_recipient}")
        else:
            recipients = _get_team_member_emails(team, course)
            if not recipients:
                logger.warning(f"No team members found for team {team.team_id}, course {course}")
                return False
            logger.info(f"[NORMAL MODE] Sending email to: {recipients}")

        grade = (evaluation.evaluation_grade or "").strip()
        subject = f"Project Evaluation Results - {project.project_id} - {team.team_id}"

        # Plain-text fallback
        plain_body = (
            f"Dear Team Member,\n\n"
            f"Please find below the evaluation results for {project.project_id} (Team: {team.team_id}):\n\n"
            f"{evaluation_text}\n\n"
            f"Best regards,\nLMT"
        )

        # HTML version
        html_body = _evaluation_text_to_html(
            evaluation_text,
            team_id=team.team_id,
            project_id=project.project_id,
            course=course,
            grade=grade,
        )

        email_message = EmailMultiAlternatives(
            subject=subject,
            body=plain_body,
            from_email=_get_gmail_email(),
            to=recipients,
        )
        email_message.attach_alternative(html_body, "text/html")
        email_message.send(fail_silently=False)

        logger.info(f"Evaluation email sent for {team.team_id} - {project.project_id}")
        return True

    except Exception as e:
        logger.error(f"Error sending evaluation email: {str(e)}")
        return False


def send_submission_received_email(team, project, course):
    """
    Send email to all team members notifying them that a submission has been received.
    
    Args:
        team: Team object
        project: ProjectRegistry object
        course: Course code string
        
    Returns:
        (success: bool, error_msg: str or None)
    """
    try:
        # Get recipients
        if _get_test_mode():
            test_recipient = _get_test_recipient()
            if not test_recipient:
                return False, "Test mode enabled but no test recipient configured"
            recipients = [test_recipient]
            logger.info(f"[TEST MODE] Sending submission received email to: {test_recipient}")
        else:
            recipients = _get_team_member_emails(team, course)
            if not recipients:
                return False, f"No team members found for team {team.team_id}"
            logger.info(f"[NORMAL MODE] Sending submission received email to: {recipients}")
        
        # Format email
        subject = f"Submission Valid - {project.project_id} - Team {team.team_id}"
        body = f"""
Dear Team Members,

We have received your submission for project {project.project_id} ({project.project_name}).

Team: {team.team_id}
Course: {course}

The project submission is valid and the results will be sent in email in 3 working days.

Best regards,
LMT
"""
        
        # Send email
        email_message = EmailMessage(
            subject=subject,
            body=body,
            from_email=_get_gmail_email(),
            to=recipients,
        )
        
        email_message.send(fail_silently=False)
        logger.info(f"Submission received email sent successfully for team {team.team_id} - {project.project_id}")
        return True, None
        
    except Exception as e:
        logger.error(f"Error sending submission received email: {str(e)}")
        return False, "Can't send emails now. Please try again later."


def send_capstones_completed_email(team, course):
    """
    Send email to all team members notifying them that all capstones are completed
    and they can now pick client projects.
    """
    try:
        if _get_test_mode():
            test_recipient = _get_test_recipient()
            if not test_recipient:
                return False
            recipients = [test_recipient]
        else:
            recipients = _get_team_member_emails(team, course)
            
        if not recipients:
            return False
            
        subject = f"Capstone Projects Completed - Team {team.team_id}"
        body = f"""
Dear Team Members,

Congratulations! All your capstone projects have been successfully evaluated.

The client projects have to be chosen now. Please log in to the portal and select your next project to continue your internship.

Best regards,
LMT
"""
        email_message = EmailMessage(
            subject=subject,
            body=body,
            from_email=_get_gmail_email(),
            to=recipients,
        )
        email_message.send(fail_silently=False)
        logger.info(f"Capstone completion email sent for team {team.team_id}")
        return True
    except Exception as e:
        logger.error(f"Error sending capstone completion email: {str(e)}")
        return False


def send_project_picked_email(team, picked_projects, course):
    """
    Send email to all team members notifying them that projects have been picked.
    
    Args:
        team: Team object
        picked_projects: List of ProjectRegistry objects
        course: Course code string
        
    Returns:
        (success: bool, error_msg: str or None)
    """
    try:
        # Get recipients
        if _get_test_mode():
            test_recipient = _get_test_recipient()
            if not test_recipient:
                return False, "Test mode enabled but no test recipient configured"
            recipients = [test_recipient]
            logger.info(f"[TEST MODE] Sending project picked email to: {test_recipient}")
        else:
            recipients = _get_team_member_emails(team, course)
            if not recipients:
                return False, f"No team members found for team {team.team_id}"
            logger.info(f"[NORMAL MODE] Sending project picked email to: {recipients}")
        
        # Format project list
        project_list = "\n".join([f"- {p.project_id}: {p.project_name}" for p in picked_projects])
        
        # Format email
        subject = f"Projects Picked - Team {team.team_id} - {course}"
        body = f"""
Dear Team Members,

The following projects have been chosen for the team for {course}:

{project_list}

Please review these assignments and prepare your submissions accordingly.

Best regards,
LMT
"""
        
        # Send email
        email_message = EmailMessage(
            subject=subject,
            body=body,
            from_email=_get_gmail_email(),
            to=recipients,
        )
        
        email_message.send(fail_silently=False)
        logger.info(f"Project picked email sent successfully for team {team.team_id}")
        return True, None
        
    except Exception as e:
        logger.error(f"Error sending project picked email: {str(e)}")
        return False, "Can't send emails now. Please try again later."


def _post_evaluation_status_transition(evaluation, team_project):
    """
    After a TeamProject is marked 'evaluated', update Student statuses and
    send completion emails as appropriate.

    Handles three cases:
      1. Normal single-phase course (CDS/CDE/CDA/legacy AIE) — check if all
         capstones are done and send the capstone completion email.
      2. AIE two-phase CDS leg — CDS client evaluated → set CDSCycleComplete.
      3. AIE two-phase AIE leg — AIE client evaluated → set AllCompleted.
    """
    from Submissions.models import Student, TeamProject as TP
    from Submissions.constants import CAPSTONE_REQUIRED, AIE_PHASE2_STATUSES

    team = evaluation.team
    project = team_project.project
    eval_course = evaluation.course  # the course on the Evaluation record (AIE for AIE teams)

    # Detect whether this team is in two-phase mode:
    # two-phase iff course==AIE AND no existing AIE TeamProject rows at the time
    # the CDS cycle began — but by now they may have them. So we inspect project.course.
    is_aie_team = (eval_course == "AIE")
    project_course = project.course  # the actual project's course (may be CDS for AIE students)
    project_type = project.project_type

    if is_aie_team and project_course == "CDS":
        # ── AIE two-phase: CDS leg ───────────────────────────────────────
        if project_type == "Capstone":
            # Check if all 4 CDS capstones are now evaluated
            cds_required = CAPSTONE_REQUIRED["CDS"]
            cds_capstones = TP.objects.filter(
                team=team, project__course="CDS", project__project_type="Capstone"
            )
            if (cds_capstones.count() == cds_required and
                    cds_capstones.filter(status="evaluated").count() == cds_required):
                send_capstones_completed_email(team, eval_course)

        elif project_type == "Client":
            # CDS client evaluated → whole CDS cycle is complete
            Student.objects.filter(
                team=team,
                course=eval_course,
                status__in=["ClientProjectAssigned", "ReadyForClientPick"],
            ).update(status="CDSCycleComplete")
            logger.info(f"Set CDSCycleComplete for team {team.team_id}")

    elif is_aie_team and project_course == "AIE":
        # ── AIE two-phase: AIE leg ───────────────────────────────────────
        if project_type == "Capstone":
            aie_required = CAPSTONE_REQUIRED["AIE"]
            aie_capstones = TP.objects.filter(
                team=team, project__course="AIE", project__project_type="Capstone"
            )
            if (aie_capstones.count() == aie_required and
                    aie_capstones.filter(status="evaluated").count() == aie_required):
                send_capstones_completed_email(team, eval_course)

        elif project_type == "Client":
            # AIE client evaluated → all done
            Student.objects.filter(
                team=team,
                course=eval_course,
                status="AIEClientAssigned",
            ).update(status="AllCompleted")
            logger.info(f"Set AllCompleted (AIE two-phase) for team {team.team_id}")

    else:
        # ── Normal single-phase ──────────────────────────────────────────
        if project_type == "Capstone":
            required = CAPSTONE_REQUIRED.get(eval_course, 0)
            if required > 0:
                capstones = TP.objects.filter(
                    team=team,
                    project__course=eval_course,
                    project__project_type="Capstone",
                )
                if (capstones.count() == required and
                        capstones.filter(status="evaluated").count() == required):
                    send_capstones_completed_email(team, eval_course)


def process_visible_evaluations():
    """
    Scheduled task: Find evaluations that are now visible, reviewed, and send emails.
    Returns a dict: {"sent": N, "failed": N, "failures": [{"id": ..., "team": ..., "project": ..., "reason": ...}]}
    """
    sent = 0
    failures = []

    try:
        now = timezone.now()

        from django.db.models import Q
        import time

        pending_evaluations = list(
            Evaluation.objects.filter(
                # Either visibility_after is set and in the past,
                # OR it is null/unset (trainer-reviewed evals have no visibility_after)
                Q(visibility_after__lte=now) | Q(visibility_after__isnull=True),
                reviewed=True,
                email_sent=False,
            ).select_related("team", "project")
        )

        logger.info(f"Found {len(pending_evaluations)} evaluations to process")

        for evaluation in pending_evaluations:
            try:
                success = send_evaluation_email(evaluation)
            except Exception as exc:
                success = False
                reason = str(exc)
            else:
                reason = "send_evaluation_email returned False" if not success else None

            if success:
                evaluation.email_sent = True
                evaluation.save(update_fields=["email_sent"])
                sent += 1

                # Update TeamProject status to "evaluated"
                team_project = evaluation.project.team_projects.filter(
                    team=evaluation.team
                ).first()

                if team_project and team_project.status == "submitted":
                    team_project.status = "evaluated"
                    team_project.save(update_fields=["status", "updated_at"])
                    logger.info(f"Updated TeamProject status to 'evaluated': {team_project.id}")

                    _post_evaluation_status_transition(evaluation, team_project)

                # Throttle sends to avoid Gmail closing the SMTP connection
                time.sleep(1.5)
            else:
                logger.error(f"Failed to send email for evaluation {evaluation.id}: {reason}")
                failures.append({
                    "id": evaluation.id,
                    "team": evaluation.team.team_id,
                    "project": evaluation.project.project_id,
                    "reason": reason,
                })

        logger.info(f"Done — sent={sent}, failed={len(failures)}")
        return {"sent": sent, "failed": len(failures), "failures": failures}

    except Exception as e:
        logger.error(f"Error in process_visible_evaluations: {str(e)}")
        return {"sent": sent, "failed": len(failures), "failures": failures, "error": str(e)}


def process_team_formation_emails():
    """
    Team formation emails are disabled — teams are formed silently.
    Kept as a no-op so existing call sites (management command, admin views) don't break.
    Returns 0 to indicate no emails were sent.
    """
    logger.info("Team formation emails are disabled. Teams are formed silently.")
    return 0


def send_team_formation_email(team, student_emails):
    """
    Send team formation notification email to all team members.
    
    Args:
        team: Team object
        student_emails: List of email addresses of team members
        
    Returns:
        bool: True if sent successfully, False otherwise
    """
    try:
        if _get_test_mode():
            test_recipient = _get_test_recipient()
            if not test_recipient:
                logger.error("EMAIL_TEST_MODE enabled but EMAIL_TEST_RECIPIENT not set")
                return False
            recipients = [test_recipient]
            logger.info(f"[TEST MODE] Sending team formation email to: {test_recipient}")
        else:
            recipients = student_emails
            if not recipients:
                logger.warning(f"No recipients found for team {team.team_id}")
                return False
            logger.info(f"[NORMAL MODE] Sending team formation email to: {recipients}")
        
        # Format email
        team_member_list = "\n".join([f"- {email}" for email in student_emails])
        
        subject = f"Team Formation - {team.team_id}"
        body = f"""
Dear Team Member,

Congratulations! Your team has been formed and assigned a Team ID.

Team ID: {team.team_id}
Course: {team.course}

Team Members:
{team_member_list}

Please use your Team ID for all future submissions and communications related to this course.

If you have any questions, please contact the Internship Portal support.

Best regards,
LMT
"""
        
        # Send email
        email_message = EmailMessage(
            subject=subject,
            body=body,
            from_email=_get_gmail_email(),
            to=recipients,
        )
        
        email_message.send(fail_silently=False)
        logger.info(f"Team formation email sent successfully for team {team.team_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error sending team formation email for {team.team_id}: {str(e)}")
        return False


def generate_otp():
    """Generate a random 6-digit OTP"""
    return ''.join(random.choices(string.digits, k=6))


def _hash_otp(otp_code):
    """Hash OTP using SHA-256"""
    return hashlib.sha256(otp_code.encode()).hexdigest()


def send_otp_email(email, otp):
    """
    Send OTP email to the provided email address.
    
    Args:
        email: Recipient email address
        otp: The plain OTP code to send
        
    Returns:
        (success: bool, error_msg: str or None)
    """
    try:
        # Determine recipients based on mode
        if _get_test_mode():
            test_recipient = _get_test_recipient()
            if not test_recipient:
                return False, "Test mode enabled but no test recipient configured"
            recipients = [test_recipient]
            logger.info(f"[TEST MODE] Sending OTP email to: {test_recipient}")
        else:
            recipients = [email]
            logger.info(f"[NORMAL MODE] Sending OTP email to: {email}")
        
        # Format email
        subject = "Your Password Reset OTP - Internship Portal"
        body = f"""
Dear User,

Your One-Time Password (OTP) for password reset/change is:

{otp}

This OTP is valid for 10 minutes only. Please do not share this code with anyone.

If you did not request this OTP, please ignore this email.

Best regards,
Internship Portal
"""
        
        # Send email
        email_message = EmailMessage(
            subject=subject,
            body=body,
            from_email=_get_gmail_email(),
            to=recipients,
        )
        
        email_message.send(fail_silently=False)
        logger.info(f"OTP email sent successfully to {email}")
        return True, None
        
    except Exception as e:
        logger.error(f"Error sending OTP email: {str(e)}")
        return False, "Can't send emails now. Please try again later."


def create_and_send_otp(email):
    """
    Create an OTP record and send it via email.
    OTP is hashed with SHA-256 before storing in database.
    
    Args:
        email: Email address to send OTP to
        
    Returns:
        (success: bool, error_msg: str or None)
    """
    try:
        # Delete any previous unused OTPs for this email
        PasswordResetOTP.objects.filter(email=email, is_used=False).delete()
        
        # Generate new plain OTP
        otp_code = generate_otp()
        
        # Hash the OTP with SHA-256
        hashed_otp = _hash_otp(otp_code)
        
        # Save hashed OTP to database
        PasswordResetOTP.objects.create(
            email=email,
            otp_code=hashed_otp,
            is_used=False,
            resend_count=0,
            last_resent_at=None
        )
        
        # Send plain OTP via email
        success, error_msg = send_otp_email(email, otp_code)
        
        if success:
            logger.info(f"OTP created and sent for {email}")
            return True, None
        else:
            # Delete the OTP record if email sending failed
            PasswordResetOTP.objects.filter(email=email, otp_code=hashed_otp).delete()
            return False, error_msg
            
    except Exception as e:
        logger.error(f"Error creating and sending OTP: {str(e)}")
        return False, "Can't send emails now. Please try again later."


def resend_otp(email):
    """
    Resend OTP to user with rate limiting and attempt tracking.
    Max 3 resends allowed, with 2-minute wait between resends.
    
    Args:
        email: Email address to resend OTP to
        
    Returns:
        (success: bool, error_msg: str or None, resends_remaining: int or None)
    """
    try:
        # Find the OTP record
        otp_record = PasswordResetOTP.objects.filter(
            email=email,
            is_used=False
        ).first()
        
        if not otp_record:
            return False, "No OTP found. Please start the process again.", None
        
        # Check if can resend
        can_resend, error_msg = otp_record.can_resend()
        if not can_resend:
            return False, error_msg, otp_record.resend_count
        
        # Generate new plain OTP
        otp_code = generate_otp()
        
        # Hash the new OTP
        hashed_otp = _hash_otp(otp_code)
        
        # Update record with new hashed OTP and resend tracking
        otp_record.otp_code = hashed_otp
        otp_record.resend_count += 1
        otp_record.last_resent_at = timezone.now()
        otp_record.created_at = timezone.now()  # Reset expiration timer
        otp_record.save(update_fields=["otp_code", "resend_count", "last_resent_at", "created_at"])
        
        # Send new OTP via email
        success, error_msg = send_otp_email(email, otp_code)
        
        if success:
            resends_remaining = 3 - otp_record.resend_count
            logger.info(f"OTP resent for {email} (Resend {otp_record.resend_count}/3)")
            return True, None, resends_remaining
        else:
            # Revert resend count if email failed
            otp_record.resend_count -= 1
            otp_record.save(update_fields=["resend_count"])
            return False, error_msg, otp_record.resend_count
            
    except Exception as e:
        logger.error(f"Error resending OTP: {str(e)}")
        return False, "Can't send emails now. Please try again later.", None


def verify_otp(email, otp_code):
    """
    Verify an OTP code for the given email.
    Compares SHA-256 hash of provided OTP with stored hash.
    
    Args:
        email: Email address
        otp_code: The plain OTP code to verify
        
    Returns:
        (valid: bool, error_msg: str or None)
    """
    try:
        # Hash the provided OTP
        hashed_input = _hash_otp(otp_code)
        
        # Find the OTP record with matching hash
        otp_record = PasswordResetOTP.objects.filter(
            email=email,
            otp_code=hashed_input,
            is_used=False
        ).first()
        
        if not otp_record:
            return False, "Invalid OTP. Please try again."
        
        # Check if expired
        if otp_record.is_expired():
            otp_record.delete()
            return False, "OTP has expired. Please request a new one."
        
        # Mark as used
        otp_record.is_used = True
        otp_record.save(update_fields=["is_used"])
        
        logger.info(f"OTP verified successfully for {email}")
        return True, None
        
    except Exception as e:
        error_msg = f"Error verifying OTP: {str(e)}"
        logger.error(error_msg)
        return False, error_msg


def send_team_preference_acknowledgment(student_email, team_preference, course, preference_emails=None):
    """
    Send acknowledgment email to student after they submit team preference.
    Different messages for each preference type.
    
    Args:
        student_email: Student email address
        team_preference: Choice (individual/assign_team)  # DISABLED: i_have_team - NOT IN FUNCTION
        course: Course code
        preference_emails: List of teammate emails (DISABLED - NOT IN FUNCTION)
        
    Returns:
        (success: bool, error_msg: str or None)
    """
    try:
        # Determine recipients based on test mode
        if _get_test_mode():
            test_recipient = _get_test_recipient()
            if not test_recipient:
                return False, "Test mode enabled but no test recipient configured"
            recipients = [test_recipient]
            logger.info(f"[TEST MODE] Sending preference ack email to: {test_recipient}")
        else:
            recipients = [student_email]
            logger.info(f"[NORMAL MODE] Sending preference ack email to: {student_email}")
        
        # Format email based on preference type
        if team_preference == "individual":
            subject = f"Team Preference Received - {course}"
            body = f"""
Dear Student,

Thank you for submitting your team preference for {course}.

Your Selection: Individual Team

We will process your request and assign you a unique Team ID shortly. You'll receive a notification with your Team ID once it's assigned.

Best regards,
LMT
"""
        
        # DISABLED FOR NOW: i_have_team email - NOT IN FUNCTION
        # elif team_preference == "i_have_team":
        #     teammate_list = "\n".join([f"- {email}" for email in (preference_emails or [])])
        #     subject = f"Team Preference Received - {course}"
        #     body = f"""
        # Dear Student,
        # 
        # Thank you for submitting your team preference for {course}.
        # 
        # Your Selection: I Have a Team
        # Team Members (including you):
        # {teammate_list}
        # 
        # IMPORTANT: All other team members must register to the portal and complete their enrollment in {course} for your team to be processed.
        # 
        # Next Steps:
        # 1. Make sure all teammates register to the portal
        # 2. All teammates must be enrolled in course {course}
        # 3. Once all conditions are met, your team will automatically be assigned a Team ID
        # 
        # You'll receive a notification with your Team ID once your team is fully formed.
        # 
        # Best regards,
        # Internship Portal Team
        # """
        elif team_preference == "assign_team":
            subject = f"Team Preference Received - {course}"
            body = f"""
Dear Student,

Thank you for submitting your team preference for {course}.

Your Selection: Assign a Team

We will automatically form teams of 4 students based on course enrollment and preferences. This process may take some time as we wait for sufficient students to register.



You'll receive a notification with your Team ID and team members once your team is formed.

Best regards,
LMT
"""
        
        else:
            return False, f"Unknown team preference: {team_preference}"
        
        # Send email
        email_message = EmailMessage(
            subject=subject,
            body=body,
            from_email=_get_gmail_email(),
            to=recipients,
        )
        
        email_message.send(fail_silently=False)
        logger.info(f"Team preference acknowledgment sent to {student_email} for preference: {team_preference}")
        return True, None
        
    except Exception as e:
        logger.error(f"Error sending team preference acknowledgment: {str(e)}")
        return False, "Can't send emails now. Please try again later."




def send_everyone_declined(requester_email, requester_name, course):
    """
    Send email to A when all teammates have declined.
    A will be assigned as an individual (single-person team).
    
    Args:
        requester_email: A's email
        requester_name: A's name
        course: Course code
    
    Returns:
        (success: bool, error_msg: str or None)
    """
    try:
        if _get_test_mode():
            test_recipient = _get_test_recipient()
            if not test_recipient:
                return False, "Test mode enabled but no test recipient configured"
            recipients = [test_recipient]
            logger.info(f"[TEST MODE] Sending everyone declined to: {test_recipient}")
        else:
            recipients = [requester_email]
            logger.info(f"[NORMAL MODE] Sending everyone declined to: {requester_email}")
        
        subject = f"Team Assignment Update - {course}"
        body = f"""
Dear {requester_name},

Unfortunately, none of your invited teammates have accepted your team request.

As a result, you will be assigned as an individual team (1 person). Your unique Team ID will be assigned shortly and you'll receive a separate notification with the details.

You can still work on the assigned projects and submit your work individually.

Best regards,
LMT
"""
        
        email_message = EmailMessage(
            subject=subject,
            body=body,
            from_email=_get_gmail_email(),
            to=recipients,
        )
        
        email_message.send(fail_silently=False)
        logger.info(f"Everyone declined notification sent to {requester_email}")
        return True, None
        
    except Exception as e:
        logger.error(f"Error sending everyone declined notification: {str(e)}")
        return False, "Can't send emails now. Please try again later."
