# Project Reference: What Does What

## Apps & Modules

### `Submissions/` — Core Application
The main Django app handling all student-facing and evaluation logic.

| File | Purpose |
|------|---------|
| `models.py` | All database models (17 models) |
| `views.py` | Student-facing pages (register, login, submit, view evaluations) |
| `admin_views.py` | Admin portal API endpoints (dashboard, student management, bulk ops) |
| `urls.py` | URL routing for student + admin portal |
| `signals.py` | Django signals — syncs `Submission.evaluated` when `Evaluation` is saved |
| `scheduler.py` | APScheduler job definitions (email, sync, import) |
| `admin.py` | Django admin site registration |
| `decorators.py` | Auth decorators for views |
| `documents.py` | Placeholder (Elasticsearch removed, search uses SQL LIKE) |
| `health.py` | Health check endpoints (for K8s probes) |
| `media_views.py` | Media file serving (downloads) |

### `Submissions/services/` — Business Logic Layer

| File | Purpose |
|------|---------|
| `email_service.py` | Sends evaluation emails via Gmail SMTP. Handles throttling, team member lookup, capstone completion notifications |
| `validation.py` | `SubmissionValidator` — validates uploaded files (extension check, ZIP contents, required files per schema) |
| `error_formatter.py` | Converts validation error codes into user-friendly HTML messages |
| `cache_service.py` | Redis cache wrapper with pattern-based invalidation |
| `team_service.py` | Team formation logic |
| `team_id_service.py` | Team ID generation (format: `PTID-COURSE-MONTH-YEAR-NNNNN`) |

### `Submissions/services/Parsers/` — LLM Evaluation Pipeline

| File | Purpose |
|------|---------|
| `payload_assembler.py` | **Orchestrator** — fetches project question/prompt from DB, calls parser, assembles payload, sends to LLM |
| `llm_service.py` | LLM API calls — Gemini (primary) with automatic fallback to Groq. Handles key rotation & rate limits |
| `image_ocr.py` | Image OCR via Groq (for screenshots in submissions) |
| `ipynb_parser.py` | Parses Jupyter notebooks — extracts code cells, markdown, outputs |
| `parser_orchestrator.py` | Routes submission file to correct parser based on type |
| `document_text.py` | Extracts text from .docx/.pdf files |
| `plain_text.py` | Handles .txt/.py/.sql files |
| `tabular_text.py` | Handles .csv/.xlsx files |
| `ppt_parser.py` | Handles .pptx files |
| `mixed_evidence.py` | Handles ZIP files with mixed content (docs + images + code) |
| `config.py` | Parser configuration |
| `registry.py` | Parser registry (maps file types to parsers) |
| `base.py` | Base parser class |

### `Submissions/management/commands/` — CLI Commands

| Command | Purpose |
|---------|---------|
| `send_pending_evaluation_emails` | Processes visible, reviewed evaluations and sends emails |
| `import_students_from_sheets` | Imports student data from Google Sheets |
| `assign_team_ids` | Assigns team IDs to pending teams |
| `sync_evaluation_status` | Syncs evaluated flag between TeamProject ↔ Submission |
| `sync_media_to_s3` | Uploads local media files to S3 |
| `rebuild_es_index` | No-op (Elasticsearch removed, kept for compatibility) |

### `ControlCenter/` — Admin Authentication

| File | Purpose |
|------|---------|
| `views.py` | Admin login/logout/status API + manage admins |
| `models.py` | Admin user model |
| `urls.py` | Routes under `/portal/api/control-center/` |

### `InternshipPortal/` — Django Project Settings

| File | Purpose |
|------|---------|
| `settings.py` | All config — DB, S3, Redis, Elasticsearch, logging, auth |
| `.env` | Environment variables (credentials, API keys) |
| `urls.py` | Root URL config — includes Submissions + ControlCenter |
| `wsgi.py` / `asgi.py` | WSGI/ASGI entry points |

---

## Database Models (17 total)

| Model | Table | Purpose |
|-------|-------|---------|
| `Course` | `submissions_course` | Course definitions (CDS, CDA, CDE, AIE) |
| `CourseAlias` | `submissions_coursealias` | Alternative course name mappings |
| `StudentIdentity` | `submissions_studentidentity` | Name + email (unique identity) |
| `Team` | `submissions_team` | Team records with team_id, email, course |
| `Student` | `submissions_student` | Enrolled student (links Identity → Team, has status) |
| `ProjectRegistry` | `submissions_project_registry` | All projects (Capstone + Client) with eval prompts |
| `TeamProject` | `submissions_team_project` | Assignment of project to team (status: assigned/submitted/evaluated) |
| `Submission` | `submissions_submission` | Individual submission event |
| `SubmissionFile` | `submissions_submission_file` | Uploaded files per submission |
| `Evaluation` | `submissions_evaluation` | LLM evaluation result per team+project |
| `PasswordResetOTP` | — | OTP for password reset |
| `AdminTempAccess` | — | Temporary admin access tokens |
| `S3ToLocalMoveLog` | — | Tracks files moved from S3 to local |
| `LocalFileDeletionLog` | — | Tracks local file deletions |
| `CapstoneClientCompletionLog` | — | Logs when team completes all capstone projects |
| `RegistrationWhitelist` | — | Whitelist for student registration |
| `CSVUploadLog` | — | Audit log for CSV bulk imports |

---

## URL Routes

### Student Pages (`/`)

| URL | View | Purpose |
|-----|------|---------|
| `/` | `home` | Login page (email entry) |
| `/register/` | `register` | New student registration |
| `/create-password/` | `create_password` | Set password |
| `/verify-password/` | `verify_password` | Login (password verify) |
| `/register-for-course/` | `register_for_course` | Select course |
| `/team-preference/` | `team_preference` | Enter team preference emails |
| `/team-waiting/` | `team_waiting` | Waiting for team assignment |
| `/team-acceptance/` | `handle_team_acceptance` | Accept/reject team invite |
| `/pick-projects/` | `pick_projects` | Choose client projects |
| `/submissions/` | `submissions` | Upload submission files |
| `/evaluations/` | `evaluations` | View evaluation results |
| `/forgot-password/` | `forgot_password` | Password reset flow |

### Admin Portal (`/portal/`)

| URL | View | Purpose |
|-----|------|---------|
| `/portal/` | `admin_login` | Admin login |
| `/portal/dashboard/` | `admin_dashboard` | Overview stats |
| `/portal/students/` | `admin_students` | Student management |
| `/portal/submissions/` | `admin_submissions` | View all submissions |
| `/portal/projects/` | `admin_projects` | Project management |
| `/portal/api/...` | Various | REST APIs for admin operations |

### Admin APIs (selected)

| Endpoint | Purpose |
|----------|---------|
| `portal/api/pipeline-stats/` | Dashboard counts (students, teams, submissions, evals) |
| `portal/api/upload-students-csv/` | Bulk import students from CSV |
| `portal/api/bulk-assign-project/` | Assign projects to teams in bulk |
| `portal/api/evaluations/` | List/update/delete evaluations |
| `portal/api/resend-evaluation-email/` | Re-send an evaluation email |
| `portal/api/team-formation-execute/` | Auto-form teams from preferences |
| `portal/api/bulk-email/` | Send custom emails to students |

---

## Key Statuses & Transitions

### Student Status Flow
```
Registered → PasswordCreated → CourseSelected → TeamPreferenceSubmitted
→ TeamIDGiven → CapStoneProjectsAssigned → ClientProjectAssigned
```

### TeamProject Status Flow
```
assigned → submitted → evaluated
```

### Evaluation Flow
```
Created (on submission) → reviewed=False → Trainer marks reviewed=True
→ visibility_after passes → email_sent=True → TeamProject status='evaluated'
```

---

## Scheduled Jobs

| Job | Trigger | What it does |
|-----|---------|--------------|
| **send_pending_evaluation_emails** | Every 12h + cron 00:00/12:00 | Finds `reviewed=True, email_sent=False, visibility_after <= now`, sends email, sets `email_sent=True`, updates TeamProject to `evaluated` |
| **sync_ready_for_client_pick** | Every 6h | Checks if students completed all capstone projects, transitions them to `ReadyForClientPick` status |
| **import_students_from_sheets** | Daily 02:00 UTC | Pulls new student registrations from Google Sheets |
