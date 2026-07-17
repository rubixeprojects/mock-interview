# Internship Portal

A Django-based platform for managing student internship projects — submission, automated LLM evaluation, trainer review, and email delivery.

**Live URL:** `internship.rubixeprojects.com`

---

## Documentation

| Document | What it covers |
|----------|----------------|
| [DEV_AND_DEBUG_GUIDE.md](DEV_AND_DEBUG_GUIDE.md) | Server access (SSH, PEM), database credentials, S3 storage, common debugging commands, known issues & fixes, how to restart services |
| [SERVER_ARCHITECTURE.md](SERVER_ARCHITECTURE.md) | All services running on the server, port mappings, directory layout, cron jobs, external services, how to start/stop each service |
| [PROJECT_REFERENCE.md](PROJECT_REFERENCE.md) | What each file/module does, all 17 database models, URL routes, LLM evaluation pipeline, management commands, status transitions |

---

## Quick Start

```bash
# SSH to server
ssh -i "project_evaluation_head.pem" ubuntu@15.206.74.51

# Project location
cd /home/ubuntu/InternshipPortal

# Run server
/DarkSpace/venv/bin/python3 manage.py runserver 8001

# Check email status
/DarkSpace/venv/bin/python3 quick_check.py

# Send pending emails
/DarkSpace/venv/bin/python3 send_emails.py
```

---

## Tech Stack

- **Backend:** Django 4.2 / Python 3.12
- **Database:** MySQL 8 (AWS RDS)
- **Storage:** AWS S3
- **Cache:** Redis
- **Search:** Elasticsearch
- **LLM:** Gemini + Groq (fallback)
- **Email:** Gmail SMTP
- **Trainer UI:** Streamlit
