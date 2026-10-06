# Mock Interview Backend — PHP port

A PHP re-implementation of the Python/FastAPI mock-interview backend. It exposes
the **same HTTP API** and serves the **same demo page**, so any frontend built
against the Python backend works unchanged.

It orchestrates a **Dograh** voice agent (WebRTC), proxies camera frames to the
**computer-vision** service, and produces a **post-interview report** (transcript
+ attention analysis + an AI evaluation of the answers).

## Requirements

- **PHP 8.1+** with the `curl` and `json` extensions (both standard).
- Network access to a running Dograh instance (and, optionally, the CV service
  and an LLM provider).

No Composer dependencies — everything is vanilla PHP.

## Run

```bash
cp .env.example .env      # then fill in DOGRAH_* and EVAL_LLM_* values
bash start.sh             # detached on 0.0.0.0:8080, logs -> server.log
# or, in the foreground:
php -S 0.0.0.0:8080 router.php
```

Open `http://localhost:8080/` for the demo. Serve behind HTTPS in production
(browsers block mic/camera on non-localhost HTTP).

## Verify

```bash
curl -s http://127.0.0.1:8080/healthz
curl -s http://127.0.0.1:8080/config
```

## Project layout

```
php-backend/
  router.php            # dev-server router (serves static, else -> index.php)
  start.sh              # detached launcher
  migrate_workflow.php  # CLI: copy a workflow (flow+prompts+config) between Dograh servers
  public/
    index.php           # front controller (all routes)
    demo.html           # reference client (identical to the Python backend)
  src/
    Config.php          # .env / env loader + URL helpers
    Http.php            # cURL wrapper
    DograhClient.php    # Dograh REST (embed token, init session, get run)
    Store.php           # file-based session store (data/sessions/*.json)
    Transcript.php      # rtf-* events -> chat messages
    LlmClient.php       # Gemini / OpenAI-compatible (replaces LiteLLM)
    Report.php          # stats + AI evaluation + saving
    Vision.php          # CV service proxy
  data/
    sessions/           # runtime: one JSON per interview (git-ignored)
    reports/            # runtime: saved reports, PII (git-ignored)
```

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/interviews` | Create voice session → connect info |
| `GET`  | `/api/interviews/{id}` | Status + transcript |
| `GET`  | `/api/interviews/{id}/messages` | Per-turn conversation |
| `POST` | `/api/interviews/{id}/end` | End + finalize |
| `GET`  | `/api/interviews/{id}/report[?eval_model=]` | Full report (+persist) |
| `POST` | `/api/vision/analyze` | Analyze one camera frame |
| `GET`  | `/api/vision/session/{id}` | Aggregated CV analysis |
| `GET`  | `/api/vision/health` | CV service health |
| `POST` | `/api/webhooks/dograh` | Dograh end-of-call webhook |
| `GET`  | `/config`, `/healthz` | Runtime config / health |

See `../backend/FRONTEND_GUIDE.md` for the full API contract and client flow.

## Migrating a workflow between Dograh servers

`migrate_workflow.php` copies the mock-interview workflow — its **flow**
(definition/nodes/edges), **prompts**, and **turn-taking configuration** — from a
**source** Dograh instance to a **target** Dograh instance. By default the target
is the server described in `../deploy/server-info.txt` (the Lightsail box, Dograh
API on port `8000`).

It uses only Dograh's control-plane REST API (`GET /workflow/fetch/{id}` →
`POST /workflow/create/definition` → `PUT /workflow/{id}` for the turn-taking
config → `POST /workflow/{id}/publish`). Nothing is hardcoded: credentials come
from CLI flags, the environment, or `.env`.

```bash
# Preview what would be copied (reads source from .env, writes nothing):
php migrate_workflow.php --dry-run

# Copy workflow #3 from a source host to the Lightsail target.
# Fresh target servers have ENABLE_SIGNUP=true, so sign up + log in, and mint an
# API key for this backend's .env in one go:
php migrate_workflow.php \
  --source-url=http://127.0.0.1:8000 --source-api-key=<SOURCE_KEY> \
  --source-workflow-id=3 \
  --target-url=http://3.6.32.171:8000 \
  --target-email=you@example.com --target-password=<PASS> --target-signup \
  --target-create-api-key

# Overwrite an existing same-named workflow on the target instead of creating new:
php migrate_workflow.php ... --update
```

Run `php migrate_workflow.php --help` for all options (source/target auth,
`--name`, `--no-publish`, `--out=backup.json`, etc.). On success it prints the new
target workflow **id** and **uuid** and the exact `.env` lines to point this
backend at the migrated workflow. Provide secrets via flags or environment only —
never commit them.

## Differences from the Python backend

- **Session storage is file-based** (`data/sessions/*.json`) instead of
  in-process memory, because PHP is request-scoped. Behaviour is otherwise the
  same. (For multi-node deployments, point this at shared storage or a DB.)
- **No LiteLLM.** `LlmClient` calls providers directly: `gemini/*` via the Google
  Generative Language API, and `openrouter/*` / `openai/*` / `gpt-*` (or anything
  with `EVAL_LLM_API_BASE`) via an OpenAI-compatible `/chat/completions`
  endpoint. The Gemini 503 fallback chain is preserved.
- Runs under the PHP built-in server, PHP-FPM + nginx/Apache, or any SAPI. The
  provider API key is read from `.env` only (never from the browser).
