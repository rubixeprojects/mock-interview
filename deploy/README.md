# LiteLLM + Dograh — standalone deployment

A clean, self-contained stack to run **LiteLLM** and the **Dograh** voice agent
on a fresh server. It carries over the *configuration shape* of the existing
servers but **none of their keys or models** — you add models yourself from the
LiteLLM UI, and every public URL uses the server's **host IP** (never
`localhost`).

## What's included

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| `litellm` | `ghcr.io/berriai/litellm:main-latest` | `4000` | LLM gateway + admin UI |
| `api` | `dograhai/dograh-api:latest` | `8000` | Dograh backend + WebRTC signaling |
| `ui` | `dograhai/dograh-ui:latest` | `3010` | Dograh web UI |
| `postgres` | `pgvector/pgvector:pg17` | `5432` | **Shared** database — `postgres` DB (Dograh) + `litellm` DB (LiteLLM) + `windmill` DB (Windmill) |
| `redis` | `redis:7` | `6379` | Dograh queue/cache |
| `minio` | `minio/minio` | `9000/9001` | Recording storage |
| `windmill_server` | `ghcr.io/windmill-labs/windmill:main` | `8002` | Windmill UI/API |
| `windmill_worker` | `ghcr.io/windmill-labs/windmill:main` | — | Single Windmill job worker (`default` group) |

> One Postgres serves all apps (separate databases: `postgres` for Dograh,
> `litellm` for LiteLLM, `windmill` for Windmill — the latter two are created by
> `initdb/10-create-litellm-db.sh` and `initdb/20-create-windmill-db.sh` on first
> start). Redis and MinIO are used only by Dograh, so they're already single
> instances.
>
> **Windmill is deliberately minimal**: just `windmill_server` + one
> `windmill_worker` (no native/reports worker, no indexer, no LSP/multiplayer, no
> Caddy). To add capacity later, duplicate the `windmill_worker` service (or add a
> `native` worker group). Set Windmill's public **Base URL** to
> `http://<HOST_IP>:8002` under **Instance Settings → Core** after first login.

No `nginx`/`coturn`/`cloudflared` — this is the simple LAN profile (matches how
Dograh runs on 192.168.1.23). Add TURN/HTTPS later if you need public WAN access.

## Prerequisites

- Docker Engine + Compose v2 (`docker compose version`).
- Open ports `4000, 8000, 3010, 9000, 9001, 8002` on the host firewall.
- The server's reachable IP (LAN or public).

## Deploy

```bash
cp .env.example .env
# 1) set HOST_IP to this server's IP (e.g. 192.168.1.30)
# 2) fill every empty secret — generate strong ones:
#      echo "sk-$(openssl rand -hex 24)"   # LITELLM_MASTER_KEY
#      openssl rand -hex 32                 # LITELLM_SALT_KEY, OSS_JWT_SECRET, passwords
docker compose up -d
docker compose ps
```

Then open:
- **LiteLLM UI** → `http://<HOST_IP>:4000/ui` (login = `LITELLM_UI_USERNAME` / `LITELLM_UI_PASSWORD`)
- **Dograh UI** → `http://<HOST_IP>:3010`
- **Windmill** → `http://<HOST_IP>:8002` (first login creates the superadmin; default
  seed account is `admin@windmill.dev` / `changeme` — change it immediately)

## Adding models to LiteLLM (nothing is pre-loaded)

Because `STORE_MODEL_IN_DB=True`, models live in the database and are managed
from the UI — no code changes, no restart:

1. `http://<HOST_IP>:4000/ui` → **Models** → **Add Model**.
2. Pick the provider, enter the model name and that provider's API key.
3. Save. The key is encrypted at rest with `LITELLM_SALT_KEY`.

Clients then call the proxy at **`http://<HOST_IP>:4000`** using
`LITELLM_MASTER_KEY` (or a virtual key you mint in the UI) — the base URL is the
host IP, not `localhost`.

> Prefer file-based config? Declare models in `litellm-config.yaml` using
> `api_key: os.environ/YOUR_VAR` and set `YOUR_VAR` in `.env`. Never put raw
> keys in the YAML.

## Pointing Dograh at LiteLLM (optional)

To have Dograh use this LiteLLM as its LLM provider, configure the workflow's LLM
in the Dograh UI with:
- **API base**: `http://<HOST_IP>:4000` (or `http://litellm:4000` from inside the
  compose network)
- **API key**: your `LITELLM_MASTER_KEY` or a LiteLLM virtual key
- **Model**: the `model_name` you added in LiteLLM

## Host IP, not localhost — where it's wired

`${HOST_IP}` drives all caller-facing URLs:
- LiteLLM `PROXY_BASE_URL = http://${HOST_IP}:4000`
- Dograh `BACKEND_API_ENDPOINT = http://${HOST_IP}:8000` (browser WebRTC/embed)
- Dograh `MINIO_PUBLIC_ENDPOINT = http://${HOST_IP}:9000` (recording playback)

Internal service-to-service traffic still uses Docker DNS names
(`litellm-db`, `postgres`, `redis`, `minio`, `api`).

## Operations

```bash
docker compose logs -f litellm       # or api / ui
docker compose down                  # stop (keeps data volumes)
docker compose pull && docker compose up -d   # update images
docker compose down -v               # DANGER: also deletes all data volumes
```

## Security notes

- Secrets come only from `.env` (git-ignored) — none are baked into the compose
  or config files.
- Browsers need mic/camera → serve behind **HTTPS** for any non-LAN use
  (add a reverse proxy / the Dograh `remote` profile). On plain HTTP this works
  only from `localhost` or a trusted-origin LAN setup.
- The API/DB/MinIO ports are published on the host — restrict them with the host
  firewall to trusted networks.
