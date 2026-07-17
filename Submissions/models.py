import os
import json

from django.db import models
from django.utils import timezone


class Course(models.Model):
    """
    Course registry: list of all available courses.
    """
    id = models.BigAutoField(primary_key=True)
    code = models.CharField(max_length=10, unique=True, db_column="code")
    name = models.CharField(max_length=255, db_column="name")

    class Meta:
        db_table = "submissions_course"

    def __str__(self):
        return f"{self.code} - {self.name}"


class CourseAlias(models.Model):
    """
    Course alias mapping: Normalize multiple course codes to one canonical course.
    
    Example:
    - CourseAlias(alias="AI", course=AIE_object)
    - CourseAlias(alias="AEN", course=AIE_object)
    - CourseAlias(alias="AIE", course=AIE_object)
    
    This allows team IDs (AI), whitelist (AEN), and database (AIE) to all work together.
    """
    alias = models.CharField(max_length=10, unique=True, db_column="alias")
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        db_column="course_code",
        related_name="aliases",
        to_field="code",
    )

    class Meta:
        db_table = "submissions_course_alias"
        indexes = [
            models.Index(fields=["alias"]),
        ]

    def __str__(self):
        return f"{self.alias} → {self.course.code}"


class StudentIdentity(models.Model):
    """
    One row per email (master student record).
    Exists because Student enrollment is per (email + course).
    """
    email = models.EmailField(primary_key=True, max_length=255)
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=30, blank=True, null=True)
    NOC_issued = models.BooleanField(default=False, null=True, blank=True)
    portal_password = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = "submissions_student_identity"

    def __str__(self):
        return f"{self.name} ({self.email})"


class Team(models.Model):
    """
    Team preference and assignment table.
    
    Initial creation: Student submits team preference (Individual/I have team/Assign team)
    with optional teammate emails (max 3 for 4-person team).
    Team ID is nullable initially, assigned by management command when conditions are met.
    
    Fields:
    - id: Auto-increment primary key (internal reference)
    - team_id: Nullable unique team identifier (PTID-COURSE-MONTH-YEAR-5DIGIT), assigned later
    - student: FK to StudentIdentity (the student who submitted preference)
    - team_preference: Choice field (individual/assign_team)  # DISABLED: i_have_team - NOT IN FUNCTION
    - preference_emails: JSON array of teammate emails (DISABLED - NOT IN FUNCTION)
    - team_count: Team size (1-4)
    - course: Course code for validation
    - created_at: When team_id was assigned
    - email_sent: Flag for team formation email
    - preference_submitted_at: Auto timestamp when preference was submitted
    """
    PREFERENCE_CHOICES = [
        ("individual", "Individual"),
        # DISABLED FOR NOW: i_have_team - NOT IN FUNCTION
        # ("i_have_team", "I have a team"),
        ("assign_team", "Assign a team"),
    ]

    id = models.BigAutoField(primary_key=True)

    # FK to StudentIdentity - the student who submitted the preference
    student = models.ForeignKey(
        StudentIdentity,
        on_delete=models.CASCADE,
        db_column="email",
        related_name="team_preferences",
    )

    # Team ID - nullable, assigned by management command (format: PTID-COURSE-MONTH-YEAR-11111)
    team_id = models.CharField(
        max_length=50,
        db_column="team_id",
        null=True,
        blank=True,
        unique=True,
    )

    # Team preference choice — max_length=500 to accommodate JSON arrays of
    # up to 3 teammate emails stored by admin_form_group_team_api
    team_preference = models.CharField(
        max_length=500,
        choices=PREFERENCE_CHOICES,
        db_column="team_preference",
        default="individual",
    )

    # Teammate emails (JSON array) - max 3 emails for 4-person team
    preference_emails = models.JSONField(
        db_column="preference_emails",
        null=True,
        blank=True,
        default=list,
        help_text="List of teammate emails (max 3)"
    )

    # Team size (1, 2, 3, or 4)
    team_count = models.PositiveIntegerField(db_column="team_count", default=1)

    # Course code (for team assignment validation)
    course = models.CharField(max_length=120, db_column="course", default="")

    # Timestamps and flags
    created_at = models.DateTimeField(
        db_column="created_at",
        null=True,
        blank=True,
        help_text="Timestamp when team_id was assigned"
    )

    email_sent = models.BooleanField(
        default=False,
        db_column="email_sent",
        help_text="Whether team formation email was sent"
    )

    # When student submitted their preference
    preference_submitted_at = models.DateTimeField(
        auto_now_add=True,
        db_column="preference_submitted_at",
        null=True,
        blank=True,
    )

    # True only when student submitted preference through the portal themselves.
    # Admin-created/imported team rows stay False and are excluded from auto team formation.
    student_submitted = models.BooleanField(
        default=False,
        db_column="student_submitted",
        help_text="Set True when student submits preference via the student portal"
    )

    class Meta:
        db_table = "submissions_team"
        indexes = [
            models.Index(fields=["course"]),
            models.Index(fields=["team_id"]),
            models.Index(fields=["student", "course"]),
        ]

    def __str__(self):
        return f"{self.team_id or 'Pending'} - {self.student.email} ({self.team_preference})"


class Student(models.Model):
    """
    Student enrollment table: unique per (email + course).
    """
    STATUS_CHOICES = [
        ("Registered", "Registered"),
        ("TeamIDGiven", "TeamIDGiven"),
        ("CapStoneProjectsAssigned", "CapStoneProjectsAssigned"),
        ("ReadyForClientPick", "ReadyForClientPick"),
        ("ClientProjectAssigned", "ClientProjectAssigned"),
        ("AllCompleted", "AllCompleted"),
        # AIE two-phase statuses (after completing full CDS cycle)
        ("CDSCycleComplete", "CDSCycleComplete"),
        ("AIECapstoneAssigned", "AIECapstoneAssigned"),
        ("AIEReadyForClientPick", "AIEReadyForClientPick"),
        ("AIEClientAssigned", "AIEClientAssigned"),
        # Legacy students imported from old portal — blocked from new portal access
        ("LegacyArchived", "LegacyArchived"),
    ]

    id = models.BigAutoField(primary_key=True)

    identity = models.ForeignKey(
        StudentIdentity,
        on_delete=models.CASCADE,
        db_column="email",
        related_name="enrollments",
    )

    course = models.CharField(max_length=120, db_column="course")

    status = models.CharField(
        max_length=40,
        choices=STATUS_CHOICES,
        db_column="status",
        default="Registered",
    )

    # optional team mapping stored in Student table; points to Team.team_id
    team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column="TeamID",
        related_name="enrolled_students",
        to_field="team_id",
    )

    # Timestamp when student status was last updated (for tracking completion time)
    status_updated_at = models.DateTimeField(
        db_column="status_updated_at",
        null=True,
        blank=True,
        help_text="Timestamp when student status was last updated"
    )

    class Meta:
        db_table = "submissions_student"
        constraints = [
            models.UniqueConstraint(fields=["identity", "course"], name="uniq_student_email_course"),
        ]
        indexes = [
            models.Index(fields=["course"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.identity.email} - {self.course}"


class ProjectRegistry(models.Model):
    """
    Project Registry: list of all projects and descriptions.
    Future extension point.
    """
    PROJECT_TYPE_CHOICES = [
        ("Capstone", "Capstone"),
        ("Client", "Client"),
    ]

    project_id = models.CharField(max_length=50, primary_key=True, db_column="ProjectID")

    course = models.CharField(max_length=120, db_column="Course")
    project_name = models.CharField(max_length=255, db_column="ProjectName")
    project_type = models.CharField(max_length=20, choices=PROJECT_TYPE_CHOICES, db_column="ProjectType")

    llm_input_prompt = models.TextField(db_column="LLMInputPrompt", null=True, blank=True)

    # Project documentation (PDF, DOCX, etc.)
    project_document = models.FileField(
        upload_to="project_docs/%Y/%m/%d/",
        null=True,
        blank=True,
        db_column="ProjectDocument",
        help_text="Project specification, guidelines, or instructions (PDF, DOCX, etc.)"
    )

    # This is your "submission schema"
    expected_submission = models.JSONField(
        db_column="ExpectedSubmissionJSON",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "submissions_project_registry"
        indexes = [
            models.Index(fields=["course"]),
            models.Index(fields=["project_type"]),
        ]

    def __str__(self):
        return f"{self.course} | {self.project_id} - {self.project_name}"

    @property
    def submission_schema_json(self) -> str:
        """
        Returns ExpectedSubmissionJSON as a JSON string safe for HTML data-* attributes.
        """
        return json.dumps(self.expected_submission or {})


class TeamProject(models.Model):
    """
    Projects Assignment table (Team + Project + Status).
    This is the SOURCE OF TRUTH for:
      - pending projects (status=assigned)
      - submitted projects (status=submitted)
      - evaluated projects (status=evaluated)
    """
    STATUS_CHOICES = [
        ("assigned", "assigned"),
        ("submitted", "submitted"),
        ("evaluated", "evaluated"),
    ]

    id = models.BigAutoField(primary_key=True)

    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        db_column="TeamID",
        related_name="team_projects",
        to_field="team_id",
    )

    project = models.ForeignKey(
        ProjectRegistry,
        on_delete=models.CASCADE,
        db_column="ProjectID",
        related_name="team_projects",
    )

    # Optional denormalized copy; you can also leave null and always read from project.project_type
    project_type = models.CharField(
        max_length=20,
        choices=ProjectRegistry.PROJECT_TYPE_CHOICES,
        db_column="ProjectType",
        null=True,
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="assigned",
        db_column="status",
    )

    assigned_at = models.DateTimeField(auto_now_add=True, db_column="assigned_at")
    updated_at = models.DateTimeField(auto_now=True, db_column="updated_at")

    class Meta:
        db_table = "submissions_team_project"
        constraints = [
            models.UniqueConstraint(fields=["team", "project"], name="uniq_team_project"),
        ]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["assigned_at"]),
        ]

    def __str__(self):
        return f"{self.team.team_id} | {self.project.project_id} | {self.status}"


class Submission(models.Model):
    """
    Submission event table.
    IMPORTANT: team_project is nullable TEMPORARILY to allow migrations
    if your DB already has older Submission rows.
    """
    id = models.BigAutoField(primary_key=True)

    team_project = models.ForeignKey(
        TeamProject,
        on_delete=models.CASCADE,
        db_column="team_project_id",
        related_name="submissions",
        null=True,      # ✅ TEMP for migration safety
        blank=True,     # ✅ TEMP for migration safety
    )

    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        db_column="student_enrollment_id",
        related_name="submissions",
    )

    submission_link = models.URLField(db_column="submission_link", max_length=1000, blank=True, null=True)
    evaluated = models.BooleanField(db_column="evaluated", default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_column="created_at")

    class Meta:
        db_table = "submissions_submission"
        indexes = [
            models.Index(fields=["evaluated"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        if self.team_project_id:
            tp = self.team_project
            return f"{tp.team.team_id} | {tp.project.project_id} | {self.student.identity.email}"
        return f"(no team_project) | {self.student.identity.email}"


def submission_upload_path(instance, filename: str) -> str:
    """
    Store files under:
      submissions/<TEAMID>/<PROJECTID>/<timestamp>_<original_filename>

    Uses Submission -> TeamProject -> (Team, Project)
    """
    ts = timezone.now().strftime("%Y%m%d_%H%M%S")
    safe = filename.replace(" ", "_")

    # If somehow team_project is null (older rows), store in a fallback folder
    if not instance.submission.team_project_id:
        return os.path.join("submissions", "unknown_team", "unknown_project", f"{ts}_{safe}")

    team_id = instance.submission.team_project.team.team_id
    project_id = instance.submission.team_project.project.project_id

    return os.path.join("submissions", team_id, project_id, f"{ts}_{safe}")


class SubmissionFile(models.Model):
    """
    Multiple uploaded files per submission.
    """
    id = models.BigAutoField(primary_key=True)

    submission = models.ForeignKey(
        Submission,
        on_delete=models.CASCADE,
        related_name="files",
    )

    file = models.FileField(upload_to=submission_upload_path)
    original_name = models.CharField(max_length=300, blank=True, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "submissions_submission_file"
        indexes = [models.Index(fields=["uploaded_at"])]

    def __str__(self):
        return self.original_name or self.file.name


class Evaluation(models.Model):
    """
    Evaluation table: unique per (TeamID, ProjectID).
    """
    id = models.BigAutoField(primary_key=True)

    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        db_column="TeamID",
        related_name="evaluations",
        to_field="team_id",
    )

    course = models.CharField(max_length=120, db_column="course")

    project = models.ForeignKey(
        ProjectRegistry,
        on_delete=models.CASCADE,
        db_column="ProjectID",
        related_name="evaluations",
    )

    evaluation_report = models.TextField(db_column="evaluation_report", blank=True, null=True)
    evaluation_grade = models.CharField(max_length=20, db_column="evaluation_grade", blank=True, null=True)
    visibility_after = models.DateTimeField(db_column="visibility_after", blank=True, null=True)
    email_sent = models.BooleanField(default=False, db_column="email_sent")
    reviewed = models.BooleanField(default=False, db_column="reviewed")
    reviewed_at = models.DateTimeField(db_column="reviewed_at", blank=True, null=True)
    sheets_submitted = models.BooleanField(
        default=False,
        db_column="sheets_submitted",
        help_text="True when the evaluation response was sent via Google Forms (not the portal mailer)"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_column="created_at")
    updated_at = models.DateTimeField(auto_now=True, db_column="updated_at")

    class Meta:
        db_table = "submissions_evaluation"
        constraints = [
            models.UniqueConstraint(fields=["team", "project"], name="uniq_evaluation_team_project"),
        ]
        indexes = [
            models.Index(fields=["course"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["visibility_after"]),
            models.Index(fields=["visibility_after", "email_sent"]),
        ]

    def __str__(self):
        return f"{self.team.team_id} - {self.project.project_id}"


# Legacy AdminUser removed in favor of ControlCenter.AdminAccount


# Legacy AdminUser fully deprecated.


class PasswordResetOTP(models.Model):
    """
    Store OTPs for password reset/change functionality.
    OTP expires after 10 minutes.
    Supports up to 3 resend attempts with rate limiting (2 minutes between resends).
    """
    email = models.EmailField(max_length=255)
    otp_code = models.CharField(max_length=256)  # SHA-256 hashed OTP
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)
    resend_count = models.PositiveIntegerField(default=0)  # Track resend attempts
    last_resent_at = models.DateTimeField(null=True, blank=True)  # Track last resend time

    class Meta:
        db_table = "submissions_password_reset_otp"
        indexes = [
            models.Index(fields=["email", "is_used"]),
        ]

    def is_expired(self):
        """Check if OTP has expired (10 minutes)"""
        return (timezone.now() - self.created_at).total_seconds() > 600

    def can_resend(self):
        """Check if user can request another resend"""
        if self.resend_count >= 3:
            return False, "Maximum resend attempts reached. Please start over."
        
        if self.last_resent_at:
            seconds_since_last = (timezone.now() - self.last_resent_at).total_seconds()
            if seconds_since_last < 120:  # 2 minutes
                wait_time = int(120 - seconds_since_last)
                return False, f"Please wait {wait_time} seconds before requesting another OTP."
        
        return True, None

    def __str__(self):
        return f"OTP for {self.email} - {'Used' if self.is_used else 'Pending'} (Resends: {self.resend_count}/3)"


class AdminTempAccess(models.Model):
    """
    Single-use, 1-hour temporary password generated by an admin to log in
    as a student on the student portal. The student's real portal_password
    is never modified.
    """
    student_email      = models.EmailField(max_length=255)
    generated_by       = models.EmailField(max_length=255)  # admin email
    temp_password_hash = models.CharField(max_length=255)   # Django hashed; plaintext never stored
    created_at         = models.DateTimeField(auto_now_add=True)
    is_used            = models.BooleanField(default=False)
    used_at            = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "submissions_admin_temp_access"
        indexes = [
            models.Index(fields=["student_email", "is_used"]),
        ]

    def is_expired(self):
        return (timezone.now() - self.created_at).total_seconds() > 3600

    def __str__(self):
        status = "used" if self.is_used else ("expired" if self.is_expired() else "active")
        return f"TempAccess for {self.student_email} by {self.generated_by} [{status}]"


class S3ToLocalMoveLog(models.Model):
    """
    Log table for DAG 1: Track all S3 to local server file movements.
    
    Used to:
    - Track which S3 files have been moved to local storage
    - Enable DAG 2 to identify files for archival deletion (after 2 months)
    - Audit trail for compliance
    
    Fields:
    - submission_id: FK to Submission being moved
    - team_id: Team identifier (from Submission → TeamProject → Team)
    - project_id: Project identifier (from Submission → TeamProject → Project)
    - original_s3_path: Path where file was stored in S3
    - local_path: Destination path on local server
    - timestamp: When the move operation occurred
    - status: Operation result (success/failure)
    - error_message: Details if operation failed
    - file_size_bytes: Size of file moved (for auditing)
    - attempt_count: Number of retry attempts made
    """
    STATUS_CHOICES = [
        ("success", "Success"),
        ("failure", "Failure"),
        ("partial", "Partial"),
    ]

    id = models.BigAutoField(primary_key=True)

    # Foreign key to Submission
    submission = models.ForeignKey(
        Submission,
        on_delete=models.CASCADE,
        db_column="submission_id",
        related_name="s3_move_logs",
        null=True,
        blank=True,
    )

    # Denormalized fields for faster queries (no join needed)
    team_id = models.CharField(max_length=50, db_column="team_id", null=True, blank=True)
    project_id = models.CharField(max_length=50, db_column="project_id", null=True, blank=True)

    # File path information
    original_s3_path = models.CharField(
        max_length=500,
        db_column="original_s3_path",
        help_text="Original path in S3 bucket"
    )

    local_path = models.CharField(
        max_length=1000,
        db_column="local_path",
        help_text="Destination path on local server"
    )

    # Status and metadata
    timestamp = models.DateTimeField(
        auto_now_add=True,
        db_column="timestamp",
        help_text="When file was moved"
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        db_column="status",
        default="pending",
    )

    error_message = models.TextField(
        db_column="error_message",
        null=True,
        blank=True,
        help_text="Error details if move failed"
    )

    file_size_bytes = models.BigIntegerField(
        db_column="file_size_bytes",
        null=True,
        blank=True,
        help_text="Size of file in bytes"
    )

    attempt_count = models.PositiveIntegerField(
        db_column="attempt_count",
        default=1,
        help_text="Number of retry attempts made"
    )

    class Meta:
        db_table = "submissions_s3_to_local_move_log"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["timestamp"]),
            models.Index(fields=["team_id"]),
            models.Index(fields=["project_id"]),
            models.Index(fields=["status", "timestamp"]),  # For DAG 2 queries
        ]

    def __str__(self):
        return f"{self.team_id} | {self.project_id} | {self.status} | {self.timestamp}"


class LocalFileDeletionLog(models.Model):
    """
    Log table for DAG 2: Track deletions of archived files from local server.
    
    Used to:
    - Audit trail of local file deletions
    - Track which files have been archived (deleted from local storage)
    - Optional: Enable restore operations if needed
    
    Fields:
    - move_log_id: FK to S3ToLocalMoveLog (the file that was deleted)
    - local_path: The path that was deleted
    - deleted_at: When deletion occurred
    - status: Operation result (success/failure)
    - error_message: Details if deletion failed
    """
    STATUS_CHOICES = [
        ("success", "Deleted Successfully"),
        ("failure", "Deletion Failed"),
        ("not_found", "File Not Found"),
    ]

    id = models.BigAutoField(primary_key=True)

    # Reference to the original move log
    move_log = models.ForeignKey(
        S3ToLocalMoveLog,
        on_delete=models.CASCADE,
        db_column="move_log_id",
        related_name="deletion_logs",
    )

    # Denormalized path for auditing
    local_path = models.CharField(
        max_length=1000,
        db_column="local_path",
    )

    # Deletion metadata
    deleted_at = models.DateTimeField(
        auto_now_add=True,
        db_column="deleted_at",
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        db_column="status",
        default="success",
    )

    error_message = models.TextField(
        db_column="error_message",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "submissions_local_file_deletion_log"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["deleted_at"]),
        ]

    def __str__(self):
        return f"Deleted: {self.local_path} | {self.status} | {self.deleted_at}"


class CapstoneClientCompletionLog(models.Model):
    """
    Log table for DAG 3: Track CSV generation for completed Capstone/Client projects.
    
    Used to:
    - Track CSV exports of completed students
    - Prevent duplicate entries in CSV
    - Track completion records by student (for append-only logic)
    
    Fields:
    - student_id: FK to Student record
    - csv_s3_key: S3 path where CSV is stored
    - generated_at: When this record was added to CSV
    - records_appended: How many new records were added in this batch
    - last_row_number: Row number in CSV (for tracking)
    - status: Generation status (success/failure)
    """
    STATUS_CHOICES = [
        ("success", "Success"),
        ("failure", "Failure"),
        ("duplicate", "Duplicate - Already Exported"),
    ]

    id = models.BigAutoField(primary_key=True)

    # Reference to Student
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        db_column="student_id",
        related_name="csv_completion_logs",
        null=True,
        blank=True,
    )

    # Denormalized fields for faster queries
    team_id = models.CharField(
        max_length=50,
        db_column="team_id",
        null=True,
        blank=True,
    )

    student_email = models.EmailField(
        max_length=255,
        db_column="student_email",
        null=True,
        blank=True,
    )

    student_name = models.CharField(
        max_length=255,
        db_column="student_name",
        null=True,
        blank=True,
    )

    # CSV metadata
    csv_s3_key = models.CharField(
        max_length=500,
        db_column="csv_s3_key",
        default="reports/completed-projects.csv",
        help_text="S3 path where CSV is stored",
    )

    # Tracking information
    generated_at = models.DateTimeField(
        auto_now_add=True,
        db_column="generated_at",
        help_text="When record was added to CSV",
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        db_column="status",
        default="success",
    )

    # Batch tracking
    batch_id = models.CharField(
        max_length=100,
        db_column="batch_id",
        null=True,
        blank=True,
        help_text="CSV generation batch identifier (for grouping)",
    )

    error_message = models.TextField(
        db_column="error_message",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "submissions_capstone_client_completion_log"
        constraints = [
            # Prevent duplicate student entries in the same batch
            models.UniqueConstraint(
                fields=["student", "csv_s3_key"],
                name="uniq_student_csv_entry"
            ),
        ]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["generated_at"]),
            models.Index(fields=["student"]),
            models.Index(fields=["team_id"]),
        ]

    def __str__(self):
        return f"{self.student_email} | {self.team_id} | {self.status} | {self.generated_at}"


class RegistrationWhitelist(models.Model):
    """
    Registration whitelist: Approved emails for course registration.
    
    Used to control who can register for which courses.
    Synced from Google Sheets via Apps Script.
    
    Course codes are automatically normalized via normalize_course_code() to handle aliases.
    """
    email = models.EmailField(max_length=255, db_column="email")
    course = models.CharField(max_length=120, db_column="course")
    added_at = models.DateTimeField(
        auto_now_add=True,
        db_column="added_at",
        help_text="Timestamp when entry was added"
    )

    class Meta:
        db_table = "submissions_registration_whitelist"
        constraints = [
            models.UniqueConstraint(
                fields=["email", "course"],
                name="uniq_whitelist_email_course"
            ),
        ]
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["course"]),
        ]

    def __str__(self):
        return f"{self.email} | {self.course}"

    def save(self, *args, **kwargs):
        """Auto-normalize course code when saving."""
        self.course = normalize_course_code(self.course)
        super().save(*args, **kwargs)


# ============================================================================
# UTILITY FUNCTIONS FOR COURSE CODE NORMALIZATION
# ============================================================================

def normalize_course_code(course_code: str) -> str:
    """
    Normalize course codes using CourseAlias mapping.
    
    Handles aliases:
    - AI → AIE (team ID format)
    - AEN → AIE (whitelist format from Apps Script)
    - AIE → AIE (canonical form)
    
    Args:
        course_code: Any course code (may be an alias)
    
    Returns:
        Canonical course code (e.g., AIE)
    
    Example:
        >>> normalize_course_code("AI")
        "AIE"
        >>> normalize_course_code("AEN")
        "AIE"
        >>> normalize_course_code("AIE")
        "AIE"
    """
    if not course_code:
        return course_code
    
    course_code = course_code.strip().upper()
    
    try:
        alias_obj = CourseAlias.objects.filter(alias__iexact=course_code).first()
        if alias_obj:
            return alias_obj.course.code
    except Exception:
        # If DB lookup fails, return as-is (for migrations, tests, etc.)
        pass
    
    return course_code
class CSVUploadLog(models.Model):
    """
    Tracks bulk database mutations made during the CSV Upload & Sync flow.
    Enables precise 'Undo' functionality by keeping track of created/modified rows.
    """
    timestamp = models.DateTimeField(auto_now_add=True)
    admin_email = models.EmailField(null=True, blank=True)
    
    # Stores a list of Student.id integers that were freshly created
    created_students = models.JSONField(default=list, blank=True)
    
    # Stores a list of StudentIdentity.email strings that were freshly created
    created_identities = models.JSONField(default=list, blank=True)
    
    # Stores mapping of StudentIdentity.email to a dictionary of previous state (name change)
    # e.g. { "user@example.com": {"previous_name": "Old Name"} }
    modified_identities = models.JSONField(default=dict, blank=True)

    # Stores mapping of Student.id to a dictionary of previous state
    # e.g. { "1005": {"previous_course": "AIE", "previous_status": "TeamIDGiven", "previous_team_id": "PTID-..."} }
    modified_students = models.JSONField(default=dict, blank=True)
    
    class Meta:
        db_table = "submissions_csv_upload_log"
        ordering = ["-timestamp"]
        verbose_name = "CSV Upload Log"
        verbose_name_plural = "CSV Upload Logs"
        
    def __str__(self):
        added = len(self.created_students)
        mod_s = len(self.modified_students)
        mod_i = len(self.modified_identities)
        return f"Upload at {self.timestamp} - {added} added, {mod_s + mod_i} modified"


