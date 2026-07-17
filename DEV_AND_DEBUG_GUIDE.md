# Internship Portal — Developer & Debugging Guide

## Server Access

| Item | Value |
|------|-------|
| **Server IP** | `15.206.74.51` |
| **User** | `ubuntu` |
| **PEM File** | `project_evaluation_head.pem` (in Downloads) |
| **SSH Command** | `ssh -i "project_evaluation_head.pem" ubuntu@15.206.74.51` |
| **Application Path** | `/home/ubuntu/InternshipPortal/` |
| **Python/Venv** | `/DarkSpace/venv/bin/python3` |
| **Django Settings** | `InternshipPortal.settings` |
| **Running Port** | `8001` |
| **Domain** | `internship.rubixeprojects.com` |

---

## Database (MySQL RDS)

| Item | Value |
|------|-------|
| **Host** | `database-1.c386s6kwe2mp.ap-south-1.rds.amazonaws.com` |
| **Port** | `3306` |
| **Database** | `internship_portal` |
| **User** | `admin` |
| **Password** | In `.env` file on server |
| **Engine** | MySQL 8.x |

---

## S3 Storage

| Item | Value |
|------|-------|
| **Bucket** | `internship7portal` |
| **Region** | `ap-south-1` |
| **Media Prefix** | `media/` |
| **Submissions** | `media/submissions/<TEAMID>/<PROJECTID>/<timestamp>_<filename>` |
| **Evaluations** | `media/Evaluations/<TEAMID>/<PROJECTID>/<timestamp>_evaluation.txt` |
| **Project Docs** | `media/project_docs/2026/01/17/<ProjectID>.(txt|rtf)` |
| **AWS Credentials** | In `.env` file (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) |

---

## Project Structure (on server)

```
/home/ubuntu/InternshipPortal/
├── manage.py
├── require.txt
├── InternshipPortal/
│   ├── settings.py          # Django settings (loads .env)
│   ├── .env                 # Credentials (DB, S3, Gmail, LLM API keys)
│   ├── urls.py
│   └── wsgi.py
├── Submissions/
│   ├── models.py            # Team, Student, TeamProject, Submission, Evaluation, etc.
│   ├── views.py             # Student-facing views (login, submit, pick-projects)
│   ├── admin_views.py       # Admin API endpoints
│   ├── signals.py           # post_save on Evaluation → sync Submission.evaluated
│   ├── scheduler.py         # APScheduler jobs (email, sync)
│   ├── urls.py
│   ├── services/
│   │   ├── email_service.py       # Gmail SMTP email sending
│   │   ├── validation.py          # ZIP/IPYNB submission validator
│   │   ├── error_formatter.py     # User-friendly validation error messages
│   │   ├── cache_service.py       # Redis cache layer
│   │   ├── team_service.py        # Team management
│   │   └── Parsers/
│   │       ├── payload_assembler.py   # Orchestrates LLM evaluation
│   │       ├── llm_service.py        # Gemini + Groq LLM calls
│   │       ├── image_ocr.py          # Groq-based image OCR
│   │       ├── ipynb_parser.py        # Jupyter notebook parser
│   │       ├── parser_orchestrator.py # Routes to correct parser
│   │       └── ...
│   ├── management/commands/
│   │   ├── send_pending_evaluation_emails.py
│   │   ├── import_students_from_sheets.py
│   │   ├── assign_team_ids.py
│   │   ├── sync_evaluation_status.py
│   │   └── sync_media_to_s3.py
│   └── templates/
└── ControlCenter/               # Admin dashboard app
```

---

## Running Processes

| Process | Command | Purpose |
|---------|---------|---------|
| Django App | `/DarkSpace/venv/bin/python3 manage.py runserver 8001` | Main web server |
| Trainer Review | `streamlit run trainer_review_app.py --server.port 8002` | Trainer eval review UI |
| APScheduler | Embedded in Django (RUN_SCHEDULER=True) | Periodic jobs |
| Cron | `0 0,12 * * *` | Email sending at midnight & noon UTC |

---

## Cron Jobs & Scheduled Tasks

### System Crontab
```
0 0,12 * * * cd /DarkSpace/InternshipPortal && python3 manage.py send_pending_evaluation_emails
```

### APScheduler Jobs (in-process)
| Job | Interval | Purpose |
|-----|----------|---------|
| `send_pending_evaluation_emails` | Every 12 hours | Send evaluation emails when visibility_after passes |
| `sync_ready_for_client_pick` | Every 6 hours | Transition students to client project phase |

> **Note:** `import_students_from_sheets` is a management command only — it is NOT registered as an APScheduler job.

---

## Key Flows

### Submission → Evaluation Flow
1. Student uploads file on `/submissions/` page
2. `views.py` validates the file (extension, ZIP contents)
3. `_evaluate_submission_onspot_with_files()` calls `assemble_and_evaluate()`
4. LLM cascade: Gemini Key1 → Gemini Key2 → Groq fallback
5. If LLM succeeds → `Submission` + `Evaluation` records created
6. If CDS course → auto-reviewed; else waits for trainer
7. Trainer reviews via `trainer_review_app.py` (Streamlit on port 8002)
8. After review: `visibility_after` = now + 2 days
9. Cron sends email when `reviewed=True AND email_sent=False AND visibility_after <= now`

### Email Sending
- **Service:** `Submissions/services/email_service.py` → `process_visible_evaluations()`
- **SMTP:** Gmail (`internship@datamites.com`) with App Password
- **Throttle:** 1.5s between emails to avoid Gmail rate limits
- **Known issue:** Server reads eval report from LOCAL disk, not S3. Run `sync_evals_from_s3.py` if files are missing.

---

## Common Debugging Commands

### SSH to Server
```bash
ssh -i "project_evaluation_head.pem" ubuntu@15.206.74.51
```

### Run Django Management Commands
```bash
cd /home/ubuntu/InternshipPortal
/DarkSpace/venv/bin/python3 manage.py send_pending_evaluation_emails
/DarkSpace/venv/bin/python3 manage.py sync_evaluation_status
/DarkSpace/venv/bin/python3 manage.py import_students_from_sheets
```

### Check Server Logs
```bash
tail -100 /home/ubuntu/nohup.out                    # Django request log
tail -50 /DarkSpace/InternshipPortal/logs/cron_email.log  # Cron email log
```

### Check Running Processes
```bash
ps aux | grep 'manage.py runserver\|streamlit' | grep -v grep
```

### Restart Django Server
```bash
# Find PID
ps aux | grep 'manage.py runserver 8001' | grep -v grep
# Kill old
kill <PID>
# Start new
cd /home/ubuntu/InternshipPortal
nohup /DarkSpace/venv/bin/python3 manage.py runserver 8001 > /home/ubuntu/nohup.out 2>&1 &
```

### Quick Email Status Check
```bash
cd /home/ubuntu/InternshipPortal
/DarkSpace/venv/bin/python3 quick_check.py
```

### Sync Evaluation Files from S3 to Local
```bash
/DarkSpace/venv/bin/python3 sync_evals_from_s3.py
```

### Trigger Email Send Manually
```bash
/DarkSpace/venv/bin/python3 send_emails.py
```

---

## Known Issues & Fixes

### 1. Evaluation report file missing from local disk
**Symptom:** Email send fails with "Could not read evaluation report file"
**Cause:** Server reads from local `/home/ubuntu/InternshipPortal/media/Evaluations/...` but file is only on S3
**Fix:** Run `sync_evals_from_s3.py` to download from S3 to local

### 2. Gmail "Username and Password not accepted" (Error 535)
**Symptom:** All emails fail with authentication error
**Cause:** Gmail App Password expired or revoked
**Fix:** Generate new App Password from Google Account → Security → App Passwords, then update `.env`:
```bash
sed -i 's/GMAIL_PASSWORD=.*/GMAIL_PASSWORD=<new_password>/' /home/ubuntu/InternshipPortal/InternshipPortal/.env
```
Restart the server afterward.

### 3. ZIP upload rejected with "This project requires: ZIP files"
**Symptom:** Students can't upload ZIP for multi-track projects (e.g., PRCL-01 Cleveland Clinic)
**Cause:** Track schema was passed without `submission_type` and `allowed_extensions`
**Fix:** Applied in `views.py` — track schema now inherits these from parent schema

### 4. Students with "no team members" — emails fail
**Symptom:** `send_evaluation_email` returns False, log says "No team members found"
**Cause:** Students were reassigned to new teams (with -A, -S, -E suffixes)
**Fix:** Send directly to `team.email` field using `send_direct_emails.py`

### 5. Old evaluation files permanently lost (March 2026)
**Symptom:** 8 evaluations can never be emailed
**Cause:** Files were stored on local disk before S3 was configured, server was restarted
**Affected:** PTID-CDS-DEC-25-3598, PTID-CDS-MAR-26-11128, PTID-CDS-MAR-26-11116, PTID-CDS-MAR-26-11122, PTID-CDS-FEB-26-11111
**Resolution:** Students need to resubmit. No recovery possible (S3 versioning was never enabled).

---

## LLM Configuration

| Provider | Models | API Keys |
|----------|--------|----------|
| **Gemini** | gemini-2.5-flash-preview-09-2025, gemini-2.5-flash-lite | `GEMINI_API_KEY1`, `GEMINI_API_KEY2` |
| **Groq** (fallback) | meta-llama/llama-4-scout-17b-16e-instruct | `GROQ_API_KEY1`, `GROQ_API_KEY2` |

**Cascade:** Gemini Key1 Model1 → Model2 → Key2 Model1 → Model2 → Groq fallback

---

## Other Paths on Server

| Path | Purpose |
|------|---------|
| `/DarkSpace/InternshipPortal/` | Old deployment (cron still references this) |
| `/DarkSpace/venv/` | Python virtual environment |
| `/DarkSpace/project_submissions/project_folder/` | Legacy submission files (flat by team) |
| `/DarkSpace/media/submissions/` | Older submission storage |
| `/DarkSpace/media/Evaluations/` | Older evaluation storage |
| `/DarkSpace/HippoCampus/` | Venv for trainer_review_app Streamlit |

---

## Environment Variables (.env)

Key variables in `/home/ubuntu/InternshipPortal/InternshipPortal/.env`:
```
DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT
USE_S3=True
AWS_STORAGE_BUCKET_NAME, AWS_S3_REGION_NAME, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
GROQ_API_KEY1, GROQ_API_KEY2
GEMINI_API_KEY1, GEMINI_API_KEY2
RUN_SCHEDULER=True
GMAIL_EMAIL, GMAIL_PASSWORD
EMAIL_TEST_MODE=False
```
