# Server Architecture & Service Map

## Server Details

| Item | Value |
|------|-------|
| **IP Address** | `15.206.74.51` |
| **Provider** | AWS EC2 (ap-south-1) |
| **OS** | Ubuntu |
| **User** | `ubuntu` |
| **SSH** | `ssh -i "project_evaluation_head.pem" ubuntu@15.206.74.51` |
| **PEM File** | `project_evaluation_head.pem` |

---

## Services Running on Server

### 1. Django Application (Internship Portal)

| Item | Value |
|------|-------|
| **Port** | `8001` |
| **Project Path** | `/home/ubuntu/InternshipPortal/` |
| **Python** | `/DarkSpace/venv/bin/python3` |
| **Command** | `manage.py runserver 8001` |
| **Domain** | `internship.rubixeprojects.com` |
| **Purpose** | Main student portal — registration, login, submission, evaluation |

**What it does:**
- Student registration & login (email/password)
- Team formation & project assignment
- File upload & validation (ZIP/IPYNB)
- LLM-based automatic evaluation (Gemini/Groq)
- Evaluation email dispatch
- Admin dashboard & student management

---

### 2. Trainer Review App (Streamlit)

| Item | Value |
|------|-------|
| **Port** | `8002` |
| **Project Path** | `/DarkSpace/InternshipPortal/trainer_review_app.py` |
| **Python/Venv** | `/DarkSpace/HippoCampus/bin/python3` |
| **Command** | `streamlit run trainer_review_app.py --server.port 8002` |
| **Purpose** | Trainer interface to review & approve evaluations |

**What it does:**
- Lists pending evaluations (reviewed=False)
- Trainers can edit evaluation text and grade
- Click "Mark as Reviewed" to approve
- Triggers Django signal → syncs Submission.evaluated

---

### 3. Project Evaluation App (Streamlit)

| Item | Value |
|------|-------|
| **Port** | `8501` |
| **Project Path** | `/DarkSpace/project_submissions/prj1.py` |
| **Python/Venv** | `/DarkSpace/project_submissions/env/bin/python` |
| **Command** | `streamlit run prj1.py --server.port 8501` |
| **Purpose** | Legacy project evaluation/management interface |

---

### 4. Status Dashboard (Streamlit)

| Item | Value |
|------|-------|
| **Port** | `8503` |
| **Project Path** | `/DarkSpace/project_submissions/status.py` |
| **Python/Venv** | `/DarkSpace/project_submissions/env/bin/python` |
| **Command** | `streamlit run status.py --server.port 8503` |
| **Purpose** | Status monitoring dashboard |

---

### 5. App (Streamlit)

| Item | Value |
|------|-------|
| **Port** | Default (8501 range) |
| **Project Path** | `/home/ubuntu/app.py` |
| **Python/Venv** | `/DarkSpace/venv/bin/python3` |
| **Command** | `streamlit run app.py` |
| **Purpose** | Additional Streamlit app |

---

### 6. Redis

| Item | Value |
|------|-------|
| **Port** | `6379` |
| **Purpose** | Caching layer for Django (sessions, query cache) |
| **Running as** | System service (user: dnsmasq) |

---

---

## Cron Jobs

```
0 0,12 * * *  cd /home/ubuntu/InternshipPortal && python3 manage.py send_pending_evaluation_emails
```

Runs at **midnight (00:00 UTC)** and **noon (12:00 UTC)** daily.
Log output: `/home/ubuntu/InternshipPortal/logs/cron_email.log`

---

## Directory Map

```
/home/ubuntu/
├── InternshipPortal/            ← PRODUCTION: Live Django app (port 8001)
│   ├── manage.py
│   ├── InternshipPortal/
│   │   ├── settings.py
│   │   ├── .env                 ← Credentials (DB, S3, Gmail, LLM keys)
│   │   └── urls.py
│   ├── Submissions/             ← Main app (models, views, services)
│   ├── ControlCenter/           ← Admin app
│   └── media/                   ← Local media cache (eval reports)
├── app.py                       ← Streamlit app
├── nohup.out                    ← Django server log
└── deleted_submissions_backup/

/DarkSpace/
├── InternshipPortal/            ← OLDER DEPLOYMENT (cron still references this)
│   ├── trainer_review_app.py    ← Trainer review Streamlit (port 8002)
│   ├── manage.py
│   └── logs/
│       └── cron_email.log       ← Cron email output log
├── project_submissions/         ← Legacy evaluation tools
│   ├── prj1.py                  ← Streamlit (port 8501)
│   ├── status.py                ← Streamlit (port 8503)
│   ├── app.py                   ← Streamlit app
│   ├── project_folder/          ← Old flat submission files by team
│   └── env/                     ← Python venv for Streamlit apps
├── venv/                        ← Main Python venv (Django + Streamlit)
├── HippoCampus/                 ← Venv for trainer_review_app
├── media/
│   ├── submissions/             ← Older submission files
│   └── Evaluations/             ← Older evaluation reports
├── backup/                      ← Old backup
└── logs/                        ← System logs
```

---

## Port Summary

| Port | Service | App |
|------|---------|-----|
| `8001` | Django | Internship Portal (main) |
| `8002` | Streamlit | Trainer Review App |
| `8501` | Streamlit | Project Evaluation (prj1.py) |
| `8503` | Streamlit | Status Dashboard |
| `6379` | Redis | Cache |
| `3306` | MySQL (RDS) | Database (external: `database-1.c386s6kwe2mp.ap-south-1.rds.amazonaws.com`) |

---

## External Services

| Service | Endpoint | Purpose |
|---------|----------|---------|
| **MySQL RDS** | `database-1.c386s6kwe2mp.ap-south-1.rds.amazonaws.com:3306` | Primary database |
| **S3** | `internship7portal.s3.ap-south-1.amazonaws.com` | File storage (submissions, evaluations, project docs) |
| **Gmail SMTP** | `smtp.gmail.com:587` | Email sending (`internship@datamites.com`) |
| **Gemini API** | `generativelanguage.googleapis.com` | LLM evaluation (primary) |
| **Groq API** | `api.groq.com` | LLM evaluation (fallback) |
| **CloudFront** | `d3ilbtxij3aepc.cloudfront.net` | Dataset file downloads for students |

---

## How to Start/Stop Services

### Django (Port 8001)
```bash
# Find PID
ps aux | grep 'manage.py runserver 8001' | grep -v grep

# Stop
kill <PID>

# Start
cd /home/ubuntu/InternshipPortal
nohup /DarkSpace/venv/bin/python3 manage.py runserver 8001 > /home/ubuntu/nohup.out 2>&1 &
```

### Trainer Review (Port 8002)
```bash
# Find PID
ps aux | grep 'trainer_review_app' | grep -v grep

# Start
cd /home/ubuntu
nohup /DarkSpace/HippoCampus/bin/streamlit run /DarkSpace/InternshipPortal/trainer_review_app.py \
  --server.port 8002 --server.address 0.0.0.0 --server.headless true &
```

### Redis
```bash
# Check status
redis-cli ping   # Should return PONG

# Restart
sudo systemctl restart redis
```
