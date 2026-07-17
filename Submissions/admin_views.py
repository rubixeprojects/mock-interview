from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Q, Count, Prefetch, F
from django.utils import timezone
from django.http import JsonResponse, HttpResponse
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from functools import wraps
from contextlib import contextmanager

@contextmanager
def suppress_es_signals():
    """No-op — Elasticsearch has been removed. Kept so call-sites don't need editing."""
    yield
from datetime import timedelta
from .models import *
from .services.email_service import process_team_formation_emails
from .services.cache_service import cached_query, CACHE_KEYS, invalidate_cache
from django.core.cache import cache
import logging
import csv
import io
from ControlCenter.models import AdminAccount
from ControlCenter.views import log_admin_action

logger = logging.getLogger(__name__)

ADMIN_SESSION_TIMEOUT = 1800  # 30 minutes

def admin_required(view_func):
    """Strict admin authentication decorator using ControlCenter"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        admin_email = request.session.get("admin_email")
        if not admin_email:
            return JsonResponse({"success": False, "error": "Not authenticated"}, status=401)
        
        admin = AdminAccount.objects.filter(email=admin_email, is_active=True).first()
        if not admin:
            request.session.flush()
            return JsonResponse({"success": False, "error": "Invalid admin account"}, status=403)
        
        request.admin_user = admin
        return view_func(request, *args, **kwargs)
    return wrapper

def admin_login(request):
    """
    Admin login
    - superadmin: username + password
    - admin: email + password
    """
    if request.session.get("admin_email"):
        admin = AdminAccount.objects.filter(
            email=request.session["admin_email"], 
            is_active=True
        ).first()
        if admin and admin.role != "student":
            return redirect("admin_dashboard")
    
    # Get pre-filled email (only for regular admins coming from home page)
    prefilled_email = request.session.pop("admin_email_prefill", "")
    
    if request.method == "GET":
        return render(request, "Submissions/admin/login.html", {
            "prefilled_email": prefilled_email
        })
    
    login_id = (request.POST.get("email") or "").strip().lower()  # Can be email or username
    password = request.POST.get("password") or ""
    
    if not login_id or not password:
        messages.error(request, "Please enter both credentials and password.")
        return render(request, "Submissions/admin/login.html", {
            "prefilled_email": login_id
        })
    
    # Find admin by email only (AdminAccount has no username field)
    admin = AdminAccount.objects.filter(
        email=login_id,
        is_active=True
    ).exclude(role="student").first()

    if not admin or not admin.check_password(password):
        messages.error(request, "Invalid credentials.")
        logger.warning(f"Failed admin login attempt: {login_id} from {request.META.get('REMOTE_ADDR')}")
        return render(request, "Submissions/admin/login.html", {
            "prefilled_email": login_id
        })

    # ✅ Set secure session
    request.session["admin_email"] = admin.email
    request.session["admin_name"] = admin.name
    request.session["admin_role"] = admin.role
    request.session["admin_last_activity"] = timezone.now().timestamp()
    request.session.set_expiry(0)

    logger.info(f"Admin login successful: {admin.email} ({admin.role}) from {request.META.get('REMOTE_ADDR')}")
    
    messages.success(request, f"Welcome back, {admin.name}!")
    return redirect("admin_dashboard")



def admin_logout(request):
    """Admin logout"""
    admin_email = request.session.get("admin_email", "unknown")
    request.session.flush()
    logger.info(f"Admin logout: {admin_email}")
    messages.success(request, "You have been logged out successfully.")
    return redirect("admin_login")


@cached_query(timeout=300, key_prefix=CACHE_KEYS['DASHBOARD'], key_suffix=':stats')
def get_dashboard_stats():
    """
    Get dashboard statistics with caching.
    Cached for 5 minutes and auto-invalidated on Submission/Evaluation changes.
    """
    return {
        "total_students": StudentIdentity.objects.count(),
        "total_teams": Team.objects.values('team_id').distinct().count(),
        "total_submissions": Submission.objects.count(),
        "pending_evaluations": Evaluation.objects.filter(
            email_sent=False,
            reviewed=True,
            visibility_after__lte=timezone.now()
        ).count(),
        "total_projects": ProjectRegistry.objects.count(),
    }


@admin_required
def admin_dashboard(request):
    """Dashboard with statistics"""
    
    stats = get_dashboard_stats()
    
    recent_submissions = (
        Submission.objects
        .select_related("team_project__team", "team_project__project", "student__identity")
        .order_by("-created_at")[:10]
    )
    
    # Get evaluations and map by (team_id, project_id) for status checking
    evaluations = Evaluation.objects.select_related("team", "project").all()
    eval_map = {}
    for ev in evaluations:
        key = (ev.team.team_id, ev.project.project_id)
        eval_map[key] = ev
    
    # Add evaluation status to each submission
    for sub in recent_submissions:
        eval_key = (sub.team_project.team.team_id, sub.team_project.project.project_id)
        sub.evaluation_email_sent = eval_map.get(eval_key) and eval_map[eval_key].email_sent
    
    course_stats = (
        Student.objects
        .values("course")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    
    context = {
        "stats": stats,
        "recent_submissions": recent_submissions,
        "course_stats": course_stats,
    }
    
    return render(request, "Submissions/admin/dashboard.html", context)


@admin_required
def admin_students(request):
    """Student management with Elasticsearch search"""
    
    search_query = request.GET.get("q", "").strip()
    course_filter = request.GET.get("course", "").strip()
    
    # SQL search across identity fields, team ID, and course
    students_qs = Student.objects.select_related("identity", "team").order_by("-id")

    if search_query:
        students_qs = students_qs.filter(
            Q(identity__email__icontains=search_query) |
            Q(identity__name__icontains=search_query) |
            Q(team__team_id__icontains=search_query) |
            Q(course__icontains=search_query)
        )

    if course_filter:
        students_qs = students_qs.filter(course=course_filter)

    matched_students = list(students_qs[:200])

    # Also pull in teammates of matched students so the full team is visible
    if search_query:
        team_ids = {st.team.team_id for st in matched_students if st.team and st.team.team_id}
        if team_ids:
            matched_ids = {st.id for st in matched_students}
            teammates = (
                Student.objects
                .filter(team__team_id__in=team_ids)
                .select_related("identity", "team")
                .exclude(id__in=matched_ids)
            )
            students = matched_students + list(teammates)
        else:
            students = matched_students
    else:
        students = matched_students
    
    courses = Student.objects.values_list("course", flat=True).distinct().order_by("course")
    
    context = {
        "students": students,
        "search_query": search_query,
        "course_filter": course_filter,
        "courses": courses,
        "can_edit": request.admin_user.role == "SuperAdmin",
    }
    
    return render(request, "Submissions/admin/students.html", context)


@admin_required
def admin_submissions(request):
    """
    Combined Submission & Evaluation management with Elasticsearch search
    Shows submissions with their evaluation status
    """
    
    search_query = request.GET.get("q", "").strip()
    course_filter = request.GET.get("course", "").strip()
    status_filter = request.GET.get("status", "").strip()
    
    # SQL search across team, project, and student fields
    submissions = (
        Submission.objects
        .select_related("team_project__team", "team_project__project", "student__identity")
        .prefetch_related("files")
        .order_by("-created_at")
    )

    if search_query:
        submissions = submissions.filter(
            Q(team_project__team__team_id__icontains=search_query) |
            Q(team_project__project__project_id__icontains=search_query) |
            Q(team_project__project__project_name__icontains=search_query) |
            Q(student__identity__email__icontains=search_query) |
            Q(student__identity__name__icontains=search_query)
        )

    if course_filter:
        submissions = submissions.filter(student__course=course_filter)

    if status_filter == "evaluated":
        submissions = submissions.filter(evaluated=True)
    elif status_filter == "pending":
        submissions = submissions.filter(evaluated=False)

    submissions = submissions[:200]
    
    # Get evaluations for enrichment
    evaluations = Evaluation.objects.select_related("team", "project").all()
    
    if status_filter == "evaluated":
        evaluations = evaluations.filter(email_sent=True)
    
    # Map evaluations by (team_id, project_id) tuple
    eval_map = {}
    for ev in evaluations:
        key = (ev.team.team_id, ev.project.project_id)
        eval_map[key] = ev
    
    # Enrich submissions with evaluation data  
    submission_data = []
    for sub in submissions:
        eval_key = (sub.team_project.team.team_id, sub.team_project.project.project_id)
        evaluation = eval_map.get(eval_key)
        
        submission_data.append({
            "submission": sub,
            "evaluation": evaluation,
            "files": sub.files.all(),
        })
    
    courses = Student.objects.values_list("course", flat=True).distinct().order_by("course")
    
    context = {
        "submission_data": submission_data,
        "search_query": search_query,
        "course_filter": course_filter,
        "status_filter": status_filter,
        "courses": courses,
    }
    
    return render(request, "Submissions/admin/submissions.html", context)


@admin_required
def admin_projects(request):
    """Project registry management"""
    
    search_query = request.GET.get("q", "").strip()
    course_filter = request.GET.get("course", "").strip()
    type_filter = request.GET.get("type", "").strip()
    
    projects = ProjectRegistry.objects.annotate(
        submission_count=Count("team_projects", filter=Q(team_projects__status="submitted")),
        assigned_count=Count("team_projects", filter=Q(team_projects__status="assigned"))
    )
    
    # Search across: project_id, project_name, course
    if search_query:
        projects = projects.filter(
            Q(project_id__icontains=search_query) |
            Q(project_name__icontains=search_query) |
            Q(course__icontains=search_query)
        )
    
    if course_filter:
        projects = projects.filter(course=course_filter)
    
    if type_filter:
        projects = projects.filter(project_type=type_filter)
    
    projects = projects.order_by("course", "project_type", "project_name")
    
    courses = ProjectRegistry.objects.values_list("course", flat=True).distinct().order_by("course")
    
    context = {
        "projects": projects,
        "search_query": search_query,
        "course_filter": course_filter,
        "type_filter": type_filter,
        "courses": courses,
    }
    
    return render(request, "Submissions/admin/projects.html", context)


@admin_required
def admin_change_team_id(request):
    """
    Page to search for students and reassign them to individual teams.
    Shows search interface with list of matching students.
    """
    search_query = request.GET.get("q", "").strip()
    students = []
    
    logger.info(f"[CHANGE_TEAM_ID] Admin {request.session.get('admin_email')} searching for: '{search_query}'")
    
    if search_query:
        students = (
            Student.objects
            .select_related("identity", "team")
            .filter(
                Q(identity__email__icontains=search_query) |
                Q(identity__name__icontains=search_query) |
                Q(team__team_id__icontains=search_query)
            )
            .order_by("-id")[:100]
        )
        logger.info(f"[CHANGE_TEAM_ID] SQL query returned {len(students)} students for '{search_query}'")
    else:
        # No search query - show recent students by default
        logger.info(f"[CHANGE_TEAM_ID] No search query, showing recent students by default")
        students = (
            Student.objects
            .select_related("identity", "team")
            .order_by("-id")[:50]
        )
        logger.info(f"[CHANGE_TEAM_ID] Loaded {len(students)} recent students as fallback")
    
    context = {
        "search_query": search_query,
        "students": students,
    }
    
    return render(request, "Submissions/admin/change_team_id.html", context)


@admin_required
def admin_reassign_student_to_individual(request):
    """
    POST endpoint to reassign a student to an individual team.
    
    Expects JSON: { "student_id": <id> }
    Returns JSON: { "success": bool, "message": str, "team_id": str }
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Method not allowed"}, status=405)
    
    try:
        import json
        data = json.loads(request.body)
        student_id = data.get("student_id")
        
        if not student_id:
            return JsonResponse({"success": False, "message": "Missing student_id"}, status=400)
        
        # Import service and call reassignment
        from .services.team_service import reassign_student_to_individual
        
        success, message, team_id = reassign_student_to_individual(student_id)
        
        if success:
            logger.info(f"Admin {request.session.get('admin_email')} reassigned student {student_id} to team {team_id}")
        else:
            logger.warning(f"Failed to reassign student {student_id}: {message}")
        
        return JsonResponse({
            "success": success,
            "message": message,
            "team_id": team_id
        })
    
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.error(f"Error in reassign endpoint: {str(e)}", exc_info=True)
        return JsonResponse({"success": False, "message": f"Server error: {str(e)}"}, status=500)


@admin_required
def admin_search_api(request):
    """Unified search API for sidebar quick search"""
    query = request.GET.get("q", "").strip()
    
    if not query or len(query) < 2:
        return JsonResponse({"results": []})
    
    results = []
    
    # Search students
    students = (
        Student.objects
        .select_related("identity", "team")
        .filter(
            Q(identity__email__icontains=query) |
            Q(identity__name__icontains=query) |
            Q(team__team_id__icontains=query)
        )[:5]
    )
    
    for s in students:
        results.append({
            "type": "student",
            "title": s.identity.name,
            "subtitle": f"{s.identity.email} | {s.course}",
            "url": f"/portal/students/?q={s.identity.email}",
        })
    
    # Search submissions
    submissions = (
        Submission.objects
        .select_related("team_project__team", "team_project__project")
        .filter(
            Q(team_project__team__team_id__icontains=query) |
            Q(team_project__project__project_name__icontains=query)
        )[:5]
    )
    
    for sub in submissions:
        results.append({
            "type": "submission",
            "title": f"{sub.team_project.project.project_name}",
            "subtitle": f"Team: {sub.team_project.team.team_id}",
            "url": f"/portal/submissions/?q={sub.team_project.team.team_id}",
        })
    
    return JsonResponse({"results": results})


@csrf_exempt
def whitelist_sync_api(request):
    """
    API endpoint to sync registration whitelist from AppScript (Google Sheets).
    
    Receives batch of records: { "records": [{"email": "user@example.com", "course": "CDS"}, ...] }
    Uses get_or_create() to handle duplicates (idempotent).
    
    Returns: { "success": true, "synced": N, "message": "..." }
    """
    import json
    
    if request.method != 'POST':
        return JsonResponse({"success": False, "error": "POST method required"}, status=405)
    
    try:
        data = json.loads(request.body)
        records = data.get("records", [])
        
        # Validate batch size
        if not records:
            return JsonResponse({"success": False, "error": "No records provided"}, status=400)
        
        if len(records) > 5000:
            return JsonResponse({
                "success": False, 
                "error": "Batch too large (max 5000 records)"
            }, status=400)
        
        synced_count = 0
        errors = []
        
        for i, record in enumerate(records):
            try:
                email = record.get("email", "").strip().lower()
                course = record.get("course", "").strip().upper()
                
                if not email or not course:
                    errors.append(f"Record {i+1}: Missing email or course")
                    continue
                
                # Validate email format
                if "@" not in email or "." not in email:
                    errors.append(f"Record {i+1}: Invalid email format '{email}'")
                    continue
                
                # Use get_or_create to handle duplicates (idempotent)
                obj, created = RegistrationWhitelist.objects.get_or_create(
                    email=email,
                    course=course,
                    defaults={"added_at": timezone.now()}
                )
                
                if created:
                    synced_count += 1
                    logger.info(f"Added to whitelist: {email} → {course}")
                # If not created, it already exists (duplicate), just skip silently
                
            except Exception as e:
                errors.append(f"Record {i+1}: {str(e)}")
                logger.error(f"Whitelist sync error: {str(e)}")
        
        response = {
            "success": True,
            "synced": synced_count,
            "total_records": len(records),
            "message": f"Synced {synced_count}/{len(records)} new records from whitelist"
        }
        
        if errors:
            response["warnings"] = errors
            logger.warning(f"Whitelist sync had {len(errors)} errors: {errors}")
        
        return JsonResponse(response)


    
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.error(f"Whitelist sync critical error: {str(e)}", exc_info=True)
        return JsonResponse({
            "success": False,
            "error": f"Server error: {str(e)}"
        }, status=500)


@csrf_exempt
def upload_students_csv_api(request):
    """
    API endpoint to upload and validate students from a CSV.
    Uses BULK queries for speed instead of per-row get_or_create.
    Supports dry_run via query param (e.g. ?dry_run=true).
    Returns JSON with added_students count, inconsistencies, and errors.
    """
    if request.method != 'POST':
        return JsonResponse({"success": False, "error": "POST method required"}, status=405)
    
    if 'file' not in request.FILES:
        return JsonResponse({"success": False, "error": "No file uploaded. Expected 'file' multipart field."}, status=400)
        
    csv_file = request.FILES['file']
    if not csv_file.name.endswith('.csv'):
        return JsonResponse({"success": False, "error": "File is not CSV type"}, status=400)
        
    is_dry_run = request.GET.get('dry_run', '').lower() == 'true' or request.POST.get('dry_run', '').lower() == 'true'
    
    allowed_inconsistencies = []
    post_data = request.POST.copy()
    raw_allowed = post_data.getlist("allow_inconsistencies")
    logger.info(f"CSV Upload Request: dry_run={is_dry_run}, raw_allowed={raw_allowed}")

    for x in raw_allowed:
        try: allowed_inconsistencies.append(int(x))
        except: pass
    
    logger.info(f"Parsed allowed_inconsistencies: {allowed_inconsistencies}")
    
    ingest_new = request.POST.get('ingest_new', 'true').lower() == 'true'
    logger.info(f"CSV Upload: ingest_new={ingest_new}, raw_ingest_new={request.POST.get('ingest_new', 'NOT_SENT')}")
    admin_email = request.session.get('admin_email', 'admin')
        
    try:
        # ── Step 1: Parse & clean CSV ──────────────────────────────────
        decoded_file = csv_file.read().decode('utf-8-sig').splitlines()
        reader = csv.DictReader(decoded_file)
        
        # Standardize headers: strip whitespace and uppercase
        if reader.fieldnames:
            reader.fieldnames = [str(f).strip().upper() if f else "" for f in reader.fieldnames]
        
        report = {
            "success": True,
            "dry_run": is_dry_run,
            "total_rows": 0,
            "valid_rows": 0,
            "null_entries_count": 0,
            "new_identities_count": 0,
            "new_enrollments_count": 0,
            "existing_enrollments_count": 0,
            "inconsistencies": [],
            "errors": [],
        }
        
        # Load course alias map (e.g. AEN → AIE) in one query
        alias_map = {
            ca.alias.upper(): ca.course.code.upper()
            for ca in CourseAlias.objects.all().select_related('course')
        }
        
        # Clean all rows upfront and separate valid from invalid
        valid_rows = []  # list of (row_num, name, email, course)
        
        for i, row in enumerate(reader):
            report["total_rows"] += 1
            # Strip every cell value
            cleaned = {k: (str(v).strip() if v is not None else "") for k, v in row.items()}
            
            # Skip entirely blank rows
            if not any(cleaned.values()):
                continue
            
            name = cleaned.get('NAME', '')
            email = (cleaned.get('EMAIL ID') or cleaned.get('EMAIL', '')).lower()
            course_raw = cleaned.get('COURSE', '').upper()
            
            if not email or not course_raw or not name:
                report["null_entries_count"] += 1
                # Tell the user exactly which fields are missing
                missing = []
                if not name:  missing.append("NAME")
                if not email: missing.append("EMAIL ID")
                if not course_raw: missing.append("COURSE")
                report["errors"].append({
                    "row": i + 1,
                    "reason": f"Missing field(s): {', '.join(missing)}",
                    "data": cleaned
                })
                continue
            
            # Resolve course alias to canonical code (e.g. AEN → AIE)
            course = alias_map.get(course_raw, course_raw)
            
            valid_rows.append((i + 1, name, email, course))
        
        report["valid_rows"] = len(valid_rows)
        
        if not valid_rows:
            report["message"] = "No valid rows found in CSV after cleaning."
            return JsonResponse(report)
        
        # ── Step 2: Disconnect cache signals to avoid Redis timeouts during bulk import ──
        from django.db.models.signals import post_save, post_delete
        from Submissions.services.cache_service import _cache_invalidation_handler

        models_to_disconnect = [StudentIdentity, Student]
        for Model in models_to_disconnect:
            post_save.disconnect(_cache_invalidation_handler, sender=Model)
            post_delete.disconnect(_cache_invalidation_handler, sender=Model)

        try:
            # ── Step 3: Bulk fetch existing data (3 queries total) ─────
            csv_emails = list({r[2] for r in valid_rows})  # unique emails
            
            # Query 1: All matching StudentIdentity records
            existing_identities = {
                si.email: si
                for si in StudentIdentity.objects.filter(email__in=csv_emails)
            }
            
            # Query 2: All matching Student enrollments
            existing_enrollments = set()
            enrollment_map = {}  # (email, course) -> Student obj
            for s in Student.objects.filter(identity__email__in=csv_emails).select_related('identity'):
                key = (s.identity.email, s.course)
                existing_enrollments.add(key)
                enrollment_map[key] = s
            
            # Also build a map of email -> list of enrolled courses (for multi-course check)
            email_courses = {}
            for s in Student.objects.filter(identity__email__in=csv_emails).select_related('identity'):
                email_courses.setdefault(s.identity.email, set()).add(s.course)
            
            # Query 3: All Team records for these students (to check team_id assignment)
            email_has_team = set()
            email_has_team_with_others = set()
            for t in Team.objects.filter(student__email__in=csv_emails, team_id__isnull=False):
                email_has_team.add(t.student_id)
                if t.team_count and t.team_count > 1:
                    email_has_team_with_others.add(t.student_id)
            
            # ── Step 3b: Pre-analyze CSV for internal multi-course conflicts ──
            csv_email_courses = {} # email -> set of courses in this CSV
            for row_num, name, email, course in valid_rows:
                csv_email_courses.setdefault(email, set()).add(course)
            
            # emails that appear with >1 course in THIS csv
            internal_multi_emails = {e for e, cset in csv_email_courses.items() if len(cset) > 1}

            # ── Step 4: Diff & build bulk lists ────────────────────────
            new_identities = []
            new_enrollments = []
            seen_emails = set()  # track first appearance within the CSV
            flagged_emails = set() # track emails already flagged as inconsistent in this loop
            inc_applied_log = []
            
            with transaction.atomic():
                for row_num, name, email, course in valid_rows:
                    try:
                        # --- Identity check ---
                        if email in existing_identities:
                            identity = existing_identities[email]
                            if identity.name.strip().lower() != name.strip().lower():
                                report["inconsistencies"].append({
                                    "row": row_num,
                                    "email": email,
                                    "type": "name_mismatch",
                                    "severity": "low",
                                    "reason": f"Name mismatch: CSV='{name}', DB='{identity.name}'",
                                    "csv_value": name,
                                    "db_value": identity.name
                                })
                        else:
                            if email not in seen_emails:
                                new_identity = StudentIdentity(email=email, name=name)
                                new_identities.append(new_identity)
                                existing_identities[email] = new_identity  # track for subsequent rows
                        
                        seen_emails.add(email)
                        
                        # --- Enrollment & Multi-course check ---
                        is_inconsistent = False
                        
                        # A: Internal Multi-course (same email, different courses in ONE csv)
                        if email in internal_multi_emails:
                            is_inconsistent = True
                            report["inconsistencies"].append({
                                "row": row_num,
                                "email": email,
                                "type": "multiple_courses",
                                "severity": "high",
                                "reason": f"CSV Error: Student appears multiple times with different courses: {', '.join(sorted(csv_email_courses[email]))}",
                                "csv_value": course,
                                "db_value": "Multiple in CSV"
                            })
                        
                        # B: External Multi-course (email already has OTHER courses in DB)
                        elif email in email_courses:
                            other_courses = email_courses[email] - {course}
                            if other_courses:
                                is_inconsistent = True
                                # CRITICAL = team_id assigned AND team has other members (solo team = HIGH only)
                                if email in email_has_team_with_others:
                                    severity = "critical"
                                elif email in email_has_team:
                                    severity = "high"
                                else:
                                    severity = "high"
                                
                                report["inconsistencies"].append({
                                    "row": row_num,
                                    "email": email,
                                    "type": "multiple_courses",
                                    "severity": severity,
                                    "reason": f"DB Conflict: Student already enrolled in: {', '.join(sorted(other_courses))}",
                                    "csv_value": course,
                                    "db_value": ", ".join(sorted(other_courses))
                                })
                        
                        if not is_inconsistent:
                            enrollment_key = (email, course)
                            if enrollment_key in existing_enrollments:
                                # Check if existing enrollment is LegacyArchived — flag for admin approval
                                existing_student = enrollment_map.get(enrollment_key)
                                if existing_student and existing_student.status == "LegacyArchived":
                                    report["inconsistencies"].append({
                                        "row": row_num,
                                        "email": email,
                                        "type": "legacy_archived",
                                        "severity": "high",
                                        "reason": f"Student is archived as a legacy user (old portal). Re-activating requires admin approval.",
                                        "csv_value": course,
                                        "db_value": "LegacyArchived"
                                    })
                                else:
                                    report["existing_enrollments_count"] += 1
                            else:
                                new_enrollments.append((row_num, email, course))
                                existing_enrollments.add(enrollment_key)
                        
                        # Track the course for subsequent rows
                        email_courses.setdefault(email, set()).add(course)
                        
                    except Exception as e:
                        report["errors"].append({
                            "row": row_num,
                            "email": email,
                            "reason": str(e)
                        })
                
                # ── Step 4b: Enrich critical entries (same vs different people) ──
                critical_emails_set = {
                    inc["email"] for inc in report["inconsistencies"]
                    if inc.get("severity") == "critical"
                }
                if critical_emails_set:
                    # All emails flagged as inconsistencies anywhere in the CSV
                    all_flagged_emails = {inc["email"] for inc in report["inconsistencies"]}
                    
                    # Fetch team records for critical students in one query
                    team_records = Team.objects.filter(
                        student__email__in=list(critical_emails_set),
                        team_id__isnull=False
                    )
                    # Build email -> list of teammate emails (from team_preference)
                    email_to_teammates = {}
                    for t in team_records:
                        prefs = t.team_preference or []
                        if isinstance(prefs, str):
                            import json
                            try: prefs = json.loads(prefs)
                            except: prefs = []
                        email_to_teammates.setdefault(t.student_id, set()).update(
                            e.strip().lower() for e in prefs if isinstance(e, str)
                        )
                    
                    # Reclassify
                    for inc in report["inconsistencies"]:
                        if inc.get("severity") != "critical":
                            continue
                        email = inc["email"]
                        teammates = email_to_teammates.get(email, set()) - {email}
                        clean_teammates = teammates - all_flagged_emails
                        if clean_teammates:
                            inc["severity"] = "critical_other"  # some clean teammates = risky
                        else:
                            inc["severity"] = "critical_same"   # all teammates also flagged = safer

                modified_log = {}
                modified_identities_log = {}
                created_identities_log = []
                created_enrollments_log = []

                # ── Step 4c: Apply Allowed Inconsistencies ──
                if not is_dry_run and allowed_inconsistencies:
                    logger.info(f"Applying {len(allowed_inconsistencies)} inconsistencies")

                    # Batch-process legacy_archived BEFORE the main loop to avoid
                    # N×2 individual DB queries (one get + one filter per row) which
                    # causes timeouts when hundreds of rows are allowed at once.
                    legacy_pairs = set()  # (email, course) to reactivate
                    for idx in allowed_inconsistencies:
                        if idx < len(report["inconsistencies"]):
                            inc = report["inconsistencies"][idx]
                            if inc["type"] == "legacy_archived":
                                legacy_pairs.add((inc["email"], inc["csv_value"]))

                    if legacy_pairs:
                        legacy_emails = [p[0] for p in legacy_pairs]
                        # Single query to fetch all matching LegacyArchived students
                        legacy_students = Student.objects.filter(
                            identity__email__in=legacy_emails,
                            status="LegacyArchived",
                        ).select_related("identity", "team")
                        # Build lookup (email, course) -> Student
                        legacy_lookup = {(s.identity.email, s.course): s for s in legacy_students}

                        ids_to_reactivate = []
                        for email, course in legacy_pairs:
                            s = legacy_lookup.get((email, course))
                            if s:
                                modified_log[str(s.id)] = {
                                    "previous_status": s.status,
                                    "previous_team_id": s.team.team_id if s.team else None,
                                }
                                ids_to_reactivate.append(s.id)
                                inc_applied_log.append(f"Re-activated {email} ({course}): LegacyArchived → Registered")
                            else:
                                logger.info(f"legacy_archived skip: {email}/{course} not found as LegacyArchived (already reactivated or status changed)")

                        if ids_to_reactivate:
                            # Single bulk update — clears team, resets status
                            Student.objects.filter(id__in=ids_to_reactivate).update(
                                status="Registered",
                                team=None,
                            )
                            logger.info(f"Bulk re-activated {len(ids_to_reactivate)} LegacyArchived students → Registered")

                    for idx in allowed_inconsistencies:
                        if idx < len(report["inconsistencies"]):
                            inc = report["inconsistencies"][idx]
                            if inc["type"] == "legacy_archived":
                                continue  # already handled above
                            email = inc["email"]
                            logger.info(f"Processing inconsistency {idx}: {email} ({inc['type']})")
                            if inc["type"] == "name_mismatch":
                                # Log changes for identity
                                current_ident = StudentIdentity.objects.get(email=email)
                                modified_identities_log[email] = {"previous_name": current_ident.name}
                                updated_name = StudentIdentity.objects.filter(email=email).update(name=inc["csv_value"])
                                logger.info(f"Updated identity name for {email}: {updated_name} row(s)")
                                inc_applied_log.append(f"Fixed name for {email}: {current_ident.name} -> {inc['csv_value']}")
                            
                            elif inc["type"] == "multiple_courses":
                                # Handle both existing and new students
                                target_course = inc["csv_value"]
                                ident, _ = StudentIdentity.objects.get_or_create(email=email, defaults={"name": inc.get("csv_value_name", email)})
                                s_qs = Student.objects.filter(identity=ident)
                                
                                # Check if a record with the target course already exists
                                s_match = s_qs.filter(course=target_course).first()
                                if s_match:
                                    # We already have an AIE record, so we just delete all others
                                    deleted_count, _ = s_qs.exclude(id=s_match.id).delete()
                                    logger.info(f"Target course {target_course} already exists for {email}. Deleted {deleted_count} other records.")
                                    inc_applied_log.append(f"Kept existing {target_course} and removed others for {email}")
                                elif s_qs.exists():
                                    # No record with target course, take the first one and update it
                                    s = s_qs.order_by('id').first()
                                    old_course = s.course

                                    # LOG PREVIOUS STATE
                                    modified_log[str(s.id)] = {
                                        "previous_course": old_course,
                                        "previous_status": s.status,
                                        "previous_team_id": s.team.team_id if s.team else None
                                    }

                                    new_status = s.status
                                    if s.status == "TeamIDGiven":
                                        new_status = "Registered"

                                    # Update the primary record
                                    Student.objects.filter(id=s.id).update(
                                        course=target_course,
                                        team=None,
                                        status=new_status
                                    )
                                    logger.info(f"Updated record {s.id} for {email} to course {target_course}")
                                    inc_applied_log.append(f"Switched {email} to course {target_course}")

                                    # Transfer pending team preference to the new course
                                    # so the student doesn't need to resubmit their preference
                                    pending_prefs = Team.objects.filter(
                                        student=ident,
                                        course=old_course,
                                        team_id__isnull=True,
                                        student_submitted=True,
                                    )
                                    if pending_prefs.exists():
                                        # Keep only the most recent, delete duplicates, move to new course
                                        latest_pref = pending_prefs.order_by('-preference_submitted_at').first()
                                        pending_prefs.exclude(id=latest_pref.id).delete()
                                        # Only move if there isn't already a pending pref for target_course
                                        if not Team.objects.filter(student=ident, course=target_course, team_id__isnull=True).exists():
                                            latest_pref.course = target_course
                                            latest_pref.save(update_fields=['course'])
                                            inc_applied_log.append(f"Transferred pending {latest_pref.team_preference} preference to {target_course} for {email}")
                                        else:
                                            latest_pref.delete()

                                    # Delete other courses if any left (shouldn't be, but safe)
                                    # Note: We don't currently support "Undo" for these auxiliary deletions,
                                    # but they are usually cleanup of duplicates.
                                    deleted_count, _ = s_qs.exclude(id=s.id).delete()
                                else:
                                    # New enrollment for existing/new identity
                                    new_s = Student.objects.create(identity=ident, course=target_course, status="Registered")
                                    logger.info(f"Created new enrollment for {email} in course {target_course}")
                                    inc_applied_log.append(f"Created enrollment for {email} in {target_course}")
                                    created_enrollments_log.append({"email": email, "course": inc["csv_value"]})

                            elif inc["type"] == "legacy_archived":
                                # Re-activate a LegacyArchived student: reset to Registered,
                                # clear team assignment so they go through onboarding fresh.
                                target_course = inc["csv_value"]
                                try:
                                    ident = StudentIdentity.objects.get(email=email)
                                    s = Student.objects.filter(identity=ident, course=target_course, status="LegacyArchived").first()
                                    if s:
                                        modified_log[str(s.id)] = {
                                            "previous_status": s.status,
                                            "previous_team_id": s.team.team_id if s.team else None,
                                        }
                                        Student.objects.filter(id=s.id).update(
                                            status="Registered",
                                            team=None,
                                        )
                                        logger.info(f"Re-activated LegacyArchived student {email} ({target_course}) → Registered")
                                        inc_applied_log.append(f"Re-activated {email} ({target_course}): LegacyArchived → Registered")
                                    else:
                                        logger.warning(f"legacy_archived allow: no LegacyArchived Student found for {email} / {target_course}")
                                except StudentIdentity.DoesNotExist:
                                    logger.warning(f"legacy_archived allow: StudentIdentity not found for {email}")


                # ── Step 5: Bulk insert ────────────────────────────────
                logger.info(f"CSV Step 5: new_identities={len(new_identities)}, new_enrollments={len(new_enrollments)}, ingest_new={ingest_new}, is_dry_run={is_dry_run}")
                if new_identities and ingest_new:
                    if not is_dry_run:
                        StudentIdentity.objects.bulk_create(new_identities, ignore_conflicts=True)
                        created_identities_log = [si.email for si in new_identities]
                    report["new_identities_count"] = len(new_identities)
                else:
                    report["new_identities_count"] = 0
                
                if new_enrollments and ingest_new:
                    # Always fetch fresh from DB — bulk_create with ignore_conflicts=True
                    # does NOT populate PKs on returned objects in MySQL, so we must
                    # re-query to get valid identity references for the FK.
                    enrollment_emails = list({e[1] for e in new_enrollments})
                    identity_lookup = {
                        si.email: si
                        for si in StudentIdentity.objects.filter(email__in=enrollment_emails)
                    }
                    logger.info(f"CSV Step 5: identity_lookup has {len(identity_lookup)} entries for {len(enrollment_emails)} enrollment emails")
                    
                    student_objects = []
                    for row_num, email, course in new_enrollments:
                        identity = identity_lookup.get(email)
                        if identity:
                            student_objects.append(Student(
                                identity=identity,
                                course=course,
                                status='Registered'
                            ))
                            created_enrollments_log.append({"email": email, "course": course})
                        else:
                            report["errors"].append({
                                "row": row_num,
                                "email": email,
                                "reason": "Identity missing for enrollment"
                            })
                    
                    if student_objects and not is_dry_run:
                        Student.objects.bulk_create(student_objects, ignore_conflicts=True)
                    
                    report["new_enrollments_count"] = len(student_objects)
                else:
                    report["new_enrollments_count"] = 0
                
                report["modified_count"] = len(modified_log) + len(modified_identities_log)

                if is_dry_run:
                    transaction.set_rollback(True)
                    report["message"] = "DRY RUN: Validation complete. NO changes were saved to the database."
                else:
                    # Save the Upload Log!
                    if created_identities_log or created_enrollments_log or modified_log or modified_identities_log:
                        CSVUploadLog.objects.create(
                            admin_email=admin_email,
                            created_identities=created_identities_log,
                            created_students=created_enrollments_log,
                            modified_students=modified_log,
                            modified_identities=modified_identities_log
                        )
                    report["message"] = f"LIVE RUN: {report['new_identities_count']} new identities, {report['new_enrollments_count']} new enrollments, and {report['modified_count']} modifications applied."
        
        finally:
            # ── Step 6: Restore cache signals ─────────────────────────
            for Model in models_to_disconnect:
                post_save.connect(
                    _cache_invalidation_handler,
                    sender=Model,
                    dispatch_uid=f"cache_invalidate_save_{Model.__name__}"
                )
                post_delete.connect(
                    _cache_invalidation_handler,
                    sender=Model,
                    dispatch_uid=f"cache_invalidate_delete_{Model.__name__}"
                )
        
        return JsonResponse(report)
        
    except Exception as e:
        logger.error(f"CSV Upload critical error: {str(e)}", exc_info=True)
        return JsonResponse({
            "success": False,
            "error": f"Server error processing CSV: {str(e)}"
        }, status=500)


@csrf_exempt
@admin_required
def pipeline_stats_api(request):
    """
    Returns pipeline status overview: counts of students at each stage,
    broken down by course.
    """
    if request.method != 'GET':
        return JsonResponse({"error": "GET required"}, status=405)

    _CACHE_KEY = "dashboard:pipeline_stats"
    _TTL = 120  # 2 minutes — invalidated by Student/Team/Evaluation signals

    cached = cache.get(_CACHE_KEY)
    if cached:
        return JsonResponse(cached)

    try:
        # Overall counts by status
        status_counts = (
            Student.objects.values('status')
            .annotate(count=Count('id'))
            .order_by('status')
        )

        # Counts by course + status
        course_status = (
            Student.objects.values('course', 'status')
            .annotate(count=Count('id'))
            .order_by('course', 'status')
        )

        # Team stats
        total_teams = Team.objects.count()
        teams_with_id = Team.objects.filter(team_id__isnull=False).count()
        teams_pending = Team.objects.filter(team_id__isnull=True).count()

        # Evaluation stats
        evals_awaiting_review = Evaluation.objects.filter(reviewed=False).count()
        emails_unsent = Evaluation.objects.filter(reviewed=True, email_sent=False).count()

        # Identity and Enrollment counts
        total_identities = StudentIdentity.objects.count()
        total_enrollments = Student.objects.count()

        # Course count
        enrolled_courses = Course.objects.count()

        payload = {
            "success": True,
            "total_identities": total_identities,
            "total_enrollments": total_enrollments,
            "enrolled_courses": enrolled_courses,
            "status_counts": list(status_counts),
            "course_status": list(course_status),
            "teams": {
                "total": total_teams,
                "assigned": teams_with_id,
                "pending": teams_pending,
            },
            "evaluations": {
                "awaiting_review": evals_awaiting_review,
                "emails_unsent": emails_unsent,
            }
        }
        cache.set(_CACHE_KEY, payload, _TTL)
        return JsonResponse(payload)
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)


def _get_student_details(identity):
    """Helper to return full student/enrollment/preference data for an identity."""
    from collections import defaultdict

    enrollments = list(Student.objects.filter(identity=identity).select_related('team'))

    # Group ALL Team rows for this identity by course (includes pending + assigned)
    all_team_rows = Team.objects.filter(student=identity).order_by('-preference_submitted_at')
    course_to_teams = defaultdict(list)
    for t in all_team_rows:
        course_to_teams[t.course].append(t)

    enrollment_data = []
    for e in enrollments:
        # The active team row is what Student.team FK points to (non-null team_id)
        active_team_pk = e.team.pk if e.team else None
        team_info = None
        if e.team:
            team_info = {
                "team_row_id": e.team.pk,
                "team_id": e.team.team_id,
                "team_preference": e.team.team_preference,
                "team_count": e.team.team_count,
            }

        course_teams = course_to_teams.get(e.course, [])

        # Split into: active (FK target), null-id rows, and real-id orphans
        null_id_rows = [t for t in course_teams if not t.team_id]
        real_id_orphans = [t for t in course_teams if t.team_id and t.pk != active_team_pk]

        # If there is an active assigned team, any null-id rows are stale — auto-delete them
        if active_team_pk:
            for t in null_id_rows:
                logger.info(f"Auto-deleting stale Team row pk={t.pk} for {identity.email}/{e.course}")
                t.delete()
            null_id_rows = []

        # For courses with no active team: keep the most recent null-id row as "pending pref",
        # auto-delete older duplicates
        pending_pref = None
        if null_id_rows:
            pending_pref = {
                "team_row_id": null_id_rows[0].pk,
                "team_preference": null_id_rows[0].team_preference,
                "preference_submitted_at": str(null_id_rows[0].preference_submitted_at) if null_id_rows[0].preference_submitted_at else None,
            }
            for t in null_id_rows[1:]:
                logger.info(f"Auto-deleting duplicate pending Team row pk={t.pk} for {identity.email}/{e.course}")
                t.delete()

        enrollment_data.append({
            "enrollment_id": e.id,
            "course": e.course,
            "status": e.status,
            "status_updated_at": str(e.status_updated_at) if e.status_updated_at else None,
            "team": team_info,
            "pending_pref": pending_pref,   # State B: submitted but not yet assigned
            "duplicate_prefs": [            # Edge case: orphan rows with real team_id
                {
                    "team_row_id": t.pk,
                    "team_id": t.team_id,
                    "team_preference": t.team_preference,
                }
                for t in real_id_orphans
            ],
        })

    # Evaluations for all assigned teams
    team_ids = [e.team.team_id for e in enrollments if e.team and e.team.team_id]
    evaluations_data = []
    if team_ids:
        evals = Evaluation.objects.filter(
            team__team_id__in=team_ids
        ).select_related("team", "project")
        for ev in evals:
            evaluations_data.append({
                "evaluation_id": ev.id,
                "team_id": ev.team.team_id,
                "project_id": ev.project.project_id,
                "course": ev.course,
                "grade": ev.evaluation_grade,
                "email_sent": ev.email_sent,
                "reviewed": ev.reviewed,
                "visibility_after": str(ev.visibility_after) if ev.visibility_after else None,
            })

    return {
        "email": identity.email,
        "name": identity.name,
        "phone": identity.phone,
        "NOC_issued": identity.NOC_issued,
        "enrollments": enrollment_data,
        "evaluations": evaluations_data,
    }


@csrf_exempt
@admin_required
def student_lookup_api(request):
    """
    Search for students by name or email (partial match).
    GET /portal/api/student-lookup/?q=john
    Returns full pipeline info for matched students.
    """
    if request.method != 'GET':
        return JsonResponse({"error": "GET required"}, status=405)
    
    query = request.GET.get('q', '').strip()
    if not query or len(query) < 2:
        return JsonResponse({"success": False, "error": "Search query must be at least 2 characters"}, status=400)

    _cache_key = f"students:lookup:{query.lower()}"
    _cached = cache.get(_cache_key)
    if _cached:
        return JsonResponse(_cached)

    try:
        # 1. Search StudentIdentity by name or email (direct matches)
        identities = set(StudentIdentity.objects.filter(
            Q(email__icontains=query) | Q(name__icontains=query)
        ))

        # 2. Search for students by Team ID (and include ALL members of matched teams)
        team_matches = Student.objects.filter(
            team__team_id__icontains=query
        ).select_related('identity')

        for s in team_matches:
            identities.add(s.identity)

        # 3. Convert back to list and cap at 50 results
        unique_identities = sorted(identities, key=lambda x: x.email)

        results = []
        for identity in unique_identities[:50]:
            results.append(_get_student_details(identity))

        payload = {"success": True, "count": len(results), "results": results}
        cache.set(_cache_key, payload, 60)
        return JsonResponse(payload)
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)

@csrf_exempt
@admin_required
def team_roster_api(request):
    """
    GET /portal/api/team-roster/?team_id=PTID-CDS-MAR-26-11123&flagged_emails=a@b.com,c@d.com
    Returns all members of a team with their status and whether they are flagged.
    """
    if request.method != 'GET':
        return JsonResponse({"error": "GET required"}, status=405)

    team_id = request.GET.get('team_id', '').strip()
    flagged_param = request.GET.get('flagged_emails', '')
    flagged_emails = set(e.strip().lower() for e in flagged_param.split(',') if e.strip())

    if not team_id:
        return JsonResponse({"success": False, "error": "team_id is required"}, status=400)

    try:
        # The Team table has exactly 1 row per team_id (the leader's preference row).
        # All members are in the Student table pointing to that team via TeamID FK.
        # Fetch the team row once for preference metadata, then query all enrolled students.
        team_row = Team.objects.filter(team_id=team_id).select_related('student').first()
        enrolled_students = (
            Student.objects
            .filter(team__team_id=team_id)
            .select_related('identity', 'team')
        )
        members = []
        for student in enrolled_students:
            identity = student.identity
            if not identity:
                continue
            # All enrollments for this identity (across courses)
            enrollments = Student.objects.filter(identity=identity).select_related('team')
            enrollment_data = [
                {
                    "course": e.course,
                    "status": e.status,
                    "team_id": e.team.team_id if e.team else None,
                }
                for e in enrollments
            ]
            members.append({
                "email": identity.email,
                "name": identity.name,
                "is_flagged": identity.email in flagged_emails,
                "enrollments": enrollment_data,
                "team_preference": team_row.team_preference if team_row else None,
            })

        return JsonResponse({
            "success": True,
            "team_id": team_id,
            "member_count": len(members),
            "members": members,
        })
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)

@csrf_exempt
@admin_required
def student_update_api(request):
    """
    PATCH /portal/api/student-update/
    Update student identity, enrollment status/course, or team_id.
    Body (JSON):
    {
        "email": "student@example.com",
        "name": "New Name",           # optional
        "phone": "9876543210",         # optional
        "course": "CDS",               # optional - which enrollment to target
        "new_course": "CDA",           # optional - change course
        "status": "TeamIDGiven",       # optional
        "team_id": "PTID-CDA-...",     # optional
    }
    """
    if request.method != 'PATCH':
        return JsonResponse({"error": "PATCH required"}, status=405)

    try:
        import json as _json
        body = _json.loads(request.body)
    except Exception:
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    email = body.get("email", "").strip().lower()
    if not email:
        return JsonResponse({"error": "email is required"}, status=400)

    try:
        identity = StudentIdentity.objects.get(email=email)
    except StudentIdentity.DoesNotExist:
        return JsonResponse({"error": f"Student with email {email} not found"}, status=404)

    changes = []

    # ── Update identity fields ──────────────────────────────────
    identity_updated = False
    if "name" in body and body["name"].strip():
        identity.name = body["name"].strip()
        identity_updated = True
        changes.append(f"name → {identity.name}")
    if "phone" in body:
        identity.phone = body["phone"].strip() if body["phone"] else ""
        identity_updated = True
        changes.append(f"phone → {identity.phone}")
    
    if "NOC_issued" in body:
        identity.NOC_issued = bool(body["NOC_issued"])
        identity_updated = True
        changes.append(f"NOC_issued → {identity.NOC_issued}")

    # NEW: Password Reset
    if "password" in body and body["password"].strip():
        from django.contrib.auth.hashers import make_password
        identity.portal_password = make_password(body["password"].strip())
        identity_updated = True
        changes.append("password updated (hashed)")
        
    if identity_updated:
        identity.save()

    # ── Update enrollment ────────────────────────────────────────
    course = body.get("course", "").strip().upper()
    new_course = body.get("new_course", "").strip().upper()
    new_status = body.get("status", "").strip()
    new_team_id = body.get("team_id", "").strip()

    if course:
        try:
            enrollment = Student.objects.get(identity=identity, course=course)
        except Student.DoesNotExist:
            return JsonResponse({"error": f"No enrollment found for {email} in course {course}"}, status=404)

        if new_course and new_course != course:
            # Check for collision — enrollment for new_course may already exist
            if Student.objects.filter(identity=identity, course=new_course).exists():
                return JsonResponse(
                    {"error": f"{email} already has an enrollment in course {new_course}. "
                               "Remove that enrollment first before transferring."},
                    status=400,
                )
            # Detach from the old course team and reset to fresh state
            old_team_id = enrollment.team.team_id if enrollment.team else None
            enrollment.team = None
            enrollment.status = "Registered"
            enrollment.status_updated_at = None
            enrollment.course = new_course
            detail = f"course {course} → {new_course}; status reset to Registered; team detached"
            if old_team_id:
                detail += f" (was {old_team_id})"
            changes.append(detail)

            # Transfer pending team preference to the new course
            pending_prefs = Team.objects.filter(
                student=identity,
                course=course,
                team_id__isnull=True,
                student_submitted=True,
            )
            if pending_prefs.exists():
                latest_pref = pending_prefs.order_by('-preference_submitted_at').first()
                pending_prefs.exclude(id=latest_pref.id).delete()
                if not Team.objects.filter(student=identity, course=new_course, team_id__isnull=True).exists():
                    latest_pref.course = new_course
                    latest_pref.save(update_fields=['course'])
                    changes.append(f"transferred pending {latest_pref.team_preference} preference to {new_course}")
                else:
                    latest_pref.delete()

        if new_status:
            valid_statuses = [s[0] for s in Student.STATUS_CHOICES]
            if new_status not in valid_statuses:
                return JsonResponse({"error": f"Invalid status '{new_status}'. Valid: {valid_statuses}"}, status=400)
            enrollment.status = new_status
            from django.utils import timezone as tz
            enrollment.status_updated_at = tz.now()
            changes.append(f"status → {new_status}")

        if new_team_id:
            try:
                team = Team.objects.get(team_id=new_team_id)
                # Team exists — require confirmation unless force flag is set
                if not body.get("force_team_assign"):
                    member_count = Student.objects.filter(team=team).count()
                    existing_members = list(
                        Student.objects.filter(team=team)
                        .select_related('identity')
                        .values_list('identity__email', flat=True)
                    )
                    return JsonResponse({
                        "requires_confirmation": True,
                        "team_id": new_team_id,
                        "member_count": member_count,
                        "existing_members": existing_members,
                        "message": (
                            f"Team {new_team_id} already exists with "
                            f"{member_count} member(s): {', '.join(existing_members)}. "
                            f"Add {email} to this team?"
                        ),
                    }, status=200)
                enrollment.team = team
                changes.append(f"team → {new_team_id} (joined existing)")
            except Team.DoesNotExist:
                # Team doesn't exist — create it with this student as the owner
                from django.utils import timezone as tz
                team = Team.objects.create(
                    team_id=new_team_id,
                    student=identity,
                    course=enrollment.course,
                    team_preference='individual',
                    student_submitted=False,
                    created_at=tz.now(),
                )
                enrollment.team = team
                changes.append(f"team → {new_team_id} (new team created)")
        elif "team_id" in body and body["team_id"] == "":
            # Explicitly clearing team
            enrollment.team = None
            changes.append("team_id cleared")

        # Save ONLY the fields we explicitly changed for this one enrollment.
        # Using update_fields prevents any stale in-memory state from accidentally
        # overwriting other columns and ensures no other student rows are touched.
        save_fields = ["status", "status_updated_at", "team", "course"]
        enrollment.save(update_fields=save_fields)

    if not changes:
        return JsonResponse({"success": False, "message": "No changes provided"}, status=400)

    invalidate_cache(pattern="students:lookup:*")
    invalidate_cache(pattern="students:table:*")
    invalidate_cache(specific_key="dashboard:pipeline_stats")

    log_admin_action(request.session.get("admin_email"), "UPDATE_STUDENT", "Student", email, {"changes": changes}, request)

    # Build a per-student summary so the caller knows exactly what changed for whom.
    response = {
        "success": True,
        "email": email,
        "changes": changes,
        "updated_student_only": True,   # explicit flag — only this student was touched
    }
    # If a team change happened, include the new team membership count
    if any("team →" in c for c in changes):
        enrollment.refresh_from_db(fields=["team"])
        if enrollment.team:
            member_count = Student.objects.filter(team=enrollment.team).count()
            response["new_team_id"] = enrollment.team.team_id
            response["team_member_count"] = member_count
    return JsonResponse(response)

@csrf_exempt
@admin_required
def team_update_api(request):
    """
    PATCH /portal/api/team-update/
    Update team preference/teammates or team projects.
    Body (JSON):
    {
        "team_id": "PTID-CDA-...", 
        "team_preference": ["a@b.com", "c@d.com"],       # optional list of teammate emails
        "projects": [                                    # optional
            {"project_id": "CAP-001", "status": "assigned"}
        ]
    }
    """
    if request.method != 'PATCH':
        return JsonResponse({"error": "PATCH required"}, status=405)

    try:
        import json as _json
        body = _json.loads(request.body)
    except Exception:
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    team_id = body.get("team_id", "").strip()
    if not team_id:
        return JsonResponse({"error": "team_id is required"}, status=400)

    # Note: team_id is unique per team but there are multiple Team objects (one per student in the team)
    # They should all share the same team_preference conceptually, but the model has it per student.
    # So we update *all* Team records that have this team_id.
    teams = Team.objects.filter(team_id=team_id)
    if not teams.exists():
        return JsonResponse({"error": f"No teams found with ID {team_id}"}, status=404)

    changes = []
    
    # ── Update team preferences (teammates) ────────────────────
    if "team_preference" in body:
        prefs = body["team_preference"]
        if not isinstance(prefs, list):
            return JsonResponse({"error": "team_preference must be a list of emails"}, status=400)
            
        import json as _json
        prefs_json = _json.dumps(prefs)
        
        # Update preference and team_count for all members
        teams.update(
            team_preference=prefs_json, 
            team_count=len(prefs)
        )
        changes.append(f"Updated preferences/teammates count to {len(prefs)}")

    # ── Update Team Projects ───────────────────────────────────
    if "projects" in body:
        proj_updates = body["projects"]
        if not isinstance(proj_updates, list):
            return JsonResponse({"error": "projects must be a list of objects"}, status=400)
            
        # We need an instance of Team representing the group to update TeamProject
        # (TeamProject foreign key points to team_id, but the Django ORM expects the related Team model instance)
        # Any instance with this team_id works since to_field="team_id"
        base_team = teams.first()
        
        for pdata in proj_updates:
            pid = pdata.get("project_id")
            pstatus = pdata.get("status")
            if not pid or not pstatus:
                continue
                
            try:
                project = ProjectRegistry.objects.get(project_id=pid)
                # update or create TeamProject record
                tp, created = TeamProject.objects.update_or_create(
                    team=base_team,
                    project=project,
                    defaults={'status': pstatus}
                )
                action = "Assigned" if created else "Updated status of"
                changes.append(f"{action} project {pid} to {pstatus}")
            except ProjectRegistry.DoesNotExist:
                changes.append(f"Failed to assign project {pid}: Not found in registry")

    if not changes:
        return JsonResponse({"success": False, "message": "No valid changes provided"}, status=400)

    log_admin_action(request.session.get("admin_email"), "UPDATE_TEAM", "Team", team_id, {"changes": changes}, request)
    return JsonResponse({
        "success": True,
        "team_id": team_id,
        "changes": changes,
    })

@csrf_exempt
@admin_required
def all_student_emails_api(request):
    """
    GET /portal/api/all-student-emails/
    Returns a list of all registered student emails for UI dropdowns.
    """
    emails = list(StudentIdentity.objects.values_list('email', flat=True))
    return JsonResponse({"emails": emails})


@csrf_exempt
@admin_required
def team_formation_preview_api(request):
    """
    GET /portal/api/team-formation-preview/
    Shows students who have submitted a team preference AND don't have a team ID yet.
    Only student_submitted=True rows are counted — excludes admin-created/imported rows.
    Query Params: ?size=4
    """
    try:
        size = int(request.GET.get("size", 4))
    except ValueError:
        size = 4

    # Only count students who logged in and submitted a preference themselves.
    # Deduplicate by (student email, course) so duplicate preference rows don't
    # inflate the count (a student can only be in one team per course).
    pending_list = list(
        Team.objects.filter(
            team_id__isnull=True,
            student_submitted=True,
        ).values("student__email", "course", "team_preference")
        .distinct()
    )

    # Further deduplicate in Python: if a student somehow has two rows for the
    # same course, keep only the first (lowest id is fetched first by default).
    seen = set()
    deduped = []
    for row in pending_list:
        key = (row["student__email"], row["course"])
        if key not in seen:
            seen.add(key)
            deduped.append(row)

    course_stats = {}
    for row in deduped:
        c = row["course"]
        if c not in course_stats:
            course_stats[c] = {"total_students": 0, "individuals": 0, "group_seekers": 0}
        course_stats[c]["total_students"] += 1
        if row["team_preference"] == "individual":
            course_stats[c]["individuals"] += 1
        else:
            course_stats[c]["group_seekers"] += 1

    results = []
    for c, stats in sorted(course_stats.items()):
        grp = stats["group_seekers"]
        results.append({
            "course": c,
            "total_students": stats["total_students"],
            "individuals": stats["individuals"],
            "full_teams": grp // size,
            "remainder": grp % size,
        })

    return JsonResponse({"courses": results, "total_pending": len(pending_list), "team_size": size})


@csrf_exempt
@admin_required
def team_formation_execute_api(request):
    """
    POST /portal/api/team-formation-execute/
    Creates teams for courses. 
    Body (JSON):
    {
        "course_settings": {
            "CDS": {"force_remainder": true},
            "CDA": {"force_remainder": false}
        }
    }
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
        
    try:
        import json as _json
        body = _json.loads(request.body)
    except Exception:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
        
    course_settings = body.get("course_settings", {})
    team_size = body.get("team_size", 4)
    
    import random
    import string
    from django.utils import timezone as tz
    
    from Submissions.services.team_id_service import generate_team_id_block
    
    results = []
    diagnostics = []  # per-course breakdown even when 0 teams formed
    total_teams_created = 0
    total_students_assigned = 0

    # Process courses based on settings dict
    try:
        with transaction.atomic():
            for course, settings in course_settings.items():
                force_remainder = settings.get("force_remainder", False)

                # Check preference rows first (for diagnostics)
                pending_prefs_count = Team.objects.filter(
                    team_id__isnull=True, student_submitted=True, course=course
                ).count()

                # Only students who self-submitted a preference (student_submitted=True)
                # and have a portal password set (meaning they've logged in at least once).
                # This mirrors the same eligibility criteria used by the preview API.
                submitted_emails = set(
                    Team.objects.filter(
                        team_id__isnull=True,
                        student_submitted=True,
                        course=course,
                    ).values_list("student__email", flat=True)
                )

                pending_students = list(Student.objects.select_related("identity").prefetch_related(
                    Prefetch(
                        "identity__team_preferences",
                        queryset=Team.objects.filter(course=course),
                        to_attr="course_prefs"
                    )
                ).filter(
                    status="Registered",
                    team__isnull=True,
                    course=course,
                    identity__email__in=submitted_emails,
                    identity__portal_password__isnull=False,
                ).exclude(identity__portal_password=""))

                diag = {
                    "course": course,
                    "preference_rows": pending_prefs_count,
                    "registered_students": len(pending_students),
                    "force_remainder": force_remainder,
                    "team_size": team_size,
                }

                if not pending_students:
                    # Break down WHY — are the preference-submitters already assigned, or missing?
                    if pending_prefs_count > 0:
                        pref_emails = list(
                            Team.objects.filter(
                                team_id__isnull=True, student_submitted=True, course=course
                            ).values_list("student__email", flat=True)
                        )
                        status_breakdown = list(
                            Student.objects.filter(identity__email__in=pref_emails, course=course)
                            .values("status").annotate(count=Count("id")).order_by("status")
                        )
                        enrolled_count = sum(r["count"] for r in status_breakdown)
                        diag["status_breakdown"] = status_breakdown
                        diag["no_enrollment_count"] = pending_prefs_count - enrolled_count
                    diag["skip_reason"] = "no_registered_students"
                    diagnostics.append(diag)
                    continue

                individuals = []
                group_seekers = []
                for s in pending_students:
                    prefs = s.identity.course_prefs  # already filtered to this course
                    if prefs and prefs[0].team_preference == "individual":
                        individuals.append(s)
                    else:
                        group_seekers.append(s)

                diag["individuals"] = len(individuals)
                diag["group_seekers"] = len(group_seekers)
                diag["full_teams_possible"] = len(group_seekers) // team_size
                diag["remainder"] = len(group_seekers) % team_size

                teams_made = 0
                students_assigned = 0
                team_records = []

                # 2. Process group seekers — figure out chunks first so we
                #    know how many IDs to allocate before touching the DB.
                random.shuffle(group_seekers)
                chunks = [group_seekers[i:i + team_size] for i in range(0, len(group_seekers), team_size)]

                # Filter out incomplete remainder if not forced
                valid_chunks = []
                for chunk in chunks:
                    if len(chunk) < team_size and not force_remainder:
                        diag["skip_reason"] = f"remainder_{len(chunk)}_students_force_remainder_false"
                        break
                    valid_chunks.append(chunk)

                # Allocate ALL IDs for this course in one DB read (prevents duplicates)
                total_teams_needed = len(individuals) + len(valid_chunks)
                id_block = generate_team_id_block(course, total_teams_needed)
                id_iter = iter(id_block)

                # 1. Process individuals
                for ind in individuals:
                    ptid = next(id_iter)
                    t = Team(
                        student=ind.identity,
                        team_id=ptid,
                        team_preference="individual",
                        team_count=1,
                        course=course
                    )
                    team_records.append(t)
                    teams_made += 1
                    students_assigned += 1

                assigned_students_with_tid = []  # list of (student, ptid) tuples
                for chunk in valid_chunks:
                    ptid = next(id_iter)
                    teammate_emails = [s.identity.email for s in chunk]

                    # ONE Team row per group (leader is chunk[0])
                    t = Team(
                        student=chunk[0].identity,
                        team_id=ptid,
                        team_preference="assign_team",
                        preference_emails=teammate_emails,
                        team_count=len(chunk),
                        course=course
                    )
                    team_records.append(t)

                    for student in chunk:
                        assigned_students_with_tid.append((student, ptid))

                    teams_made += 1
                    students_assigned += len(chunk)
                        
                if team_records:
                    # 1. Bulk create the Team records
                    Team.objects.bulk_create(team_records)
                    
                    # 2. Prepare Student updates
                    # We need to re-fetch or link the Team objects
                    # Actually, the Student.team FK is on 'team_id' (to_field='team_id')
                    # so we only need the PTID string to link them!
                    
                    all_students_to_update = []
                    
                    # Individuals
                    for ind in individuals:
                        ind.status = "TeamIDGiven"
                        # ind.team_id was already assigned if we kept it, but let's be explicit
                        # Search for the Team object we just made in team_records
                        my_ptid = next((tr.team_id for tr in team_records if tr.student_id == ind.identity.email and tr.team_count == 1), None)
                        if my_ptid:
                            ind.team_id = my_ptid
                            ind.status_updated_at = tz.now()
                            all_students_to_update.append(ind)

                    # Group members
                    for student, ptid in assigned_students_with_tid:
                        student.team_id = ptid
                        student.status = "TeamIDGiven"
                        student.status_updated_at = tz.now()
                        all_students_to_update.append(student)
                    
                    if all_students_to_update:
                        Student.objects.bulk_update(all_students_to_update, ["team_id", "status", "status_updated_at"])

                    # Clean up stale preference rows for students that were just assigned
                    assigned_emails = [s.identity.email for s in all_students_to_update]
                    deleted_count, _ = Team.objects.filter(
                        student__email__in=assigned_emails,
                        team_id__isnull=True,
                        student_submitted=True,
                        course=course,
                    ).delete()
                    if deleted_count:
                        diag["stale_prefs_cleaned"] = deleted_count

                diag["teams_made"] = teams_made
                diag["students_assigned"] = students_assigned
                diagnostics.append(diag)

                if teams_made > 0:
                    results.append({
                        "course": course,
                        "teams_created": teams_made,
                        "students_assigned": students_assigned
                    })
                    total_teams_created += teams_made
                    total_students_assigned += students_assigned

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({"success": False, "error": str(e)}, status=500)

    # After processing all courses
    if total_teams_created > 0:
        # Team formation emails are disabled — teams are formed silently
        log_admin_action(
            request.session.get("admin_email"),
            "TEAM_FORMATION_EXECUTE",
            "Team",
            "bulk_execution",
            {
                "teams_created": total_teams_created,
                "students_assigned": total_students_assigned,
                "results": results
            },
            request
        )

    return JsonResponse({
        "success": True,
        "total_teams_created": total_teams_created,
        "total_students_assigned": total_students_assigned,
        "details": results,
        "diagnostics": diagnostics,
    })


@csrf_exempt
@admin_required
def purge_stale_preferences_api(request):
    """
    POST /portal/api/purge-stale-preferences/
    Removes Team preference rows (team_id=NULL, student_submitted=True) for students
    who already have a team assigned (Student.team IS NOT NULL or status != Registered).
    These are orphaned rows left when the execute API ran but didn't clean up.
    Body (optional): {"course": "CDS"}  — omit to purge all courses.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    import json as _json
    try:
        body = _json.loads(request.body) if request.body else {}
    except Exception:
        body = {}

    course_filter = body.get("course", "")

    # Find stale preference rows: student_submitted=True, team_id=None
    # whose owner already has a non-null team assignment in Student table
    stale_qs = Team.objects.filter(team_id__isnull=True, student_submitted=True)
    if course_filter:
        stale_qs = stale_qs.filter(course=course_filter)

    # A preference row is stale if the student has a Student enrollment for
    # that course AND either: has a team assigned OR is no longer Registered
    stale_ids = []
    per_course_breakdown = {}
    for pref in stale_qs.select_related("student"):
        enrollment = Student.objects.filter(
            identity__email=pref.student.email,
            course=pref.course,
        ).first()
        if enrollment and (enrollment.team_id is not None or enrollment.status != "Registered"):
            stale_ids.append(pref.pk)
            per_course_breakdown[pref.course] = per_course_breakdown.get(pref.course, 0) + 1

    deleted = 0
    if stale_ids:
        deleted, _ = Team.objects.filter(pk__in=stale_ids).delete()

    log_admin_action(
        request.session.get("admin_email"),
        "PURGE_STALE_PREFERENCES",
        "Team",
        "bulk_purge",
        {"deleted": deleted, "breakdown": per_course_breakdown},
        request,
    )

    return JsonResponse({
        "success": True,
        "deleted": deleted,
        "breakdown": per_course_breakdown,
    })


@csrf_exempt
@admin_required
def list_csv_logs_api(request):
    """Returns the last 10 CSV upload logs for the Undo UI."""
    # PERFORMANCE: Use only() and sort by -id (indexed) to avoid huge filesort
    # Large JSONs in CSVUploadLog cause 'Out of sort memory' during filesort by timestamp
    logs = CSVUploadLog.objects.all().only('id', 'timestamp', 'admin_email').order_by('-id')[:10]
    data = []
    for log in logs:
        # Note: We skip summary/len() here because they trigger a re-fetch of deferred JSON fields
        data.append({
            "id": log.id,
            "timestamp": log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "admin": log.admin_email,
            "summary": f"Sync log #{log.id} at {log.timestamp.strftime('%H:%M')}"
        })
    return JsonResponse({"success": True, "logs": data})


@csrf_exempt
@admin_required
def undo_csv_upload_api(request):
    """
    Reverses the database changes generated by a specific CSV upload.
    Expects POST with {"log_id": id}
    """
    if request.method != "POST":
         return JsonResponse({"success": False, "error": "POST required"})
    import json
    try:
        body = json.loads(request.body)
        log_id = body.get("log_id")
        log = CSVUploadLog.objects.get(id=log_id)
        
        with transaction.atomic():
            # 1. Restore overridden students (course, status, team)
            for student_id_str, prev_state in log.modified_students.items():
                try:
                    s = Student.objects.get(id=int(student_id_str))
                    s.course = prev_state.get("previous_course")
                    s.status = prev_state.get("previous_status")
                    prev_team_id = prev_state.get("previous_team_id")
                    if prev_team_id:
                        team = Team.objects.filter(team_id=prev_team_id).first()
                        if team:
                            s.team = team
                    else:
                        s.team = None
                    s.save(update_fields=["course", "status", "team"])
                except Student.DoesNotExist:
                    pass
            
            # 2. Delete created enrollments
            for enr in log.created_students:
                 Student.objects.filter(identity__email=enr["email"], course=enr["course"]).delete()
                 
            # 3. Restore identity names
            if hasattr(log, "modified_identities") and log.modified_identities:
                for email, state in log.modified_identities.items():
                    StudentIdentity.objects.filter(email=email).update(name=state.get("previous_name"))

            # 4. Delete created identities
            if log.created_identities:
                # Only delete if they have NO other enrollments left
                for email in log.created_identities:
                    if not Student.objects.filter(identity__email=email).exists():
                        StudentIdentity.objects.filter(email=email).delete()
                 
            # Delete log itself
            log.delete()
            
        return JsonResponse({"success": True, "message": f"Changes from CSV sync restored successfully."})
        
    except Exception as e:
        logger.error(f"Undo error: {str(e)}")
        return JsonResponse({"success": False, "error": str(e)})


@csrf_exempt
@admin_required
def get_db_inconsistencies_api(request):
    """
    Detects database inconsistencies:
    1. Multiple active courses for one student.
    2. Multiple Team records for one (student, course).
    3. team_id (string) vs team (FK) mismatch in Student model.
    4. team_id set but no Team record exists.
    """
    inconsistencies = []

    # 1. Multiple Courses (Optimized)
    multi_course_data = list(Student.objects.values('identity_id').annotate(
        course_count=Count('id')
    ).filter(course_count__gt=1))
    
    for item in multi_course_data:
        email = item['identity_id']
        count = item['course_count']
        ident = StudentIdentity.objects.get(email=email)
        inconsistencies.append({
            "type": "multiple_courses",
            "identifier": email,
            "details": f"Enrolled in {count} courses",
            "severity": "high",
            "student_email": email,
            "student_data": _get_student_details(ident)
        })

    # 2. Multiple Team preferences for same (student, course)
    team_counts = Team.objects.values('student_id', 'course').annotate(
        count=Count('id')
    ).filter(count__gt=1)
    
    for tc in team_counts:
        email = tc['student_id']
        ident = StudentIdentity.objects.filter(email=email).first()
        inconsistencies.append({
            "type": "multiple_teams",
            "identifier": f"{email} ({tc['course']})",
            "details": f"Has {tc['count']} Team preference records for course {tc['course']}",
            "severity": "critical",
            "student_email": email,
            "course": tc['course'],
            "student_data": _get_student_details(ident) if ident else None
        })

    # 3. Team Assignment Inconsistencies (Bulk)
    # Check for mismatches and missing links in one pass over Student table
    students_with_teams = Student.objects.filter(
        Q(team__isnull=False) | (~Q(team_id__isnull=True) & ~Q(team_id=""))
    ).select_related('team', 'identity')
    # Bulk pre-fetch all needed data to avoid N+1
    all_emails = set()
    all_emails.update([item['identity_id'] for item in multi_course_data])
    all_emails.update([tc['student_id'] for tc in team_counts])
    all_emails.update([s.identity_id for s in students_with_teams])
    
    idents = {i.email: i for i in StudentIdentity.objects.filter(email__in=all_emails)}
    
    from collections import defaultdict
    enrolls_by_email = defaultdict(list)
    for e in Student.objects.filter(identity_id__in=all_emails).select_related('team'):
        team_info = None
        if e.team:
            team_info = {
                "team_id": e.team.team_id,
                "team_preference": e.team.team_preference,
                "team_count": e.team.team_count,
            }
        enrolls_by_email[e.identity_id].append({
            "course": e.course,
            "status": e.status,
            "status_updated_at": str(e.status_updated_at) if e.status_updated_at else None,
            "team": team_info,
        })
        
    prefs_by_email = defaultdict(list)
    for t in Team.objects.filter(student_id__in=all_emails):
        prefs_by_email[t.student_id].append({
            "team_id": t.team_id,
            "course": t.course,
            "team_preference": t.team_preference,
            "team_count": t.team_count,
            "preference_submitted_at": str(t.preference_submitted_at) if t.preference_submitted_at else None,
        })

    def _format_student(email):
        identity = idents.get(email)
        if not identity: return None
        return {
            "email": identity.email,
            "name": identity.name,
            "phone": identity.phone,
            "enrollments": enrolls_by_email.get(email, []),
            "team_preferences": prefs_by_email.get(email, []),
        }

    # 1. Multiple Courses
    for item in multi_course_data:
        email = item['identity_id']
        inconsistencies.append({
            "type": "multiple_courses",
            "identifier": email,
            "details": f"Enrolled in {item['course_count']} courses",
            "severity": "high",
            "student_email": email,
            "student_data": _format_student(email)
        })

    # 2. Multiple Team preferences for same (student, course)
    for tc in team_counts:
        email = tc['student_id']
        inconsistencies.append({
            "type": "multiple_teams",
            "identifier": f"{email} ({tc['course']})",
            "details": f"Has {tc['count']} Team preference records for course {tc['course']}",
            "severity": "critical",
            "student_email": email,
            "course": tc['course'],
            "student_data": _format_student(email)
        })

    # 3. Team Assignment Inconsistencies (Bulk)
    for s in students_with_teams:
        # Case 3: Team Mismatch (FK vs String)
        if s.team and s.team_id != s.team.team_id:
            inconsistencies.append({
                "type": "team_mismatch",
                "identifier": s.identity_id,
                "details": f"FK Team: {s.team.team_id}, String Team: {s.team_id}",
                "severity": "medium",
                "student_email": s.identity_id,
                "course": s.course,
                "fk_team_id": s.team.team_id,
                "str_team_id": s.team_id,
                "student_data": _format_student(s.identity_id)
            })
        # Case 4: Missing Team Assignment (String set but FK null)
        elif not s.team and s.team_id:
            inconsistencies.append({
                "type": "missing_team_link",
                "identifier": s.identity_id,
                "details": f"team_id set to '{s.team_id}' but no FK link",
                "severity": "medium",
                "student_email": s.identity_id,
                "course": s.course,
                "team_id": s.team_id,
                "student_data": _format_student(s.identity_id)
            })

    return JsonResponse({"success": True, "inconsistencies": inconsistencies})


@csrf_exempt
@admin_required
def resolve_inconsistency_api(request):
    """
    Resolves a specific inconsistency.
    POST {"type": "...", "student_email": "...", "course": "..."}
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST required"})
    
    import json
    try:
        body = json.loads(request.body)
        inc_type = body.get("type")
        email = body.get("student_email")
        course = body.get("course")
        
        with transaction.atomic():
            if inc_type == "multiple_courses":
                # Decision: Keep most recent enrollment, delete others? Or just report.
                # User asked for option to update, so we'll implement a 'keep latest' logic
                enrollments = Student.objects.filter(identity__email=email).order_by('-status_updated_at')
                if enrollments.count() > 1:
                    to_delete = enrollments[1:]
                    count = len(to_delete)
                    for e in to_delete:
                         e.delete()
                    return JsonResponse({"success": True, "message": f"Deleted {count} duplicate enrollments for {email}"})
            
            elif inc_type == "multiple_teams":
                 teams = Team.objects.filter(student_id=email, course=course).order_by('-id')
                 if teams.count() > 1:
                     to_delete = teams[1:]
                     count = len(to_delete)
                     for t in to_delete:
                          t.delete()
                     return JsonResponse({"success": True, "message": f"Deleted {count} duplicate Team records for {email}"})
            
            elif inc_type == "team_mismatch" or inc_type == "missing_team_link":
                 student = Student.objects.get(identity__email=email, course=course)
                 if student.team_id:
                     team = Team.objects.filter(team_id=student.team_id).first()
                     if team:
                         student.team = team
                         student.save(update_fields=["team"])
                         return JsonResponse({"success": True, "message": f"Synced team FK for {email}"})
                     else:
                         # Team ID string is invalid, clear it
                         student.team_id = None
                         student.save(update_fields=["team_id"])
                         return JsonResponse({"success": True, "message": f"Cleared invalid team_id for {email}"})
                 else:
                     student.team = None
                     student.save(update_fields=["team"])
                     return JsonResponse({"success": True, "message": f"Cleared team FK for {email}"})
        log_admin_action(request.session.get("admin_email"), "RESOLVE_INCONSISTENCY", "Student", email, {"type": inc_type, "resolution": resolution}, request)
        return JsonResponse({"success": True, "message": f"Successfully resolved {inc_type} for {email}"})

    except Exception as e:
        logger.error(f"Resolution error: {str(e)}")
        return JsonResponse({"success": False, "error": str(e)})


@csrf_exempt
@admin_required
def resend_evaluation_email_api(request):
    """
    POST /portal/api/resend-evaluation-email/
    Re-sends the evaluation result email for a specific evaluation.
    Body: { "evaluation_id": <int> }
    Resets email_sent=False and retriggers send, logging the action.
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST required"}, status=405)

    import json as _json
    try:
        data = _json.loads(request.body)
    except Exception:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    evaluation_id = data.get("evaluation_id")
    if not evaluation_id:
        return JsonResponse({"success": False, "error": "evaluation_id is required"}, status=400)

    try:
        evaluation = Evaluation.objects.select_related("team", "project").get(id=evaluation_id)
    except Evaluation.DoesNotExist:
        return JsonResponse({"success": False, "error": f"Evaluation {evaluation_id} not found"}, status=404)

    # Reset so send_evaluation_email will re-send
    evaluation.email_sent = False
    evaluation.save(update_fields=["email_sent"])

    from .services.email_service import send_evaluation_email
    success = send_evaluation_email(evaluation)

    if success:
        evaluation.email_sent = True
        evaluation.save(update_fields=["email_sent"])
        log_admin_action(
            request.session.get("admin_email"), "RESEND_EVALUATION_EMAIL",
            "Evaluation", str(evaluation_id),
            {"team_id": evaluation.team.team_id, "project_id": evaluation.project.project_id},
            request
        )
        logger.info(f"Admin {request.session.get('admin_email')} resent evaluation email for evaluation {evaluation_id}")
        invalidate_cache(pattern="evaluations:list:*")
        invalidate_cache(specific_key="dashboard:pipeline_stats")
        return JsonResponse({"success": True, "message": "Email resent successfully"})
    else:
        # Restore email_sent to its original state since send failed
        evaluation.email_sent = False
        evaluation.save(update_fields=["email_sent"])
        return JsonResponse({"success": False, "error": "Failed to send email — no recipients found or SMTP error. Check server logs."}, status=200)


# ──────────────────────────────────────────────────────────────────────────────
# Student Search Panel — Team & Enrollment Management APIs
# ──────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@admin_required
def delete_team_pref_api(request):
    """
    DELETE /portal/api/delete-team-pref/
    Delete a specific Team row by its internal pk (team_row_id).
    Used to clean up duplicate team preference rows from the student card.
    Body: { "team_row_id": <int> }
    """
    if request.method != "DELETE":
        return JsonResponse({"success": False, "error": "DELETE required"}, status=405)
    import json as _json
    try:
        data = _json.loads(request.body)
    except Exception:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    team_row_id = data.get("team_row_id")
    if not team_row_id:
        return JsonResponse({"success": False, "error": "team_row_id is required"}, status=400)

    deleted, _ = Team.objects.filter(pk=team_row_id).delete()
    if not deleted:
        return JsonResponse({"success": False, "error": "Team row not found"}, status=404)

    log_admin_action(request.session.get("admin_email"), "DELETE_TEAM_PREF", "Team", str(team_row_id), {}, request)
    invalidate_cache(pattern="students:lookup:*")
    invalidate_cache(pattern="students:table:*")
    return JsonResponse({"success": True, "message": f"Team row {team_row_id} deleted"})


@csrf_exempt
@admin_required
def delete_enrollment_api(request):
    """
    DELETE /portal/api/delete-enrollment/
    Remove a student's enrollment for a course.
    Body: { "email": "...", "course": "...", "force": false }
    If the student has submissions, requires force=true.
    Cleans up the associated Team row if this was the only member.
    """
    if request.method != "DELETE":
        return JsonResponse({"success": False, "error": "DELETE required"}, status=405)
    import json as _json
    try:
        data = _json.loads(request.body)
    except Exception:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    email = (data.get("email") or "").strip().lower()
    course = (data.get("course") or "").strip().upper()
    force = data.get("force", False)

    if not email or not course:
        return JsonResponse({"success": False, "error": "email and course are required"}, status=400)

    try:
        enrollment = Student.objects.select_related("team").get(identity__email=email, course=course)
    except Student.DoesNotExist:
        return JsonResponse({"success": False, "error": f"No enrollment for {email} in {course}"}, status=404)

    # Guard: block if student has submissions unless force=true
    if not force:
        from .models import Submission
        sub_count = Submission.objects.filter(student=enrollment).count()
        if sub_count:
            return JsonResponse({
                "success": False,
                "error": f"Student has {sub_count} submission(s) in {course}. Pass force=true to delete anyway.",
                "has_submissions": True,
                "submission_count": sub_count,
            }, status=409)

    # If the student has an assigned team, decide whether to clean up the Team row
    team_to_check = enrollment.team
    enrollment.delete()

    if team_to_check and team_to_check.team_id:
        other_members = Student.objects.filter(team=team_to_check).count()
        if other_members == 0:
            # No other students on this team — safe to delete the Team row
            team_to_check.delete()
            logger.info(f"Deleted lone team {team_to_check.team_id} after enrollment removal")

    log_admin_action(request.session.get("admin_email"), "DELETE_ENROLLMENT", "Student", email, {"course": course, "force": force}, request)
    invalidate_cache(specific_key="dashboard:pipeline_stats")
    invalidate_cache(pattern="students:lookup:*")
    invalidate_cache(pattern="students:table:*")
    return JsonResponse({"success": True, "message": f"Enrollment for {email} in {course} deleted"})


@csrf_exempt
@admin_required
def add_enrollment_api(request):
    """
    POST /portal/api/add-enrollment/
    Add a new course enrollment for an existing student.
    Body: { "email": "...", "course": "...", "status": "Registered" }
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST required"}, status=405)
    import json as _json
    try:
        data = _json.loads(request.body)
    except Exception:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    email = (data.get("email") or "").strip().lower()
    course = (data.get("course") or "").strip().upper()
    status = (data.get("status") or "Registered").strip()

    if not email or not course:
        return JsonResponse({"success": False, "error": "email and course are required"}, status=400)

    valid_statuses = [s[0] for s in Student.STATUS_CHOICES]
    if status not in valid_statuses:
        return JsonResponse({"success": False, "error": f"Invalid status. Valid: {valid_statuses}"}, status=400)

    try:
        identity = StudentIdentity.objects.get(email=email)
    except StudentIdentity.DoesNotExist:
        return JsonResponse({"success": False, "error": f"Student {email} not found"}, status=404)

    if Student.objects.filter(identity=identity, course=course).exists():
        return JsonResponse({"success": False, "error": f"{email} is already enrolled in {course}"}, status=409)

    enrollment = Student.objects.create(identity=identity, course=course, status=status, status_updated_at=timezone.now())
    log_admin_action(request.session.get("admin_email"), "ADD_ENROLLMENT", "Student", email, {"course": course, "status": status}, request)
    invalidate_cache(specific_key="dashboard:pipeline_stats")
    invalidate_cache(pattern="students:lookup:*")
    invalidate_cache(pattern="students:table:*")
    return JsonResponse({"success": True, "enrollment_id": enrollment.id, "course": course, "status": status})


@csrf_exempt
@admin_required
def unassigned_students_api(request):
    """
    GET /portal/api/unassigned-students/?course=CDS
    Returns students in a course who are Registered with no team assigned.
    Used for the group team formation picker.
    """
    if request.method != "GET":
        return JsonResponse({"success": False, "error": "GET required"}, status=405)

    course = request.GET.get("course", "").strip().upper()
    if not course:
        return JsonResponse({"success": False, "error": "course is required"}, status=400)

    _cache_key = f"students:unassigned:{course}"
    _cached = cache.get(_cache_key)
    if _cached:
        return JsonResponse(_cached)

    # Only return students who explicitly requested group assignment
    assign_team_emails = set(
        Team.objects.filter(
            course=course,
            team_preference="assign_team",
            team_id__isnull=True,
        ).values_list("student__email", flat=True)
    )

    unassigned = (
        Student.objects
        .filter(
            course=course,
            status="Registered",
            team__isnull=True,
            identity__email__in=assign_team_emails,
        )
        .select_related("identity")
        .order_by("identity__name")
    )

    results = [
        {
            "email": s.identity.email,
            "name": s.identity.name,
            "has_pending_pref": True,  # all returned students have assign_team preference
        }
        for s in unassigned
    ]
    payload = {"success": True, "count": len(results), "students": results}
    cache.set(_cache_key, payload, 30)
    return JsonResponse(payload)


@csrf_exempt
@admin_required
def admin_create_individual_team_api(request):
    """
    POST /portal/api/admin-create-individual-team/
    Immediately creates and assigns an individual team to a student.
    Body: { "email": "...", "course": "..." }
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST required"}, status=405)
    import json as _json
    try:
        data = _json.loads(request.body)
    except Exception:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    email = (data.get("email") or "").strip().lower()
    course = (data.get("course") or "").strip().upper()

    if not email or not course:
        return JsonResponse({"success": False, "error": "email and course are required"}, status=400)

    try:
        identity = StudentIdentity.objects.get(email=email)
        enrollment = Student.objects.get(identity=identity, course=course)
    except StudentIdentity.DoesNotExist:
        return JsonResponse({"success": False, "error": f"Student {email} not found"}, status=404)
    except Student.DoesNotExist:
        return JsonResponse({"success": False, "error": f"No enrollment for {email} in {course}"}, status=404)

    if enrollment.team:
        return JsonResponse({"success": False, "error": f"{email} already has team {enrollment.team.team_id}"}, status=409)

    from .services.team_id_service import generate_unified_team_id
    try:
        with suppress_es_signals(), transaction.atomic():
            # Clean up stale null-id preference rows for this student+course
            Team.objects.filter(student=identity, course=course, team_id__isnull=True).delete()

            new_team_id = generate_unified_team_id(course)

            team = Team.objects.create(
                student=identity,
                course=course,
                team_preference="individual",
                team_count=1,
                team_id=new_team_id,
                created_at=timezone.now(),
            )

            enrollment.team = team
            enrollment.status = "TeamIDGiven"
            enrollment.status_updated_at = timezone.now()
            enrollment.save()

    except Exception as e:
        logger.error(f"Failed to create individual team for {email}/{course}: {e}")
        return JsonResponse({"success": False, "error": str(e)}, status=500)

    log_admin_action(request.session.get("admin_email"), "CREATE_INDIVIDUAL_TEAM", "Team", new_team_id, {"email": email, "course": course}, request)
    # Bust caches affected by team creation
    invalidate_cache(specific_key="dashboard:pipeline_stats")
    invalidate_cache(specific_key=f"students:unassigned:{course}")
    invalidate_cache(pattern="students:table:*")
    return JsonResponse({"success": True, "team_id": new_team_id})


@csrf_exempt
@admin_required
def admin_form_group_team_api(request):
    """
    POST /portal/api/admin-form-group-team/
    Form a group team from 2-4 unassigned students in the same course.
    Body: { "emails": ["a@x.com", "b@x.com"], "course": "CDS" }
    Creates ONE Team row (leader = first email), sets all students' team FK.
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST required"}, status=405)
    import json as _json
    try:
        data = _json.loads(request.body)
    except Exception:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    emails = data.get("emails", [])
    course = (data.get("course") or "").strip().upper()

    if not isinstance(emails, list) or len(emails) < 2 or len(emails) > 4:
        return JsonResponse({"success": False, "error": "emails must be a list of 2-4 addresses"}, status=400)
    if not course:
        return JsonResponse({"success": False, "error": "course is required"}, status=400)

    from .services.team_id_service import generate_unified_team_id
    try:
        with suppress_es_signals(), transaction.atomic():
            identities = []
            enrollments = []
            for em in emails:
                try:
                    ident = StudentIdentity.objects.get(email=em.strip().lower())
                    enroll = Student.objects.get(identity=ident, course=course)
                except StudentIdentity.DoesNotExist:
                    return JsonResponse({"success": False, "error": f"Student {em} not found"}, status=404)
                except Student.DoesNotExist:
                    return JsonResponse({"success": False, "error": f"No enrollment for {em} in {course}"}, status=404)
                if enroll.team:
                    return JsonResponse({"success": False, "error": f"{em} already has team {enroll.team.team_id}"}, status=409)
                identities.append(ident)
                enrollments.append(enroll)

            new_team_id = generate_unified_team_id(course)

            # ONE Team row for the group leader (first email)
            leader_identity = identities[0]
            other_emails = [e for e in emails if e.strip().lower() != leader_identity.email]

            # Clean stale null rows for the leader
            Team.objects.filter(student=leader_identity, course=course, team_id__isnull=True).delete()

            team = Team.objects.create(
                student=leader_identity,
                course=course,
                team_preference=_json.dumps(other_emails),
                team_count=len(emails),
                team_id=new_team_id,
                created_at=timezone.now(),
            )

            # All members (including leader) point to this Team row
            for ident, enroll in zip(identities, enrollments):
                # Clean stale null rows for non-leaders too
                if ident.pk != leader_identity.pk:
                    Team.objects.filter(student=ident, course=course, team_id__isnull=True).delete()
                enroll.team = team
                enroll.status = "TeamIDGiven"
                enroll.status_updated_at = timezone.now()
                enroll.save()

    except Exception as e:
        logger.error(f"Failed to form group team for {emails}/{course}: {e}")
        return JsonResponse({"success": False, "error": str(e)}, status=500)

    log_admin_action(request.session.get("admin_email"), "FORM_GROUP_TEAM", "Team", new_team_id, {"emails": emails, "course": course}, request)
    # Bust caches affected by team creation
    invalidate_cache(specific_key="dashboard:pipeline_stats")
    invalidate_cache(specific_key=f"students:unassigned:{course}")
    invalidate_cache(pattern="students:table:*")
    return JsonResponse({"success": True, "team_id": new_team_id, "members": emails})


@csrf_exempt
@admin_required
def students_table_api(request):
    """
    GET /portal/api/students-table/?course=&status=&has_team=&q=&page=&page_size=
    Returns paginated list of students for dashboard table.
    """
    if request.method != 'GET':
        return JsonResponse({"error": "GET required"}, status=405)

    # Parse query parameters
    course = request.GET.get('course', '').strip().upper()
    status = request.GET.get('status', '').strip()
    has_team = request.GET.get('has_team', '').strip().lower()
    q = request.GET.get('q', '').strip().lower()
    page = int(request.GET.get('page', 1))
    page_size = int(request.GET.get('page_size', 50))

    if page < 1:
        page = 1
    if page_size < 1 or page_size > 100:
        page_size = 50

    # Cache filter-only requests (skip caching free-text searches — too user-specific)
    _cache_key = None
    if not q:
        _cache_key = f"students:table:{course}:{status}:{has_team}:p{page}:ps{page_size}"
        _cached = cache.get(_cache_key)
        if _cached:
            return JsonResponse(_cached)

    # Build queryset
    queryset = Student.objects.select_related('identity', 'team').order_by('-id')

    if course:
        queryset = queryset.filter(course=course)

    if status:
        queryset = queryset.filter(status=status)

    if has_team == 'yes':
        queryset = queryset.filter(team__isnull=False)
    elif has_team == 'no':
        queryset = queryset.filter(team__isnull=True)

    if q:
        queryset = queryset.filter(
            Q(identity__email__icontains=q) |
            Q(identity__name__icontains=q)
        )

    # Get total count for pagination
    total_count = queryset.count()
    start = (page - 1) * page_size
    end = start + page_size

    # Paginate
    students = queryset[start:end]

    # Build response
    results = []
    now = timezone.now()
    for student in students:
        days_in_status = None
        if student.status_updated_at:
            days_in_status = (now - student.status_updated_at).days

        results.append({
            'id': student.id,
            'name': student.identity.name,
            'email': student.identity.email,
            'course': student.course,
            'status': student.status,
            'team_id': student.team.team_id if student.team else None,
            'status_updated_at': student.status_updated_at.isoformat() if student.status_updated_at else None,
            'days_in_status': days_in_status,
        })

    payload = {
        'results': results,
        'total_count': total_count,
        'page': page,
        'page_size': page_size,
        'has_next': end < total_count,
        'has_prev': page > 1,
    }
    if _cache_key:
        cache.set(_cache_key, payload, 60)
    return JsonResponse(payload)


@csrf_exempt
@admin_required
def bulk_email_api(request):
    """
    POST /portal/api/bulk-email/
    Send templated email to audience.
    Body (JSON):
    {
        "target": {"status": "Registered", "course": "CDS"} or {"emails": ["a@b.com", "c@d.com"]},
        "subject": "Subject line",
        "message_template": "Hello {{name}}, your status is {{status}}..."
    }
    Returns: {sent: N, failed: [{email, error}]}
    """
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        import json as _json
        body = _json.loads(request.body)
    except Exception:
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    target = body.get("target", {})
    subject = body.get("subject", "").strip()
    message_template = body.get("message_template", "").strip()

    if not subject or not message_template:
        return JsonResponse({"error": "subject and message_template are required"}, status=400)

    # Get recipient list
    if "emails" in target:
        # Direct email list
        emails = target["emails"]
        if not isinstance(emails, list):
            return JsonResponse({"error": "target.emails must be a list"}, status=400)
        recipients_data = []
        for email in emails:
            try:
                identity = StudentIdentity.objects.get(email=email.strip().lower())
                enrollment = Student.objects.filter(identity=identity).first()
                if enrollment:
                    recipients_data.append({
                        'email': email,
                        'name': identity.name,
                        'course': enrollment.course,
                        'status': enrollment.status,
                        'team_id': enrollment.team.team_id if enrollment.team else None,
                    })
                else:
                    recipients_data.append({
                        'email': email,
                        'name': identity.name,
                        'course': '',
                        'status': '',
                        'team_id': None,
                    })
            except StudentIdentity.DoesNotExist:
                recipients_data.append({
                    'email': email,
                    'name': 'Student',
                    'course': '',
                    'status': '',
                    'team_id': None,
                })
    elif "team_id" in target:
        # Team-based targeting
        team_id = target["team_id"].strip()
        try:
            team = Team.objects.get(team_id=team_id)
            enrollments = Student.objects.filter(team=team).select_related('identity')
            recipients_data = []
            for enrollment in enrollments:
                recipients_data.append({
                    'email': enrollment.identity.email,
                    'name': enrollment.identity.name,
                    'course': enrollment.course,
                    'status': enrollment.status,
                    'team_id': team_id,
                })
        except Team.DoesNotExist:
            return JsonResponse({"error": f"Team '{team_id}' not found"}, status=404)
    else:
        # Query-based audience
        status = target.get("status")
        course = target.get("course")

        queryset = Student.objects.select_related('identity', 'team')
        if status:
            queryset = queryset.filter(status=status)
        if course:
            queryset = queryset.filter(course=course)

        recipients_data = []
        for student in queryset:
            recipients_data.append({
                'email': student.identity.email,
                'name': student.identity.name,
                'course': student.course,
                'status': student.status,
                'team_id': student.team.team_id if student.team else None,
            })

    if not recipients_data:
        return JsonResponse({"error": "No recipients found"}, status=400)

    # Send emails
    sent = 0
    failed = []

    from django.core.mail import send_mail
    from Submissions.services.email_service import _get_gmail_email, _get_test_mode, _get_test_recipient

    for recipient in recipients_data:
        try:
            # Substitute variables
            message = message_template
            message = message.replace("{{name}}", recipient['name'] or "")
            message = message.replace("{{email}}", recipient['email'] or "")
            message = message.replace("{{course}}", recipient['course'] or "")
            message = message.replace("{{status}}", recipient['status'] or "")
            message = message.replace("{{team_id}}", recipient['team_id'] or "")

            # Determine actual recipient
            if _get_test_mode():
                actual_recipient = _get_test_recipient()
                if not actual_recipient:
                    failed.append({"email": recipient['email'], "error": "Test mode enabled but no test recipient"})
                    continue
            else:
                actual_recipient = recipient['email']

            send_mail(
                subject=subject,
                message=message,
                from_email=_get_gmail_email(),
                recipient_list=[actual_recipient],
                fail_silently=False,
            )
            sent += 1

        except Exception as e:
            failed.append({"email": recipient['email'], "error": str(e)})

    log_admin_action(request.session.get("admin_email"), "BULK_EMAIL", "Email", f"{len(recipients_data)} recipients", 
                    {"subject": subject, "sent": sent, "failed": len(failed)}, request)

    return JsonResponse({
        "sent": sent,
        "failed": failed,
    })


@csrf_exempt
@admin_required
def send_adhoc_email_api(request):
    """
    POST /portal/api/send-adhoc-email/
    Send one-off email to single student.
    Body (JSON): {email, subject, message}
    """
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        import json as _json
        body = _json.loads(request.body)
    except Exception:
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    email = body.get("email", "").strip().lower()
    subject = body.get("subject", "").strip()
    message = body.get("message", "").strip()

    if not email or not subject or not message:
        return JsonResponse({"error": "email, subject, and message are required"}, status=400)

    try:
        from django.core.mail import send_mail
        from Submissions.services.email_service import _get_gmail_email, _get_test_mode, _get_test_recipient

        # Determine actual recipient
        if _get_test_mode():
            actual_recipient = _get_test_recipient()
            if not actual_recipient:
                return JsonResponse({"error": "Test mode enabled but no test recipient"}, status=400)
        else:
            actual_recipient = email

        send_mail(
            subject=subject,
            message=message,
            from_email=_get_gmail_email(),
            recipient_list=[actual_recipient],
            fail_silently=False,
        )

        log_admin_action(request.session.get("admin_email"), "ADHOC_EMAIL", "Email", email, {"subject": subject}, request)

        return JsonResponse({"success": True, "message": "Email sent"})

    except Exception as e:
        return JsonResponse({"error": f"Failed to send email: {str(e)}"}, status=500)
@csrf_exempt
@admin_required
def bulk_status_update_api(request):
    """
    POST /portal/api/bulk-status-update/
    Move batch of students from one status to another.
    Body: {from_status, to_status, course, dry_run: bool}
    """
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        data = json.loads(request.body)
        from_status = data.get("from_status")
        to_status = data.get("to_status")
        course = data.get("course")
        dry_run = data.get("dry_run", False)
    except Exception:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if not from_status or not to_status:
        return JsonResponse({"error": "from_status and to_status are required"}, status=400)

    valid_statuses = [s[0] for s in Student.STATUS_CHOICES]
    if from_status not in valid_statuses or to_status not in valid_statuses:
        return JsonResponse({"error": "Invalid status provided"}, status=400)

    # Filter students
    queryset = Student.objects.filter(status=from_status)
    if course and course != "All":
        queryset = queryset.filter(course=course)

    total_matching = queryset.count()
    if dry_run:
        return JsonResponse({"success": True, "dry_run": True, "matching_count": total_matching})

    # Execute update
    updated_count = 0
    skipped = []
    
    with transaction.atomic():
        for student in queryset:
            # Guard rails (enforced in backend)
            # Example: can't move to CapStoneProjectsAssigned without a team
            if to_status in ["CapStoneProjectsAssigned", "ReadyForClientPick", "ClientProjectAssigned", "AllCompleted"] and not student.team:
                skipped.append({"email": student.identity.email, "reason": "No team assigned"})
                continue
            
            student.status = to_status
            student.status_updated_at = timezone.now()
            student.save(update_fields=["status", "status_updated_at"])
            updated_count += 1

    log_admin_action(request.session.get("admin_email"), "BULK_STATUS_UPDATE", "Student", 
                    f"{from_status} -> {to_status}", {"course": course, "updated": updated_count, "skipped": len(skipped)}, request)

    return JsonResponse({
        "success": True,
        "updated": updated_count,
        "skipped": skipped
    })


@csrf_exempt
@admin_required
def bulk_noc_api(request):
    """
    POST /portal/api/bulk-noc/
    Issue NOC to all students in AllCompleted status for a course.
    Body: {course}
    """
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        data = json.loads(request.body)
        course = data.get("course")
    except Exception:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    queryset = Student.objects.filter(status="AllCompleted")
    if course and course != "All":
        queryset = queryset.filter(course=course)

    # Get identities that don't have NOC issued
    identities = StudentIdentity.objects.filter(
        email__in=queryset.values_list('identity_id', flat=True),
        NOC_issued=False
    )
    
    updated_count = identities.update(NOC_issued=True)

    log_admin_action(request.session.get("admin_email"), "BULK_NOC", "StudentIdentity", 
                    f"Course: {course}", {"updated": updated_count}, request)

    return JsonResponse({"success": True, "updated": updated_count})


@csrf_exempt
@admin_required
def bulk_password_reset_api(request):
    """
    POST /portal/api/bulk-password-reset/
    Reset passwords for a list of emails.
    Body: {emails: [], new_password: "..."}
    """
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        data = json.loads(request.body)
        emails = data.get("emails", [])
        new_password = data.get("new_password")
    except Exception:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if not emails:
        return JsonResponse({"error": "emails list is required"}, status=400)

    from django.contrib.auth.hashers import make_password
    import secrets
    import string

    results = []
    for email in emails:
        email = email.strip().lower()
        try:
            identity = StudentIdentity.objects.get(email=email)
            pw = new_password or ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10))
            identity.portal_password = make_password(pw)
            identity.save(update_fields=["portal_password"])
            results.append({"email": email, "success": True, "password": pw if not new_password else "******"})
        except StudentIdentity.DoesNotExist:
            results.append({"email": email, "success": False, "error": "Not found"})

    log_admin_action(request.session.get("admin_email"), "BULK_PASSWORD_RESET", "StudentIdentity", 
                    f"{len(emails)} students", {"results": len(results)}, request)

    return JsonResponse({"success": True, "results": results})


@csrf_exempt
@admin_required
def generate_temp_access_api(request):
    """
    POST /portal/api/generate-temp-access/
    Generate a single-use, 1-hour temporary password so an admin can log in
    as a student on the student portal without touching the student's real password.
    Body: {"email": "student@example.com"}
    Returns: {"success": true, "temp_password": "<plaintext shown once>", "expires_in_minutes": 60}
    """
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        data = json.loads(request.body)
        student_email = (data.get("email") or "").strip().lower()
    except Exception:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if not student_email:
        return JsonResponse({"error": "email is required"}, status=400)

    if not StudentIdentity.objects.filter(email=student_email).exists():
        return JsonResponse({"error": "Student not found"}, status=404)

    import secrets
    import string
    from django.contrib.auth.hashers import make_password as _make_password

    alphabet = string.ascii_letters + string.digits
    temp_password = ''.join(secrets.choice(alphabet) for _ in range(16))

    AdminTempAccess.objects.create(
        student_email=student_email,
        generated_by=request.session.get("admin_email"),
        temp_password_hash=_make_password(temp_password),
    )

    log_admin_action(
        request.session.get("admin_email"),
        "GENERATE_TEMP_ACCESS",
        "AdminTempAccess",
        student_email,
        {"student_email": student_email, "note": "Temp password generated (plaintext not logged)"},
        request,
    )

    return JsonResponse({
        "success": True,
        "temp_password": temp_password,
        "expires_in_minutes": 60,
        "student_email": student_email,
    })


@csrf_exempt
@admin_required
def export_students_api(request):
    """
    GET /portal/api/export-students/?course=&status=
    Returns CSV file of student data.
    """
    if request.method != 'GET':
        return JsonResponse({"error": "GET required"}, status=405)

    course = request.GET.get('course', '').strip().upper()
    status = request.GET.get('status', '').strip()

    queryset = Student.objects.select_related('identity', 'team').all()
    if course and course != "All":
        queryset = queryset.filter(course=course)
    if status and status != "All":
        queryset = queryset.filter(status=status)

    import csv
    from django.http import HttpResponse
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="students_export_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['Name', 'Email', 'Phone', 'Course', 'Status', 'Team ID', 'NOC Issued', 'Enrolled Date'])
    
    for s in queryset:
        writer.writerow([
            s.identity.name,
            s.identity.email,
            s.identity.phone,
            s.course,
            s.status,
            s.team.team_id if s.team else '',
            'Yes' if s.identity.NOC_issued else 'No',
            s.status_updated_at.strftime("%Y-%m-%d %H:%M:%S") if s.status_updated_at else ''
        ])
    
    return response

@csrf_exempt
@admin_required
def projects_api(request, project_id=None):
    """
    GET /portal/api/projects/?course=&type=
    POST /portal/api/projects/
    PATCH /portal/api/projects/<project_id>/
    DELETE /portal/api/projects/<project_id>/
    """
    if request.method == 'GET':
        course = request.GET.get('course', '').strip().upper()
        p_type = request.GET.get('type', '').strip()
        
        queryset = ProjectRegistry.objects.all()
        if course and course != "All":
            queryset = queryset.filter(course=course)
        if p_type:
            queryset = queryset.filter(project_type=p_type)
            
        results = []
        for p in queryset:
            results.append({
                "project_id": p.project_id,
                "project_name": p.project_name,
                "course": p.course,
                "project_type": p.project_type,
                "llm_input_prompt": p.llm_input_prompt,
                "project_document": str(p.project_document) if p.project_document else None,
                "expected_submission": p.expected_submission,
            })
        return JsonResponse({"success": True, "results": results})

    elif request.method == 'POST':
        try:
            data = json.loads(request.body)
        except Exception:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
            
        pid = data.get("project_id")
        name = data.get("project_name")
        course = data.get("course")
        p_type = data.get("project_type")
        
        if not pid or not name or not course or not p_type:
            return JsonResponse({"error": "project_id, project_name, course, and project_type are required"}, status=400)
            
        project = ProjectRegistry.objects.create(
            project_id=pid,
            project_name=name,
            course=course,
            project_type=p_type,
            llm_input_prompt=data.get("llm_input_prompt", ""),
            expected_submission=data.get("expected_submission", {})
        )
        log_admin_action(request.session.get("admin_email"), "CREATE_PROJECT", "Project", pid, {}, request)
        return JsonResponse({"success": True, "project_id": pid})

    elif request.method == 'PATCH':
        if not project_id:
            return JsonResponse({"error": "project_id in URL required"}, status=400)
        try:
            project = ProjectRegistry.objects.get(project_id=project_id)
            data = json.loads(request.body)
            if "project_name" in data: project.project_name = data["project_name"]
            if "course" in data: project.course = data["course"]
            if "project_type" in data: project.project_type = data["project_type"]
            if "llm_input_prompt" in data: project.llm_input_prompt = data["llm_input_prompt"]
            if "expected_submission" in data: project.expected_submission = data["expected_submission"]
            project.save()
            log_admin_action(request.session.get("admin_email"), "UPDATE_PROJECT", "Project", project_id, {}, request)
            return JsonResponse({"success": True})
        except ProjectRegistry.DoesNotExist:
            return JsonResponse({"error": "Project not found"}, status=404)

    elif request.method == 'DELETE':
        if not project_id:
            return JsonResponse({"error": "project_id in URL required"}, status=400)
        try:
            project = ProjectRegistry.objects.get(project_id=project_id)
            # Guard: block if TeamProject rows exist
            if TeamProject.objects.filter(project=project).exists():
                return JsonResponse({"error": "Cannot delete project: existing team assignments exist"}, status=409)
            project.delete()
            log_admin_action(request.session.get("admin_email"), "DELETE_PROJECT", "Project", project_id, {}, request)
            return JsonResponse({"success": True})
        except ProjectRegistry.DoesNotExist:
            return JsonResponse({"error": "Project not found"}, status=404)

    return JsonResponse({"error": "Method not allowed"}, status=405)


@csrf_exempt
@admin_required
def bulk_assign_project_api(request):
    """
    POST /portal/api/bulk-assign-project/
    Assign project to all teams in a course.
    Body: {project_id, course}
    """
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        data = json.loads(request.body)
        pid = data.get("project_id")
        course = data.get("course")
    except Exception:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if not pid or not course:
        return JsonResponse({"error": "project_id and course are required"}, status=400)

    try:
        project = ProjectRegistry.objects.get(project_id=pid)
        teams = Team.objects.filter(course=course, team_id__isnull=False).distinct('team_id')
        
        count = 0
        for team in teams:
            tp, created = TeamProject.objects.get_or_create(
                team=team,
                project=project,
                defaults={'project_type': project.project_type, 'status': 'assigned'}
            )
            if created:
                count += 1
                
        log_admin_action(request.session.get("admin_email"), "BULK_ASSIGN_PROJECT", "TeamProject", 
                        f"Project: {pid}, Course: {course}", {"assigned": count}, request)
        return JsonResponse({"success": True, "assigned_count": count})
    except ProjectRegistry.DoesNotExist:
        return JsonResponse({"error": "Project not found"}, status=404)


@csrf_exempt
@admin_required
def evaluations_list_api(request):
    """
    GET /portal/api/evaluations/?course=&reviewed=&email_sent=&grade=
    """
    if request.method != 'GET':
        return JsonResponse({"error": "GET required"}, status=405)

    course = request.GET.get('course', '').strip().upper()
    reviewed = request.GET.get('reviewed', '')
    email_sent = request.GET.get('email_sent', '')
    grade = request.GET.get('grade', '')

    _cache_key = f"evaluations:list:{course}:{reviewed}:{email_sent}:{grade}"
    _cached = cache.get(_cache_key)
    if _cached:
        return JsonResponse(_cached)

    queryset = Evaluation.objects.select_related('team', 'project').all()
    if course and course != "All":
        queryset = queryset.filter(course=course)
    if reviewed:
        queryset = queryset.filter(reviewed=(reviewed.lower() == 'true'))
    if email_sent:
        queryset = queryset.filter(email_sent=(email_sent.lower() == 'true'))
    if grade and grade != "All":
        queryset = queryset.filter(evaluation_grade=grade)

    results = []
    for ev in queryset:
        results.append({
            "evaluation_id": ev.id,
            "team_id": ev.team.team_id,
            "project_id": ev.project.project_id,
            "course": ev.course,
            "grade": ev.evaluation_grade,
            "visibility_after": ev.visibility_after.isoformat() if ev.visibility_after else None,
            "reviewed": ev.reviewed,
            "email_sent": ev.email_sent,
        })
    payload = {"success": True, "results": results}
    cache.set(_cache_key, payload, 300)
    return JsonResponse(payload)


@csrf_exempt
@admin_required
def evaluation_update_api(request, evaluation_id):
    """
    PATCH /portal/api/evaluations/<id>/
    Update reviewed status, visibility date, and grade.
    """
    if request.method != 'PATCH':
        return JsonResponse({"error": "PATCH required"}, status=405)

    try:
        ev = Evaluation.objects.get(pk=evaluation_id)
        data = json.loads(request.body)
        if "reviewed" in data: 
            ev.reviewed = bool(data["reviewed"])
            if ev.reviewed:
                ev.reviewed_at = timezone.now()
        if "visibility_after" in data:
            ev.visibility_after = data["visibility_after"]
        if "grade" in data:
            ev.evaluation_grade = data["grade"]
        ev.save()
        return JsonResponse({"success": True})
    except Evaluation.DoesNotExist:
        return JsonResponse({"error": "Evaluation not found"}, status=404)

@csrf_exempt
@admin_required
def trigger_single_student_email_api(request):
    """
    POST /portal/api/trigger-single-student-email/
    Triggers a professional email template for one student.
    Expected payload: {"email": "...", "template": "...", "evaluation_id": <optional>}
    """
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)
    
    try:
        data = json.loads(request.body)
        email = data.get("email", "").strip().lower()
        template_name = data.get("template")
        evaluation_id = data.get("evaluation_id")
        
        from django.core.mail import send_mail, EmailMultiAlternatives
        from Submissions.services.email_service import (_get_gmail_email, _get_test_mode, _get_test_recipient,
                                                        send_evaluation_email)
        from Submissions.models import Student, Evaluation

        # Determine actual recipient
        if _get_test_mode():
            actual_recipient = _get_test_recipient()
        else:
            actual_recipient = email

        if not actual_recipient:
            return JsonResponse({"success": False, "error": "No recipient found"}, status=400)

        # Case 1: Specific Evaluation Report
        if evaluation_id or template_name == "Evaluation Ready":
            try:
                if evaluation_id:
                    ev = Evaluation.objects.select_related("team", "project").get(pk=evaluation_id)
                else:
                    # Find most recent evaluation for this student
                    ev = Evaluation.objects.filter(team__student__identity__email=email).select_related("team", "project").order_by("-id").first()
                
                if ev:
                    # use the existing service which is robust
                    success = send_evaluation_email(ev)
                    if success:
                        return JsonResponse({"success": True, "message": f"Evaluation report sent for {ev.project.project_id}"})
                    else:
                        return JsonResponse({"success": False, "error": "Failed to send evaluation report"}, status=500)
                else:
                    return JsonResponse({"success": False, "error": "No evaluation found for this student"}, status=404)
            except Evaluation.DoesNotExist:
                return JsonResponse({"success": False, "error": "Evaluation not found"}, status=404)

        # Case 2: Other Templates (Status Update, NOC Clearance, etc.)
        # Fetch student context
        student = Student.objects.filter(identity__email=email).select_related('identity', 'team').first()
        name = student.identity.name if student else "Student"
        team_id = student.team.team_id if student and student.team else "N/A"
        course = student.course if student else "N/A"

        subject = f"Portal Update: {template_name} - {team_id}"
        if template_name == "Status Update":
            message = f"Dear {name},\n\nYour enrollment status for {course} has been updated to: {student.status}.\n\nTeam ID: {team_id}\n\nBest regards,\nLMT"
        elif template_name == "NOC Clearance":
            message = f"Dear {name},\n\nYour NOC (No Objection Certificate) has been cleared and issued.\n\nTeam ID: {team_id}\n\nBest regards,\nLMT"
        else:
            message = f"Dear {name},\n\nThis is a notification regarding: {template_name}.\n\nTeam ID: {team_id}\n\nBest regards,\nLMT"

        send_mail(
            subject=subject,
            message=message,
            from_email=_get_gmail_email(),
            recipient_list=[actual_recipient],
            fail_silently=False,
        )
        
        log_admin_action(request.session.get("admin_email"), "SINGLE_TRIGGER_EMAIL", "Email", email, {"template": template_name}, request)
        return JsonResponse({"success": True, "message": f"Email triggered: {template_name}"})

    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@csrf_exempt
@admin_required
def evaluation_delete_api(request, evaluation_id):
    """
    DELETE /portal/api/evaluations/<id>/
    Deletes evaluation + resets TeamProject to assigned.
    """
    if request.method != 'DELETE':
        return JsonResponse({"error": "DELETE required"}, status=405)

    try:
        with transaction.atomic():
            ev = Evaluation.objects.select_related('team', 'project').get(pk=evaluation_id)
            # Reset TeamProject status
            TeamProject.objects.filter(team=ev.team, project=ev.project).update(status='assigned')
            ev.delete()
        return JsonResponse({"success": True})
    except Evaluation.DoesNotExist:
        return JsonResponse({"error": "Evaluation not found"}, status=404)


@csrf_exempt
@admin_required
def bulk_mark_reviewed_api(request):
    """
    POST /portal/api/bulk-mark-reviewed/
    Mark multiple evaluations as reviewed.
    Body: {evaluation_ids: [1, 2, ...]}
    """
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        data = json.loads(request.body)
        ids = data.get("evaluation_ids", [])
    except Exception:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    updated = Evaluation.objects.filter(pk__in=ids).update(reviewed=True, reviewed_at=timezone.now())
    return JsonResponse({"success": True, "updated_count": updated})


@csrf_exempt
@admin_required
def trigger_pending_emails_api(request):
    """
    POST /portal/api/trigger-pending-emails/
    Calls process_visible_evaluations() manually and updates the cron cache
    so the Cron Jobs tab reflects the latest run even when triggered on demand.
    """
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)

    from .services.email_service import process_visible_evaluations
    from .scheduler import _cache_job_result

    result = process_visible_evaluations()

    # Build the same summary format used by the scheduled job
    summary = f"Sent: {result['sent']}  Failed: {result['failed']}"
    if result.get("failures"):
        summary += "\nFailures:\n" + "\n".join(
            f"  ID={f['id']} {f['team']} {f['project']}: {f['reason']}"
            for f in result["failures"]
        )
    status = "success" if result["failed"] == 0 else ("error" if result["sent"] == 0 else "partial")
    _cache_job_result("cron:eval_emails:last_run", status, summary, error=result.get("error"))

    return JsonResponse({
        "success": True,
        "emails_sent": result["sent"],
        "failed": result["failed"],
        "failures": result.get("failures", []),
        "error": result.get("error"),
    })


@csrf_exempt
@admin_required
def cron_status_api(request):
    """
    GET /portal/api/cron-status/
    Returns the latest run status and summary for each scheduled job.
    Combines DjangoJobExecution records (timestamp, duration, exception)
    with cache-stored summaries written by each job function.
    """
    if request.method != 'GET':
        return JsonResponse({"error": "GET required"}, status=405)

    from django.core.cache import cache

    JOBS = [
        {
            "job_id": "send_pending_evaluation_emails",
            "label": "Evaluation Email Sender",
            "schedule": "Every 12 hours",
            "cache_key": "cron:eval_emails:last_run",
        },
    ]

    # Pre-fetch all DjangoJobExecution records in one query
    exec_by_job = {}
    try:
        from django_apscheduler.models import DjangoJobExecution
        for row in (
            DjangoJobExecution.objects
            .filter(job__id__in=[j["job_id"] for j in JOBS])
            .order_by("-run_time")
            .values("job__id", "status", "run_time", "duration", "exception")
        ):
            jid = row["job__id"]
            if jid not in exec_by_job:   # keep only the latest per job
                exec_by_job[jid] = row
    except Exception:
        pass  # DjangoJobStore not yet active; fall back to cache only

    jobs_out = []
    for job in JOBS:
        cached = cache.get(job["cache_key"]) or {}
        ex = exec_by_job.get(job["job_id"], {})

        run_time = ex.get("run_time")
        jobs_out.append({
            "job_id":   job["job_id"],
            "label":    job["label"],
            "schedule": job["schedule"],
            "last_run": run_time.isoformat() if run_time else cached.get("ran_at"),
            "status":   ex.get("status") or cached.get("status"),
            "duration": str(ex["duration"]) if ex.get("duration") else None,
            "exception": ex.get("exception") or cached.get("error"),
            "summary":  cached.get("summary"),
        })

    return JsonResponse({"jobs": jobs_out})

