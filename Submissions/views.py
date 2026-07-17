import hashlib
from functools import wraps
import json
import os
import re
import logging

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.db import IntegrityError
from django.http import JsonResponse, HttpResponse
from django.db.models import Count, Q, Prefetch
from django.utils import timezone
from datetime import timedelta
from django.contrib.auth.hashers import make_password, check_password

from .models import *
from ControlCenter.models import AdminAccount
from .services.validation import SubmissionValidator
from .services.error_formatter import format_validation_error
from .services.Parsers.payload_assembler import assemble_and_evaluate
from .services.email_service import (
    send_project_picked_email, 
    send_submission_received_email, 
    send_team_preference_acknowledgment,
    send_everyone_declined,
)


logger = logging.getLogger(__name__)

# ============================================================================
# CONSTANTS
# ============================================================================

from .constants import CAPSTONE_REQUIRED, COURSE_CODES, COURSE_ALIASES, AIE_PHASE2_STATUSES

SESSION_KEYS = ["user_email", "student_name", "course", "team_id", "enrollment_id"]

TEAMID_RE = re.compile(r"^PTID-(?P<course>[A-Z]{2,5})-", re.IGNORECASE)


def course_from_team_id(team_id: str, default="CDS") -> str:
    if not team_id:
        return default
    m = TEAMID_RE.match(team_id.strip())
    if not m:
        return default
    code = m.group("course").upper()
    code = COURSE_ALIASES.get(code, code)
    return code if code in COURSE_CODES else default


def _aie_two_phase_leg(enrollment, team):
    """
    Determine which leg of the two-phase AIE pipeline this student is on.

    Returns:
        "cds"   — student has no AIE projects yet; must complete CDS cycle first
        "aie"   — student is in AIE phase (CDSCycleComplete or beyond)
        None    — not applicable (not an AIE team, or legacy AIE with AIE projects)

    Logic:
        - Only applies when enrollment.course == "AIE"
        - If team already has TeamProject rows where project.course == "AIE"
          → legacy path (old students); return None so they use the normal flow
        - Otherwise check enrollment status:
            CDSCycleComplete / AIECapstoneAssigned / AIEReadyForClientPick
            / AIEClientAssigned → "aie"
            anything else → "cds"
    """
    if enrollment.course != "AIE":
        return None

    has_aie_projects = TeamProject.objects.filter(
        team=team, project__course="AIE"
    ).exists()

    if has_aie_projects:
        return None  # legacy — has already picked AIE projects

    if enrollment.status in AIE_PHASE2_STATUSES:
        return "aie"

    return "cds"


# ============================================================================
# DECORATORS
# ============================================================================

def student_required(view_func):
    """Ensure student session exists, redirect to home if not"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user_email = request.session.get("user_email")
        if not user_email:
            messages.warning(request, "Please enter your email to continue.")
            return redirect("home")
        return view_func(request, *args, **kwargs)
    return wrapper


# ============================================================================
# HELPER FUNCTIONS (SESSION / LOOKUPS)
# ============================================================================

def reset_session(request):
    for k in SESSION_KEYS:
        request.session.pop(k, None)
    messages.info(request, "Session cleared. Please log in again.")
    return redirect("home")


def _set_student_session(request, *, ident, enrollment, team_id: str = None):
    """
    Single place to set the 5 session keys used everywhere.
    Does not change the session variable names already used in templates/views.
    """
    request.session["user_email"] = ident.email
    request.session["student_name"] = ident.name
    request.session["course"] = enrollment.course
    request.session["enrollment_id"] = enrollment.id
    request.session["team_id"] = team_id or (enrollment.team.team_id if enrollment.team else None)


def _get_identity(email: str):
    return StudentIdentity.objects.filter(email=email).first()


def _get_enrollments_with_team(ident):
    return (
        Student.objects
        .select_related("identity", "team")
        .filter(identity=ident, team__isnull=False)
        .order_by("-id")
    )


def _build_enrollment_choices(enrollments):
    return [{"id": e.id, "course": e.course, "team_id": e.team.team_id if e.team else None} for e in enrollments]


def _get_selected_enrollment(email: str, enrollment_id: str):
    return (
        Student.objects
        .select_related("identity", "team")
        .filter(id=enrollment_id, identity__email=email, team__isnull=False)
        .exclude(status="LegacyArchived")
        .first()
    )


def _get_team_assignment_for_identity(existing_identity):
    return Team.objects.filter(student=existing_identity).first()


def _get_team_assignment_for_email(email: str):
    return Team.objects.filter(student__email=email).first()



def _team_id_from_email(email: str) -> str:
    """
    Deterministic, unique team id from email.
    Example: TEAM-SEETHAHARITH-4F8A2C
    """
    email = (email or "").strip().lower()
    local = email.split("@")[0] if "@" in email else email
    local_clean = "".join(ch for ch in local.upper() if ch.isalnum())[:16] or "STUDENT"
    h = hashlib.sha1(email.encode("utf-8")).hexdigest()[:6].upper()
    return f"TEAM-{local_clean}-{h}"


def _post_login_redirect(enrollment, request=None):
    """
    Determine where a student should go after login/selecting an enrollment.
    """
    # 1. If student already has a team assigned in their enrollment (inviter or invitee)
    if enrollment.team:
        if not enrollment.team.team_id:
            if request:
                messages.info(request, "Your team ID has not yet been assigned. Please wait for an email notification.")
            return "team_waiting"
        return "pick_projects"

    # 2. Fallback: team preference record exists but enrollment.team FK not yet written back.
    # A team_id on the preference means admin has done team formation, but the student
    # row hasn't been updated yet. Show pending — do NOT send to pick_projects until
    # enrollment.team is formally set, to prevent project picks landing on the wrong team.
    team_pref = Team.objects.filter(student__email=enrollment.identity.email, course=enrollment.course).first()

    if team_pref:
        return "team_waiting"

    # 3. New student, needs to select team preference first
    return "team_preference"


# DISABLED FOR NOW: i_have_team option - NOT IN FUNCTION
# def _find_pending_team_requests(student_email: str, course: str):
#     """
#     Find all pending i_have_team preferences where this student's email is listed.
#     Returns list of Team records where student is invited.
#     """
#     return Team.objects.filter(
#         team_preference="i_have_team",
#         team_id__isnull=True,  # Team not yet formed
#         course=course,
#         preference_emails__contains=student_email,
#     )


def _check_all_teammates_responded(team_record):
    """
    Check if all of team_record's preference_emails have either:
    1. Accepted (created Student enrollment pointing to this team), OR
    2. Declined/moved on (created their own Team preference)
    
    Returns (all_responded: bool, acceptance_status: dict)
    acceptance_status = {
        'accepted': [list of emails who accepted],
        'declined': [list of emails who declined/moved on],
        'pending': [list of emails not yet registered]
    }
    """
    acceptance_status = {
        'accepted': [],
        'declined': [],
        'pending': [],
    }
    
    preference_emails = team_record.preference_emails or []
    
    for email in preference_emails:
        # Check if they accepted by creating Student enrollment pointing to this team
        student_in_team = Student.objects.filter(
            identity__email=email,
            course=team_record.course,
            team=team_record,
        ).exists()
        
        if student_in_team:
            acceptance_status['accepted'].append(email)
            continue
        
        # Check if they registered but created their own Team preference instead
        owns_team = Team.objects.filter(
            student__email=email,
            course=team_record.course,
        ).exists()
        
        if owns_team:
            acceptance_status['declined'].append(email)
            continue
        
        # Still pending registration
        acceptance_status['pending'].append(email)
    
    # All responded if no pending emails left
    all_responded = len(acceptance_status['pending']) == 0
    
    return all_responded, acceptance_status


# ============================================================================
# VIEWS
# ============================================================================

def home(request):
    """
    Home: email entry with smart routing
    - If admin/superadmin email → redirect to admin portal (password required)
    - If student email → check portal password status
    Note: Superadmin can also login directly at /portal/
    """
    if request.method == "GET":
        return render(request, "Submissions/home.html")

    email = (request.POST.get("email") or "").strip().lower()
    # Check if this is an admin user (admin OR superadmin)
    admin = AdminAccount.objects.filter(
        email=email,
        is_active=True
    ).first()

    if admin:
        # Redirect to the Streamlit admin portal
        return redirect("http://localhost:8501")

    ident = _get_identity(email)
    if not ident:
        # BLOCK NEW USER - deny access
        messages.error(request, "Access Denied. Your email is not registered in our internship program. Please contact the administrator.")
        return redirect("home")

    # Block legacy students — all their enrollments are LegacyArchived
    enrollments = Student.objects.filter(identity=ident)
    if enrollments.exists() and enrollments.exclude(status="LegacyArchived").count() == 0:
        messages.error(request, "Your account is not active on this portal. Please contact the internship team.")
        return redirect("home")

    # NEW: Check portal password status
    if not ident.portal_password:
        # Password is NULL - redirect to create password page
        request.session["temp_email"] = email
        return redirect("create_password")
    else:
        # Password exists - redirect to verify password page
        request.session["temp_email"] = email
        return redirect("verify_password")

    enrollments = _get_enrollments_with_team(ident)
    count = enrollments.count()

    if count == 0:
        messages.error(request, "No active enrollments found for your identity. Please contact support.")
        return redirect("home")

    if count == 1:
        enrollment = enrollments.first()
        _set_student_session(request, ident=ident, enrollment=enrollment)
        return redirect(_post_login_redirect(enrollment))

    return redirect(f"/register/?email={email}&mode=select")




def handle_team_acceptance(request):
    """
    Handle team acceptance response after registration.
    Student says YES or NO to joining a requester's team.
    
    POST params:
    - email: Student email (the one being asked)
    - team_id: Team ID (requester's team record)
    - response: 'yes' or 'no'
    """
    if request.method == "GET":
        messages.error(request, "Invalid request method.")
        return redirect("home")
    
    email = (request.POST.get("email") or "").strip().lower()
    team_id = (request.POST.get("team_id") or "").strip()
    response = (request.POST.get("response") or "").strip().lower()
    
    if not all([email, team_id, response]) or response not in ['yes', 'no']:
        messages.error(request, "Invalid team acceptance request.")
        return redirect("home")
    
    try:
        # Get the team request record
        team_request = Team.objects.filter(id=team_id).first()
        if not team_request:
            messages.error(request, "Team request not found.")
            return redirect("home")
        
        # Get student identity and enrollment
        student_ident = StudentIdentity.objects.filter(email=email).first()
        if not student_ident:
            messages.error(request, "Student identity not found.")
            return redirect("home")
        
        student_enrollment = Student.objects.filter(
            identity=student_ident,
            course=team_request.course
        ).first()
        if not student_enrollment:
            messages.error(request, "Student enrollment not found.")
            return redirect("home")
        
        # Get requester info
        requester_ident = StudentIdentity.objects.filter(email=team_request.student.email).first()
        
        with transaction.atomic():
            if response == 'yes':
                # Accept: Update Student to point to requester's team
                student_enrollment.team = team_request
                student_enrollment.save()
                
                # Get updated team status
                _, acceptance_status = _check_all_teammates_responded(team_request)
                accepted_emails = acceptance_status['accepted']
                pending_emails = acceptance_status['pending']
                declined_emails = acceptance_status['declined']
                
                # Build team members list with requester
                team_members_accepted = [team_request.student.email] + accepted_emails
                team_members_declined = declined_emails
                team_members_pending = pending_emails
                
                # Set session and show team status page
                _set_student_session(request, ident=student_ident, enrollment=student_enrollment)
                
                return render(request, "Submissions/team_status.html", {
                    "message": f"Great! You've joined {requester_ident.name if requester_ident else 'your teammate'}'s team.",
                    "requester_name": requester_ident.name if requester_ident else "Teammate",
                    "course": team_request.course,
                    "team_members_accepted": team_members_accepted,
                    "team_members_pending": team_members_pending,
                    "team_members_declined": team_members_declined,
                    "student_email": email,
                    "student_name": student_ident.name,
                })
            
            else:  # response == 'no'
                # Decline: Remove email from preference_emails and check if requester is now fully declined
                preference_emails = team_request.preference_emails or []
                if email in preference_emails:
                    preference_emails.remove(email)
                    team_request.preference_emails = preference_emails
                    team_request.save()
                
                # Check if requester is now fully declined (no one left in preference list)
                all_responded, acceptance_status = _check_all_teammates_responded(team_request)
                
                if all_responded and len(acceptance_status['accepted']) == 0:
                    # Nobody accepted (everyone either declined or moved on)
                    # Convert to individual team preference
                    team_request.team_preference = "individual"
                    team_request.team_count = 1
                    team_request.preference_emails = []
                    team_request.save()
                    
                    send_everyone_declined(
                        requester_email=team_request.student.email,
                        requester_name=requester_ident.name if requester_ident else "Teammate",
                        course=team_request.course,
                    )
                
                messages.info(request, "You declined the team invitation. Please select your own team preference.")
                # Redirect to team preference page
                _set_student_session(request, ident=student_ident, enrollment=student_enrollment)
                return redirect("team_preference")
    
    except Exception as e:
        messages.error(request, f"Error processing team acceptance: {str(e)}")
        return redirect("home")


def register(request):
    """
    Register page:
    - No editable inputs from student.
    - Show email/name/phone/team_id/course as read-only.
    - Course derived from TeamID via regex helper.
    - If multiple enrollments exist, show table to pick and continue.
    """

    # -------------------------
    # GET
    # -------------------------
    if request.method == "GET":
        email = (request.GET.get("email") or "").strip().lower()
        mode = (request.GET.get("mode") or "").strip()

        if not email:
            messages.error(request, "Please enter your email on the home page first.")
            return redirect("home")

        existing_identity = _get_identity(email)
        if not existing_identity:
            messages.error(request, "Access Denied. Your email is not registered in our internship program. Please contact the administrator.")
            return redirect("home")

        # Case B: selection mode (multi-course / multi-team) for an existing identity
        if mode == "select":
            enrollments = (
                Student.objects
                .select_related("team", "identity")
                .filter(identity=existing_identity, team__isnull=False)
                .exclude(status="LegacyArchived")
                .order_by("course", "team__team_id")
            )
            if enrollments.count() > 1:
                return render(request, "Submissions/register.html", {
                    "prefill_email": email,
                    "prefill_name": existing_identity.name,
                    "prefill_phone": existing_identity.phone or "",
                    "team_exists": True,
                    "multi_enrollments": True,
                    "choices": _build_enrollment_choices(enrollments),
                })

        # Single-team confirm/register screen
        # For existing students, check if they have a Student enrollment with a team.
        # Exclude LegacyArchived enrollments so a student with one active + one legacy
        # course is treated as single-enrollment (matches home()/create_password() guards).
        enrollments = Student.objects.filter(identity=existing_identity).exclude(status="LegacyArchived")
        
        if enrollments.count() == 0:
            # Student identity exists but no active (non-legacy) enrollments
            return render(request, "Submissions/register.html", {
                "prefill_email": email,
                "prefill_name": existing_identity.name,
                "prefill_phone": existing_identity.phone or "",
                "team_exists": False,
                "error_message": "No course enrollment found. Please contact the internship team.",
            })
        
        if enrollments.count() == 1:
            enrollment = enrollments.first()
            _set_student_session(request, ident=existing_identity, enrollment=enrollment)
            return redirect(_post_login_redirect(enrollment, request))
        
        # Multiple enrollments - show selection
        return render(request, "Submissions/register.html", {
            "prefill_email": email,
            "prefill_name": existing_identity.name,
            "prefill_phone": existing_identity.phone or "",
            "team_exists": True,
            "multi_enrollments": True,
            "choices": _build_enrollment_choices(enrollments),
        })

    # -------------------------
    # POST
    # -------------------------
    email = (request.POST.get("email") or "").strip().lower()
    if not email:
        messages.error(request, "Please enter your email on the home page.")
        return redirect("home")

    # Disallow new user registration POST
    mode = (request.POST.get("mode") or "").strip()
    if mode == "new_user":
        messages.error(request, "Registration is disabled. Please contact support.")
        return redirect("home")

    # Case B POST: user clicked Continue on one of the enrollments
    enrollment_id = (request.POST.get("enrollment_id") or "").strip()
    if enrollment_id:
        enrollment = _get_selected_enrollment(email, enrollment_id)
        if not enrollment:
            messages.error(request, "Invalid selection. Please try again.")
            return redirect(f"/register/?email={email}&mode=select")

        ident = enrollment.identity
        _set_student_session(request, ident=ident, enrollment=enrollment)
        return redirect(_post_login_redirect(enrollment, request))

    # Existing student with single enrollment
    ident = _get_identity(email)
    if not ident:
        messages.error(request, "Your email is not onboarded yet. Please contact the internship team.")
        return redirect(f"/register/?email={email}")

    enrollments = Student.objects.filter(identity=ident).exclude(status="LegacyArchived")
    
    if enrollments.count() == 0:
        messages.error(request, "No course enrollment found. Please contact the internship team.")
        return redirect(f"/register/?email={email}")

    if enrollments.count() == 1:
        enrollment = enrollments.first()
        _set_student_session(request, ident=ident, enrollment=enrollment)
        messages.success(request, f"Welcome, {ident.name}!")
        return redirect(_post_login_redirect(enrollment, request))

    # Multiple enrollments - shouldn't reach here (would have shown selection on GET)
    messages.error(request, "Please select an enrollment from the form.")
    return redirect(f"/register/?email={email}&mode=select")


def create_password(request):
    """
    Create password page for students who don't have a portal password yet.
    User enters: Password + Confirm Password
    Password is hashed with Argon2 and stored in StudentIdentity.portal_password
    """
    temp_email = request.session.get("temp_email")
    if not temp_email:
        messages.error(request, "Please enter your email on the home page first.")
        return redirect("home")

    ident = _get_identity(temp_email)
    if not ident:
        messages.error(request, "Email not found. Please try again.")
        return redirect("home")

    # Block legacy students from creating a password
    enrollments = Student.objects.filter(identity=ident)
    if enrollments.exists() and enrollments.exclude(status="LegacyArchived").count() == 0:
        request.session.pop("temp_email", None)
        messages.error(request, "Your account is not active on this portal. Please contact the internship team.")
        return redirect("home")

    if ident.portal_password:
        # Password already exists - shouldn't be here
        messages.warning(request, "You already have a password. Please verify it.")
        return redirect("verify_password")

    if request.method == "GET":
        return render(request, "Submissions/create_password.html", {
            "email": temp_email,
        })

    # POST: Create password
    password = request.POST.get("password", "").strip()
    confirm_password = request.POST.get("confirm_password", "").strip()

    if not password:
        messages.error(request, "Please enter a password.")
        return redirect("create_password")

    if password != confirm_password:
        messages.error(request, "Passwords do not match. Please try again.")
        return redirect("create_password")

    if len(password) < 6:
        messages.error(request, "Password must be at least 6 characters long.")
        return redirect("create_password")

    # Hash password with Django (uses Argon2 by default)
    try:
        hashed_password = make_password(password)

        # Save to StudentIdentity
        ident.portal_password = hashed_password
        ident.save(update_fields=["portal_password"])

        request.session.pop("temp_email", None)

        # Log the student in directly — don't send them back to home
        enrollments = _get_enrollments_with_team(ident)
        count = enrollments.count()

        if count == 1:
            enrollment = enrollments.first()
            _set_student_session(request, ident=ident, enrollment=enrollment)
            messages.success(request, "Password created! Welcome to the portal.")
            return redirect(_post_login_redirect(enrollment, request))

        if count > 1:
            messages.success(request, "Password created! Please select your enrollment to continue.")
            return redirect(f"/register/?email={ident.email}&mode=select")

        # No team yet — go through register to pick team preference
        messages.success(request, "Password created! Please complete your registration.")
        return redirect(f"/register/?email={ident.email}")

    except Exception as e:
        messages.error(request, f"Error creating password: {str(e)}")
        return redirect("create_password")


def verify_password(request):
    """
    Verify portal password for students who already have one.
    User enters their password, which is verified against stored Argon2 hash.
    """
    temp_email = request.session.get("temp_email")
    if not temp_email:
        messages.error(request, "Please enter your email on the home page first.")
        return redirect("home")

    ident = _get_identity(temp_email)
    if not ident:
        messages.error(request, "Email not found. Please try again.")
        return redirect("home")

    if not ident.portal_password:
        # No password set - redirect to create password
        return redirect("create_password")

    if request.method == "GET":
        return render(request, "Submissions/verify_password.html", {
            "email": temp_email,
        })

    # POST: Verify password
    password = request.POST.get("password", "").strip()

    if not password:
        messages.error(request, "Please enter your password.")
        return redirect("verify_password")

    # Verify password: Try Django's check first, then fallback to manual Argon2
    password_valid = False
    
    if check_password(password, ident.portal_password):
        # Django's hasher worked (new format)
        password_valid = True
    else:
        # Fallback: Try manual Argon2 verification for old passwords
        try:
            from argon2 import PasswordHasher
            from argon2.exceptions import VerifyMismatchError, InvalidHash
            
            ph = PasswordHasher()
            try:
                ph.verify(ident.portal_password, password)
                password_valid = True
                # Rehash with Django's format for future logins
                ident.portal_password = make_password(password)
                ident.save(update_fields=["portal_password"])
                logger.info(f"Migrated password hash for {ident.email} to Django format")
            except (VerifyMismatchError, InvalidHash):
                password_valid = False
        except Exception as e:
            logger.warning(f"Argon2 fallback verification failed: {e}")
            password_valid = False
    
    if not password_valid:
        # Fallback: check AdminTempAccess (admin logging in as this student)
        temp_record = (
            AdminTempAccess.objects
            .filter(student_email=temp_email, is_used=False)
            .order_by('-created_at')
            .first()
        )
        if (temp_record and not temp_record.is_expired()
                and check_password(password, temp_record.temp_password_hash)):
            # Atomic update prevents race-condition double-use
            AdminTempAccess.objects.filter(pk=temp_record.pk).update(
                is_used=True,
                used_at=timezone.now(),
            )
            password_valid = True
        else:
            messages.error(request, "Incorrect password. Please try again.")
            return redirect("verify_password")

    # Password correct - continue with registration flow
    enrollments = _get_enrollments_with_team(ident)
    count = enrollments.count()

    request.session.pop("temp_email", None)

    if count == 0:
        return redirect(f"/register/?email={temp_email}")

    if count == 1:
        enrollment = enrollments.first()
        _set_student_session(request, ident=ident, enrollment=enrollment)
        return redirect(_post_login_redirect(enrollment, request))

    return redirect(f"/register/?email={temp_email}&mode=select")


@student_required
def register_for_course(request):
    """
    Register for another course page.
    Allows already-logged-in students to register for additional courses.
    
    GET: Show list of available courses (ones they're not already enrolled in)
    POST: Create new Student enrollment and redirect to team_preference
    """
    user_email = request.session.get("user_email")
    user_name = request.session.get("student_name")
    
    if not user_email or not user_name:
        messages.error(request, "Session invalid. Please log in again.")
        return redirect("home")
    
    # Get student identity
    ident = _get_identity(user_email)
    if not ident:
        messages.error(request, "Student identity not found.")
        return redirect("home")
    
    # Get all courses
    all_courses = Course.objects.all().order_by("code")
    
    # Get courses student is already enrolled in
    enrolled_courses = (
        Student.objects
        .filter(identity=ident)
        .values_list("course", flat=True)
        .distinct()
    )
    
    # Filter available courses (exclude ones they're already in)
    available_courses = all_courses.exclude(code__in=enrolled_courses)
    
    if request.method == "GET":
        return render(request, "Submissions/register_for_course.html", {
            "available_courses": available_courses,
            "enrolled_courses": enrolled_courses,
        })
    
    # POST: Create new enrollment for selected course
    course_code = (request.POST.get("course") or "").strip()
    
    if not course_code:
        messages.error(request, "Please select a course.")
        return render(request, "Submissions/register_for_course.html", {
            "available_courses": available_courses,
            "enrolled_courses": enrolled_courses,
        })
    
    # Verify course exists and is available
    try:
        course = Course.objects.get(code=course_code)
    except Course.DoesNotExist:
        messages.error(request, "Invalid course selection.")
        return render(request, "Submissions/register_for_course.html", {
            "available_courses": available_courses,
            "enrolled_courses": enrolled_courses,
        })
    
    # Check if already enrolled (double-check safety)
    if Student.objects.filter(identity=ident, course=course_code).exists():
        messages.warning(request, f"You are already enrolled in {course_code}.")
        return redirect("home")
    
    try:
        with transaction.atomic():
            # Create new enrollment
            enrollment = Student.objects.create(
                identity=ident,
                course=course_code,
                status="Registered",
            )
            
            # Update session with new course
            _set_student_session(request, ident=ident, enrollment=enrollment)
            
            messages.success(request, f"Successfully registered for {course_code}!")
            return redirect("team_preference")
    
    except Exception as e:
        messages.error(request, f"Error registering for course: {str(e)}")
        return render(request, "Submissions/register_for_course.html", {
            "available_courses": available_courses,
            "enrolled_courses": enrolled_courses,
        })


@student_required
def team_preference(request):
    """
    Team preference page for new students.
    Options:
    1. Individual - single person team
    2. I have a team - provide teammate emails (max 3 for 4-person team)
    3. Assign a team - system assigns automatically
    
    Creates Team record with team_id=NULL initially.
    Management command will assign team_id later.
    """
    user_email = request.session.get("user_email")
    course = request.session.get("course")
    
    if not user_email or not course:
        messages.error(request, "Session invalid. Please log in again.")
        return redirect("home")

    # 1. Check if student already has a team linked in their enrollment
    ident = _get_identity(user_email)
    enrollment = Student.objects.filter(identity=ident, course=course).first()
    
    if enrollment and enrollment.team:
        if not enrollment.team.team_id:
            return redirect("team_waiting")
        return redirect("pick_projects")

    # 2. Check if student already has a team preference (legacy check for creator)
    existing_team = Team.objects.filter(student__email=user_email, course=course).first()
    if existing_team:
        if not existing_team.team_id:
            return redirect("team_waiting")
        return redirect("pick_projects")

    if request.method == "GET":
        # DISABLED FOR NOW: Check for pending invitations from i_have_team preferences - NOT IN FUNCTION
        # ident = _get_identity(user_email)
        # pending_invitations = Team.objects.filter(
        #     team_preference="i_have_team",
        #     team_id__isnull=True,
        #     course=course,
        #     preference_emails__contains=user_email,
        # ).exclude(student__email=user_email)
        # 
        # if pending_invitations.exists():
        #     # User has pending invitations, show acceptance page instead
        #     invitation = pending_invitations.first()
        #     requester_ident = StudentIdentity.objects.filter(email=invitation.student.email).first()
        #     
        #     return render(request, "Submissions/team_acceptance.html", {
        #         "email": user_email,
        #         "name": ident.name if ident else "Student",
        #         "requester_email": invitation.student.email,
        #         "requester_name": requester_ident.name if requester_ident else "teammate",
        #         "course": course,
        #         "team_size": invitation.team_count,
        #         "team_id": invitation.id,
        #     })
        
        # No pending invitations, show normal preference page
        return render(request, "Submissions/team_preference.html")

    # POST: Handle team preference submission
    team_preference = (request.POST.get("team_preference") or "").strip()
    
    # DISABLED FOR NOW: i_have_team option - NOT IN FUNCTION
    if not team_preference or team_preference not in ["individual", "assign_team"]:
        messages.error(request, "Invalid team preference selected.")
        return render(request, "Submissions/team_preference.html")

    # Get student identity
    ident = _get_identity(user_email)
    if not ident:
        messages.error(request, "Student identity not found.")
        return redirect("home")

    try:
        with transaction.atomic():
            if team_preference == "individual":
                # Option 1: Individual
                team = Team.objects.create(
                    student=ident,
                    team_preference="individual",
                    team_count=1,
                    course=course,
                    preference_emails=[],
                    student_submitted=True,
                )
                # Send acknowledgment email
                send_team_preference_acknowledgment(user_email, team_preference, course)
                messages.success(request, "Team preference submitted! You will be assigned a team ID shortly.")

            # DISABLED FOR NOW: Option 2 - I have a team - NOT IN FUNCTION
            # elif team_preference == "i_have_team":
            #     # Option 2: I have a team
            #     preference_emails_json = request.POST.get("preference_emails")
            #     if not preference_emails_json:
            #         messages.error(request, "Please provide teammate emails.")
            #         return render(request, "Submissions/team_preference.html")
            #
            #     try:
            #         preference_emails = json.loads(preference_emails_json)
            #     except json.JSONDecodeError:
            #         messages.error(request, "Invalid email data.")
            #         return render(request, "Submissions/team_preference.html")
            #
            #     if not isinstance(preference_emails, list) or len(preference_emails) == 0:
            #         messages.error(request, "Please provide at least one teammate email.")
            #         return render(request, "Submissions/team_preference.html")
            #
            #     if len(preference_emails) > 3:
            #         messages.error(request, "Maximum 3 teammates allowed (4 people total per team).")
            #         return render(request, "Submissions/team_preference.html")
            #
            #     # Validate emails are not duplicates and don't include student email
            #     if user_email in preference_emails:
            #         messages.error(request, "You cannot add your own email as a teammate.")
            #         return render(request, "Submissions/team_preference.html")
            #
            #     if len(preference_emails) != len(set(preference_emails)):
            #         messages.error(request, "Duplicate emails not allowed.")
            #         return render(request, "Submissions/team_preference.html")
            #
            #     team_count = len(preference_emails) + 1  # +1 for self
            #
            #     team = Team.objects.create(
            #         student=ident,
            #         team_preference="i_have_team",
            #         preference_emails=preference_emails,
            #         team_count=team_count,
            #         course=course,
            #     )
            #     # Send acknowledgment email with teammate info
            #     send_team_preference_acknowledgment(user_email, team_preference, course, preference_emails)
            #     messages.success(request, f"Team preference submitted! We will process your team once all {team_count} members are registered.")

            elif team_preference == "assign_team":
                # Option 3: Assign a team
                team = Team.objects.create(
                    student=ident,
                    team_preference="assign_team",
                    team_count=4,  # Standard 4-person teams
                    course=course,
                    preference_emails=[],
                    student_submitted=True,
                )
                # Send acknowledgment email
                send_team_preference_acknowledgment(user_email, team_preference, course)
                messages.success(request, "Team preference submitted! You will be assigned to a team automatically.")

        return redirect("pick_projects")

    except Exception as e:
        messages.error(request, f"Error submitting team preference: {str(e)}")
        return render(request, "Submissions/team_preference.html")


@student_required
def team_waiting(request):
    """
    Team waiting page: shown when team preference is submitted but the team
    assignment hasn't been fully propagated to the student's enrollment row yet.

    The gate for pick_projects is enrollment.team (the FK on submissions_student),
    NOT team_pref.team_id. A team_id on the Team record means admin has run team
    formation, but until the student row is updated the assignment isn't final and
    students must not pick projects (wrong-team clash risk).
    """
    user_email = request.session.get("user_email")
    course = request.session.get("course")
    enrollment_id = request.session.get("enrollment_id")

    ident = _get_identity(user_email)
    if not ident:
        messages.error(request, "Student not found. Please log in again.")
        return redirect("home")

    # Re-fetch enrollment with team FK to get current DB state
    enrollment = (
        Student.objects
        .select_related("team")
        .filter(id=enrollment_id, identity=ident)
        .first()
    )

    # Enrollment.team is the authoritative signal — only proceed when it's set
    if enrollment and enrollment.team and enrollment.team.team_id:
        messages.success(request, f"Your team has been assigned: {enrollment.team.team_id}")
        request.session["team_id"] = enrollment.team.team_id
        return redirect("pick_projects")

    team_pref = Team.objects.filter(student=ident, course=course).first()

    if not team_pref:
        messages.warning(request, "No team preference found. Please set your team preference.")
        return redirect("team_preference")

    # Differentiate what stage the pending is at for the student-facing message
    assignment_pending = bool(team_pref.team_id)  # team_id set on pref but not yet on enrollment

    return render(request, "Submissions/team_waiting.html", {
        "student_name": ident.name,
        "team_preference": team_pref.get_team_preference_display(),
        "assignment_pending": assignment_pending,
    })


def forgot_password(request):
    """
    Forgot password page: User enters email to request OTP.
    """
    if request.method == "GET":
        return render(request, "Submissions/forgot_password.html")

    email = (request.POST.get("email") or "").strip().lower()
    if not email:
        messages.error(request, "Please enter your email.")
        return redirect("forgot_password")

    ident = _get_identity(email)
    if not ident:
        messages.error(request, "Email not found in our system.")
        return redirect("forgot_password")

    # Generate and send OTP
    from .services.email_service import create_and_send_otp
    success, error_msg = create_and_send_otp(email)

    if success:
        request.session["temp_email"] = email
        messages.success(request, "OTP sent to your email. Please check your inbox.")
        return redirect("verify_otp_page")
    else:
        messages.error(request, f"Error sending OTP: {error_msg}")
        return redirect("forgot_password")


def verify_otp_page(request):
    """
    Verify OTP page: User enters OTP received via email.
    """
    temp_email = request.session.get("temp_email")
    if not temp_email:
        messages.error(request, "Please start the forgot password process first.")
        return redirect("forgot_password")

    if request.method == "GET":
        return render(request, "Submissions/verify_otp.html", {
            "email": temp_email,
        })

    otp_code = (request.POST.get("otp_code") or "").strip()
    if not otp_code:
        messages.error(request, "Please enter the OTP.")
        return redirect("verify_otp_page")

    # Verify OTP
    from .services.email_service import verify_otp
    valid, error_msg = verify_otp(temp_email, otp_code)

    if valid:
        messages.success(request, "OTP verified successfully!")
        return redirect("reset_password")
    else:
        messages.error(request, error_msg or "Invalid OTP.")
        return redirect("verify_otp_page")


def reset_password(request):
    """
    Reset password page: User sets a new password after OTP verification.
    """
    temp_email = request.session.get("temp_email")
    if not temp_email:
        messages.error(request, "Please start the forgot password process first.")
        return redirect("forgot_password")

    ident = _get_identity(temp_email)
    if not ident:
        messages.error(request, "Email not found. Please try again.")
        return redirect("forgot_password")

    if request.method == "GET":
        return render(request, "Submissions/reset_password.html", {
            "email": temp_email,
        })

    # POST: Set new password
    password = request.POST.get("password", "").strip()
    confirm_password = request.POST.get("confirm_password", "").strip()

    if not password:
        messages.error(request, "Please enter a password.")
        return redirect("reset_password")

    if password != confirm_password:
        messages.error(request, "Passwords do not match. Please try again.")
        return redirect("reset_password")

    if len(password) < 6:
        messages.error(request, "Password must be at least 6 characters long.")
        return redirect("reset_password")

    try:
        hashed_password = make_password(password)
        
        # Update StudentIdentity password
        ident.portal_password = hashed_password
        ident.save(update_fields=["portal_password"])
        
        messages.success(request, "Password reset successfully! Please log in with your new password.")
        request.session.pop("temp_email", None)
        return redirect("home")
    
    except Exception as e:
        messages.error(request, f"Error resetting password: {str(e)}")
        return redirect("reset_password")


def resend_otp(request):
    """
    Resend OTP to user with rate limiting and attempt tracking.
    Max 3 resend attempts allowed with 2-minute wait between resends.
    """
    temp_email = request.session.get("temp_email")
    if not temp_email:
        messages.error(request, "Please start the forgot password process first.")
        return redirect("forgot_password")

    from .services.email_service import resend_otp as resend_otp_service
    success, error_msg, resends_remaining = resend_otp_service(temp_email)

    if success:
        messages.success(request, f"OTP resent to your email. {resends_remaining} attempts remaining.")
        return redirect("verify_otp_page")
    else:
        messages.error(request, error_msg or "Error resending OTP.")
        return redirect("verify_otp_page")


@student_required
def submissions(request):
    # Must have a session enrollment; otherwise go home
    enrollment_id = request.session.get("enrollment_id")
    if not enrollment_id:
        messages.error(request, "Please enter email id and proceed")
        return redirect("home")

    enrollment = (
        Student.objects
        .select_related("identity", "team")
        .filter(id=enrollment_id)
        .first()
    )
    if not enrollment or not enrollment.team:
        messages.error(request, "Please enter email id and proceed.")
        return redirect("home")

    team = enrollment.team
    course = enrollment.course

    # For two-phase AIE students show ALL team projects regardless of course;
    # for everyone else filter to their own course as normal.
    aie_leg = _aie_two_phase_leg(enrollment, team)
    project_course_filter = Q(project__course=course) if aie_leg is None else Q(team=team)

    # Projects dropdown = only ASSIGNED (pending)
    projects = (
        TeamProject.objects
        .filter(project_course_filter, team=team, status="assigned")
        .select_related("project")
        .order_by("project__project_id")
    )

    # Submitted projects list = submitted/evaluated
    submitted_qs = (
        TeamProject.objects
        .filter(project_course_filter, team=team, status__in=["submitted", "evaluated"])
        .select_related("project")
        .order_by("-updated_at")
    )

    submitted_list = [
        {
            "project_id": tp.project.project_id,
            "project_name": tp.project.project_name,
            "status": tp.status,
            "updated_at": tp.updated_at,
        }
        for tp in submitted_qs
    ]

    if request.method == "POST":
        project_id = (request.POST.get("project_id") or "").strip()
        track = (request.POST.get("track") or "").strip() or None
        files = request.FILES.getlist("submission_files")

        if not project_id:
            messages.error(request, "Please select a project.")
            return redirect("submissions")

        if not files:
            messages.error(request, "Please upload at least one file.")
            return redirect("submissions")

        tp = (
            TeamProject.objects
            .select_related("project")
            .filter(team=team, project__project_id=project_id)
            .first()
        )
        if not tp:
            messages.error(request, "This project is not assigned to your team.")
            return redirect("submissions")

        if tp.status in ["submitted", "evaluated"]:
            messages.warning(request, f"You already submitted for {project_id}.")
            return redirect("submissions")

        submission_schema = tp.project.submission_schema_json or {}
        if isinstance(submission_schema, str):
            try:
                submission_schema = json.loads(submission_schema)
            except json.JSONDecodeError:
                submission_schema = {}

        if track and "tracks" in submission_schema and track in submission_schema["tracks"]:
            track_zip_schema = submission_schema["tracks"][track].get("zip_schema", {})
            track_schema = {
                "required": submission_schema.get("required", True),
                "submission_type": submission_schema.get("submission_type", "zip"),
                "allowed_extensions": submission_schema.get("allowed_extensions", [".zip"]),
                "zip_schema": track_zip_schema,
            }
            validator = SubmissionValidator(track_schema)
        else:
            validator = SubmissionValidator(submission_schema)

        # Validate all files and collect errors
        validation_errors = []
        for f in files:
            result = validator.validate(f)
            if not result.is_valid:
                validation_errors.append((f.name, result))

        if validation_errors:
            # Format the first validation error for display
            filename, result = validation_errors[0]
            error_message = format_validation_error(result, submission_schema, filename)
            
            messages.error(request, error_message)
            return redirect("submissions")

        try:
            evaluation_result = _evaluate_submission_onspot_with_files(
                project_id=project_id,
                team=team,
                course=enrollment.course,
                files=files,
                track=track
            )

            if not evaluation_result["success"]:
                messages.error(request, "We're processing your submission. Please try again in a moment.")
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Evaluation failed for {project_id}: {evaluation_result.get('error')}")
                return redirect("submissions")

            with transaction.atomic():
                sub = Submission.objects.create(
                    team_project=tp,
                    student=enrollment,
                    evaluated=False,
                )

                submission_files = []
                for f in files:
                    import time
                    import logging
                    logger = logging.getLogger(__name__)
                    
                    upload_success = False
                    last_error = None
                    
                    # Try uploading with 1 retry (2 attempts total)
                    for attempt in range(2):
                        try:
                            sf = SubmissionFile.objects.create(
                                submission=sub,
                                file=f,
                                original_name=f.name,
                            )
                            submission_files.append(sf)
                            upload_success = True
                            break  # Success, move to next file
                            
                        except Exception as s3_error:
                            last_error = s3_error
                            if attempt == 0:  # First attempt failed
                                logger.warning(f"S3 upload attempt 1 failed for {f.name}: {str(s3_error)}. Retrying in 2 seconds...")
                                time.sleep(2)  # Wait 2 seconds before retry
                                continue
                            else:  # Second attempt also failed
                                logger.error(f"S3 upload failed after retry for {f.name}: {str(s3_error)}")
                                raise  # Re-raise to trigger rollback
                    
                    if not upload_success:
                        raise Exception(f"Failed to upload {f.name}: {str(last_error)}")

                tp.status = "submitted"
                tp.save(update_fields=["status", "updated_at"])

                evaluation_text = evaluation_result["evaluation"]
                grade = evaluation_result["grade"]

                eval_file_path = _save_evaluation_file(team.team_id, project_id, evaluation_text)

                visibility_after = timezone.now() + timedelta(days=2)

                # CDS submissions auto-approve (no trainer review needed).
                # Also auto-approve CDS projects submitted by AIE two-phase students.
                submitted_project_course = tp.project.course
                eval_defaults = {
                    "course": enrollment.course,
                    "evaluation_report": eval_file_path,
                    "evaluation_grade": grade,
                    "visibility_after": visibility_after,
                }

                if submitted_project_course == "CDS":
                    eval_defaults["reviewed"] = True
                    eval_defaults["reviewed_at"] = timezone.now()

                Evaluation.objects.update_or_create(
                    team=team,
                    project=tp.project,
                    defaults=eval_defaults
                )

                # Send email to team members
                email_success, email_error = send_submission_received_email(team, tp.project, enrollment.course)
                
                if not email_success:

                    messages.warning(request, f"Submission accepted for {project_id}. However, email notification could not be sent. Your submission is safely stored.")
                else:
                    messages.success(request, f"Submission accepted for {project_id}. Emails sent to all team members.")

        except IntegrityError:
            messages.warning(request, f"Duplicate submission is not allowed for {project_id}.")
            return redirect("submissions")
        except Exception as general_error:
            # Catch S3 and other errors
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Submission failed for {project_id}: {str(general_error)}")
            messages.error(request, f"Submission failed: File upload or processing error. Please try again later.")
            return redirect("submissions")

        return redirect("submissions")

    return render(
        request,
        "Submissions/submissions.html",
        {
            "student_name": enrollment.identity.name,
            "team_id": team.team_id,
            "course": enrollment.course,
            "projects": projects,
            "submitted_list": submitted_list,
        }
    )


@student_required
def pick_projects(request):
    user_email = request.session.get("user_email")
    course = request.session.get("course")
    team_id = request.session.get("team_id")

    if not user_email or not course or not team_id:
        messages.error(request, "Please enter your email and proceed.")
        return redirect("home")

    if CAPSTONE_REQUIRED.get(course, 0) == 0:
        messages.error(request, "Unknown course. Contact internship team")
        return redirect("home")

    # Refresh enrollment from DB — enrollment.team is the authoritative signal.
    # Session team_id is not trusted here; we re-derive from the FK to prevent
    # stale sessions from allowing picks before assignment is complete.
    enrollment_id = request.session.get("enrollment_id")
    _enrollment = (
        Student.objects.select_related("team", "identity")
        .filter(id=enrollment_id, identity__email=user_email)
        .first()
    )

    if not (_enrollment and _enrollment.team and _enrollment.team.team_id):
        messages.info(request, "Your team assignment is still being processed. Please check back shortly.")
        return redirect("team_waiting")

    if _enrollment.team.team_id != team_id:
        # Admin reassigned the team since last login — update session to DB truth
        request.session["team_id"] = _enrollment.team.team_id
        team_id = _enrollment.team.team_id

    team = _enrollment.team
    enrollment = _enrollment

    if not team:
        messages.error(request, "Please enter your email and proceed")
        return redirect("home")

    # ── Determine project pool and required counts ───────────────────────────
    # For new AIE students (no AIE projects yet) we run a two-phase pipeline:
    #   Phase "cds"  — pick from CDS pool, 4 capstones + 1 client
    #   Phase "aie"  — pick from AIE pool, 4 capstones + 1 client
    # Legacy AIE (already has AIE projects) and all other courses use the
    # normal single-phase flow.
    aie_leg = _aie_two_phase_leg(enrollment, team)
    if aie_leg == "cds":
        project_course = "CDS"
        required_capstones = CAPSTONE_REQUIRED["CDS"]  # 4
    elif aie_leg == "aie":
        project_course = "AIE"
        required_capstones = CAPSTONE_REQUIRED["AIE"]  # 4
    else:
        project_course = course
        required_capstones = CAPSTONE_REQUIRED.get(course, 0)

    members = (
        Student.objects
        .select_related("identity")
        .filter(team=team, course=course)
        .order_by("identity__email")
    )

    team_projects_qs = (
        TeamProject.objects
        .select_related("project")
        .filter(team=team, project__course=project_course)
    )

    capstone_selected = team_projects_qs.filter(project__project_type="Capstone")
    client_selected = team_projects_qs.filter(project__project_type="Client")

    capstone_selected_ids = set(capstone_selected.values_list("project__project_id", flat=True))
    client_selected_ids = set(client_selected.values_list("project__project_id", flat=True))

    capstone_eval_counts = capstone_selected.aggregate(
        total=Count("id"),
        submitted=Count("id", filter=Q(status__in=["submitted", "evaluated"])),
        evaluated=Count("id", filter=Q(status="evaluated")),
    )
    has_all_capstones = (capstone_eval_counts["total"] == required_capstones)
    all_capstones_submitted = (capstone_eval_counts["submitted"] == required_capstones)
    client_unlocked = has_all_capstones and all_capstones_submitted

    capstone_projects = (
        ProjectRegistry.objects
        .filter(course=project_course, project_type="Capstone")
        .order_by("project_name")
    )
    client_projects = (
        ProjectRegistry.objects
        .filter(course=project_course, project_type="Client")
        .order_by("project_name")
    )

    errors = []

    if request.method == "POST":
        action = request.POST.get("action")  # "save_capstones" or "save_client"

        if action == "save_capstones":
            chosen = request.POST.getlist("capstone_ids")

            if len(chosen) != required_capstones:
                errors.append(
                    f"You must select exactly {required_capstones} capstone project(s). You selected {len(chosen)}."
                )

            valid_ids = set(capstone_projects.values_list("project_id", flat=True))
            if any(pid not in valid_ids for pid in chosen):
                errors.append("One or more selected capstone projects are invalid.")

            if capstone_selected.count() >= required_capstones:
                errors.append("Capstone projects are already selected for this team. Changes are not allowed.")

            if not errors:
                with transaction.atomic():
                    picked_projects = ProjectRegistry.objects.filter(project_id__in=chosen)

                    for proj in picked_projects:
                        TeamProject.objects.get_or_create(
                            team=team,
                            project=proj,
                            defaults={
                                "status": "assigned",
                                "project_type": "Capstone",
                            },
                        )

                    new_status = "AIECapstoneAssigned" if aie_leg == "aie" else "CapStoneProjectsAssigned"
                    Student.objects.filter(team=team, course=course).update(status=new_status)

                # Send email to team members
                picked_projects_list = list(ProjectRegistry.objects.filter(project_id__in=chosen))
                email_success, email_error = send_project_picked_email(team, picked_projects_list, course)
                
                if not email_success:
                    messages.warning(request, f"Capstone projects assigned successfully, but email notification could not be sent: {email_error}")
                else:
                    messages.success(request, "Capstone projects assigned successfully! Emails sent to all team members.")
                
                return redirect("submissions")

        elif action == "save_client":
            if not client_unlocked:
                errors.append("Client project selection is locked until all capstone projects are evaluated.")

            chosen_client = request.POST.getlist("client_ids")

            if len(chosen_client) != 1:
                errors.append(f"You must select exactly 1 client project. You selected {len(chosen_client)}.")

            valid_client_ids = set(client_projects.values_list("project_id", flat=True))
            if any(pid not in valid_client_ids for pid in chosen_client):
                errors.append("Selected client project is invalid.")

            if client_selected.exists():
                errors.append("Client project is already selected for this team. Changes are not allowed.")

            if not errors:
                with transaction.atomic():
                    proj = ProjectRegistry.objects.get(project_id=chosen_client[0])
                    TeamProject.objects.get_or_create(
                        team=team,
                        project=proj,
                        defaults={
                            "status": "assigned",
                            "project_type": "Client",
                        },
                    )
                    new_status = "AIEClientAssigned" if aie_leg == "aie" else "ClientProjectAssigned"
                    Student.objects.filter(team=team, course=course).update(status=new_status)

                # Send email to team members
                email_success, email_error = send_project_picked_email(team, [proj], course)
                
                if not email_success:
                    messages.warning(request, f"Client project assigned successfully, but email notification could not be sent: {email_error}")
                else:
                    messages.success(request, "Client project assigned successfully! Emails sent to all team members.")
                
                return redirect("submissions")

        else:
            errors.append("Invalid action.")

    return render(request, "Submissions/pick_projects.html", {
        "team": team,
        "course": course,
        "members": members,

        "required_capstones": required_capstones,
        "capstone_projects": capstone_projects,
        "client_projects": client_projects,

        "capstone_selected_ids": capstone_selected_ids,
        "client_selected_ids": client_selected_ids,

        "capstone_total": capstone_eval_counts["total"],
        "capstone_evaluated": capstone_eval_counts["evaluated"],
        "client_unlocked": client_unlocked,
        "capstones_locked": capstone_eval_counts["total"] >= required_capstones,

        "errors": errors,
    })


@student_required
def evaluations(request):
    user_email = request.session.get("user_email")
    course = request.session.get("course")
    team_id = request.session.get("team_id")

    if not user_email or not course or not team_id:
        messages.error(request, "Please enter email id and proceed.")
        return redirect("home")

    team = Team.objects.filter(team_id=team_id).first()
    if not team:
        messages.error(request, "Please enter email id and proceed.")
        return redirect("home")

    enrollment = Student.objects.select_related("identity").filter(
        identity__email=user_email,
        course=course
    ).first()

    if not enrollment:
        messages.error(request, "Please enter email id and proceed.")
        return redirect("home")

    aie_leg = _aie_two_phase_leg(enrollment, team)

    # Two-phase AIE: show projects from both CDS and AIE pools.
    # Legacy AIE / all other courses: filter to the student's own course.
    if aie_leg is not None:
        team_projects = (
            TeamProject.objects
            .select_related("project", "team")
            .filter(team=team)
            .order_by("project__course", "project__project_type", "project__project_name")
        )
        evals = Evaluation.objects.filter(team=team)
        submissions = (
            Submission.objects
            .select_related("team_project", "student")
            .prefetch_related("files")
            .filter(team_project__team=team)
            .order_by("-created_at")
        )
    else:
        team_projects = (
            TeamProject.objects
            .select_related("project", "team")
            .filter(team=team, project__course=course)
            .order_by("project__project_type", "project__project_name")
        )
        evals = Evaluation.objects.filter(team=team, course=course)
        submissions = (
            Submission.objects
            .select_related("team_project", "student")
            .prefetch_related("files")
            .filter(team_project__team=team, team_project__project__course=course)
            .order_by("-created_at")
        )

    eval_map = {e.project_id: e for e in evals}

    latest_sub_by_tp = {}
    for s in submissions:
        if s.team_project_id and s.team_project_id not in latest_sub_by_tp:
            latest_sub_by_tp[s.team_project_id] = s

    rows = []
    now = timezone.now()

    for tp in team_projects:
        latest_sub = latest_sub_by_tp.get(tp.id)
        files = list(latest_sub.files.all()) if latest_sub else []

        evaluation = eval_map.get(tp.project_id)

        # Students only see evaluation content after the email has been sent.
        # Before that, everything looks like "Yet to be evaluated" — no hint
        # that review is done or pending visibility window.
        email_sent = evaluation.email_sent if evaluation else False
        evaluation_text = None

        if email_sent:
            raw = (evaluation.evaluation_report or "").strip()
            if raw:
                if "\n" in raw:
                    evaluation_text = raw
                else:
                    try:
                        from django.core.files.storage import default_storage
                        with default_storage.open(raw, 'rb') as f:
                            evaluation_text = f.read().decode('utf-8')
                    except Exception:
                        try:
                            from django.conf import settings as _settings
                            full_path = os.path.join(_settings.MEDIA_ROOT, raw)
                            with open(full_path, 'r', encoding='utf-8') as f:
                                evaluation_text = f.read()
                        except Exception:
                            evaluation_text = None

        rows.append({
            "tp": tp,
            "project": tp.project,
            "latest_submission": latest_sub,
            "files": files,
            "evaluation": evaluation,
            "email_sent": email_sent,
            "evaluation_text": evaluation_text,
        })

    # ── Status auto-transitions ──────────────────────────────────────────────
    if aie_leg == "cds":
        # CDS leg of two-phase AIE
        cds_capstones = team_projects.filter(project__course="CDS", project__project_type="Capstone")
        cds_required = CAPSTONE_REQUIRED["CDS"]
        cds_submitted = cds_capstones.filter(status__in=["submitted", "evaluated"]).count()
        cds_client = team_projects.filter(project__course="CDS", project__project_type="Client")
        cds_client_evaluated = cds_client.filter(status="evaluated").exists()

        if cds_capstones.count() == cds_required and cds_submitted == cds_required and not cds_client.exists():
            if enrollment.status != "ReadyForClientPick":
                enrollment.status = "ReadyForClientPick"
                enrollment.save(update_fields=["status"])

        if cds_client_evaluated:
            if enrollment.status not in ("CDSCycleComplete",) | AIE_PHASE2_STATUSES:
                enrollment.status = "CDSCycleComplete"
                enrollment.save(update_fields=["status"])

        required = cds_required
        capstone_eval_count = cds_capstones.filter(status="evaluated").count()

    elif aie_leg == "aie":
        # AIE leg of two-phase AIE
        aie_capstones = team_projects.filter(project__course="AIE", project__project_type="Capstone")
        aie_required = CAPSTONE_REQUIRED["AIE"]
        aie_submitted = aie_capstones.filter(status__in=["submitted", "evaluated"]).count()
        aie_client = team_projects.filter(project__course="AIE", project__project_type="Client")

        if aie_capstones.count() == aie_required and aie_submitted == aie_required and not aie_client.exists():
            if enrollment.status != "AIEReadyForClientPick":
                enrollment.status = "AIEReadyForClientPick"
                enrollment.save(update_fields=["status"])

        required = aie_required
        capstone_eval_count = aie_capstones.filter(status="evaluated").count()

    else:
        # Normal single-phase (CDS, CDE, CDA, or legacy AIE)
        required = CAPSTONE_REQUIRED.get(course, 0)
        capstones = team_projects.filter(project__project_type="Capstone")
        capstone_count = capstones.count()
        capstone_submitted_count = capstones.filter(status__in=["submitted", "evaluated"]).count()
        capstone_eval_count = capstones.filter(status="evaluated").count()
        has_client_selected = team_projects.filter(project__project_type="Client").exists()

        if required and capstone_count == required and capstone_submitted_count == required and not has_client_selected:
            if enrollment.status != "ReadyForClientPick":
                enrollment.status = "ReadyForClientPick"
                enrollment.save(update_fields=["status"])

    return render(request, "Submissions/evaluations.html", {
        "team": team,
        "course": course,
        "enrollment": enrollment,
        "rows": rows,
        "required_capstones": required,
        "capstone_eval_count": capstone_eval_count,
        "aie_leg": aie_leg,
    })


# ============================================================================
# HELPER FUNCTIONS FOR ON-THE-SPOT EVALUATION
# ============================================================================

def _extract_grade_from_evaluation(evaluation_text):
    if not evaluation_text:
        return None

    final_grade_match = re.search(
        r'FINAL\s+GRADE\s*\n\s*[-\s]*\n\s*([A-D])',
        evaluation_text,
        re.IGNORECASE | re.MULTILINE
    )
    if final_grade_match:
        return final_grade_match.group(1).strip()

    patterns = [
        r'(?:Grade|grade|GRADE)[:\s]+([A-Fa-f]\+?)',
        r'(?:Score|score|SCORE)[:\s]+(\d+(?:\.\d+)?(?:/\d+)?(?:%)?)',
        r'(?:Rating|rating|RATING)[:\s]+(\d+(?:\.\d+)?(?:/\d+)?)',
        r'(?:Mark|mark|MARK)[:\s]+(\d+(?:\.\d+)?(?:/\d+)?)',
        r'(?:Overall|overall|OVERALL)\s+(?:Grade|grade|GRADE)[:\s]+([A-D])',
    ]

    for pattern in patterns:
        match = re.search(pattern, evaluation_text)
        if match:
            return match.group(1).strip()

    return None


def _evaluate_submission_onspot_with_files(project_id: str, team: Team, course: str, files: list, track: str = None) -> dict:
    """
    On-the-spot evaluation using uploaded file objects (before saving to DB).
    This allows validation before database commit.
    """
    try:
        import tempfile
        import shutil

        project = ProjectRegistry.objects.get(project_id=project_id)

        if not files:
            return {"success": False, "evaluation": None, "grade": None, "error": "No files to evaluate"}

        first_file = files[0]

        temp_dir = tempfile.mkdtemp()
        temp_file_path = os.path.join(temp_dir, first_file.name)

        with open(temp_file_path, 'wb') as temp_file:
            for chunk in first_file.chunks():
                temp_file.write(chunk)

        try:
            result = assemble_and_evaluate(
                project_id=project_id,
                submission_file_path=temp_file_path,
                track=track
            )

            if result.get("status") == "success" and result.get("evaluation"):
                evaluation_text = result["evaluation"]["llm_evaluation"]
                grade = _extract_grade_from_evaluation(evaluation_text)
                return {"success": True, "evaluation": evaluation_text, "grade": grade, "error": None}

            return {"success": False, "evaluation": None, "grade": None, "error": result.get("error", "Evaluation failed")}

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    except Exception as e:
        return {"success": False, "evaluation": None, "grade": None, "error": f"Evaluation error: {str(e)}"}


def _evaluate_submission_onspot(project_id: str, submission_id: int, team: Team, course: str, files: list, track: str = None) -> dict:
    """
    On-the-spot evaluation: parse submission and get LLM evaluation immediately.
    """
    try:
        project = ProjectRegistry.objects.get(project_id=project_id)

        submission_schema = project.expected_submission or {}
        if isinstance(submission_schema, str):
            submission_schema = json.loads(submission_schema)

        if not files:
            return {"success": False, "evaluation": None, "grade": None, "error": "No files to evaluate"}

        first_submission_file = files[0]
        submission_file_path = os.path.join(
            os.getcwd(),
            first_submission_file.file.path if hasattr(first_submission_file.file, 'path') else str(first_submission_file.file)
        )

        result = assemble_and_evaluate(
            project_id=project_id,
            submission_file_path=submission_file_path,
            track=track
        )

        if result.get("status") == "success" and result.get("evaluation"):
            evaluation_text = result["evaluation"]["llm_evaluation"]
            grade = _extract_grade_from_evaluation(evaluation_text)
            return {"success": True, "evaluation": evaluation_text, "grade": grade, "error": None}

        return {"success": False, "evaluation": None, "grade": None, "error": result.get("error", "Evaluation failed")}

    except Exception as e:
        return {"success": False, "evaluation": None, "error": f"Evaluation error: {str(e)}"}


def _save_evaluation_file(team_id: str, project_id: str, evaluation_text: str) -> str:
    """
    Save evaluation report to S3 (if enabled) or local disk.
    Returns RELATIVE PATH that works with both local storage and S3.
    
    Examples:
    - Local: Database stores "Evaluations/TEAM-XYZ/PRCP-001/20260127_120000_evaluation.txt"
             Django reads from /media/Evaluations/TEAM-XYZ/PRCP-001/...
    - S3:    Database stores "Evaluations/TEAM-XYZ/PRCP-001/20260127_120000_evaluation.txt"
             Django reads from https://bucket.s3.amazonaws.com/Evaluations/TEAM-XYZ/PRCP-001/...
    """
    try:
        from django.conf import settings
        from io import BytesIO
        
        timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        eval_filename = f"{timestamp}_evaluation.txt"
        
        # Relative path (works for both local and S3)
        relative_path = os.path.join("Evaluations", team_id, project_id, eval_filename)
        
        use_s3 = os.getenv('USE_S3', 'False').lower() == 'true'
        
        if use_s3:
            # Save to S3
            from django.core.files.storage import default_storage
            
            # Write to S3
            default_storage.save(relative_path, BytesIO(evaluation_text.encode('utf-8')))
            
            # Return relative path (Django storage will convert to S3 URL when needed)
            return relative_path
        else:
            # Save locally (backward compatible)
            eval_dir = os.path.join(settings.MEDIA_ROOT, "Evaluations", team_id, project_id)
            os.makedirs(eval_dir, exist_ok=True)
            
            eval_file_path = os.path.join(eval_dir, eval_filename)
            
            with open(eval_file_path, 'w', encoding='utf-8') as f:
                f.write(evaluation_text)
            
            # Return relative path instead of absolute path
            return relative_path

    except Exception as e:
        print(f"Warning: Could not save evaluation file: {str(e)}")
        return None

