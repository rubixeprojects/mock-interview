"""Local MPS shim for Dograh's Knowledge Base on a self-hosted OSS deployment.

Dograh's KB "files" feature delegates document conversion + chunking to its
hosted Model Proxy Service (MPS) at ``https://services.dograh.com`` via
``POST /api/v1/document/process``. On a self-hosted OSS box that endpoint 403s
(the instance isn't entitled), and ``docling`` is not bundled locally, so KB
ingestion never completes.

This shim sits between the Dograh ``api`` service and MPS:

  * ``POST /api/v1/document/process`` is handled **locally** — our KB files are
    already text/markdown, so we just extract text and (for chunked mode) split
    it into plain-text chunks. The Dograh ``api`` then generates embeddings with
    the org's own BYOK embedding config and stores them; MPS never sees the file.
  * **Every other path** is transparently proxied to the real MPS upstream, so
    unrelated features (service keys, pricing, billing, cloudonix, …) keep
    working exactly as before.

Response shapes match what ``api/tasks/knowledge_base_processing.py`` expects:
  full_document -> {"mode","full_text","docling_metadata"}
  chunked       -> {"mode","chunks":[{chunk_text,contextualized_text,chunk_index,
                    chunk_metadata,token_count}], "docling_metadata"}
"""
from __future__ import annotations

import os
import re

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

UPSTREAM = os.getenv("MPS_UPSTREAM_URL", "https://services.dograh.com").rstrip("/")
# hop-by-hop headers we must not forward
_HOP = {
    "host", "content-length", "connection", "keep-alive",
    "transfer-encoding", "upgrade", "proxy-connection", "te", "trailer",
}

app = FastAPI(title="Local MPS shim", version="1.0.0")


def extract_text(filename: str, blob: bytes) -> str:
    """Best-effort text extraction. KB files here are markdown/txt/json/csv."""
    name = (filename or "").lower()
    text = blob.decode("utf-8", errors="replace")
    if name.endswith((".html", ".htm")):
        text = re.sub(r"(?is)<script.*?</script>", " ", text)
        text = re.sub(r"(?is)<style.*?</style>", " ", text)
        text = re.sub(r"(?is)<[^>]+>", " ", text)
        text = re.sub(r"[ \t]+", " ", text)
    return text


def chunk_text(text: str, max_tokens: int, overlap_tokens: int) -> list[dict]:
    """Split text into ~max_tokens-sized chunks (token ~= whitespace word)."""
    words = text.split()
    if not words:
        return []
    size = max(1, int(max_tokens) or 128)
    overlap = max(0, min(size - 1, int(overlap_tokens)))
    step = max(1, size - overlap)
    chunks: list[dict] = []
    i = idx = 0
    while i < len(words):
        piece = words[i : i + size]
        body = " ".join(piece)
        chunks.append({
            "chunk_text": body,
            "contextualized_text": body,
            "chunk_index": idx,
            "chunk_metadata": {},
            "token_count": len(piece),
        })
        idx += 1
        i += step
    return chunks


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "upstream": UPSTREAM}


@app.post("/api/v1/document/process")
async def process_document(request: Request):
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return JSONResponse({"detail": "missing 'file' field"}, status_code=400)

    filename = getattr(upload, "filename", "document")
    blob = await upload.read()
    retrieval_mode = str(form.get("retrieval_mode", "chunked"))

    def _int(name: str, default: int) -> int:
        try:
            return int(float(form.get(name, default)))
        except (TypeError, ValueError):
            return default

    max_tokens = _int("max_tokens", 128)
    overlap = _int("chunk_overlap_tokens", 0)

    text = extract_text(filename, blob)
    meta = {"source": "local-mps-shim", "filename": filename, "chars": len(text)}

    if retrieval_mode == "full_document":
        return {"mode": "full_document", "full_text": text,
                "chunks": [], "docling_metadata": meta}

    chunks = chunk_text(text, max_tokens, overlap)
    return {"mode": "chunked", "full_text": None,
            "chunks": chunks, "docling_metadata": meta}


@app.api_route("/{full_path:path}",
               methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def passthrough(full_path: str, request: Request):
    """Transparently forward all non-document-process calls to the real MPS."""
    url = f"{UPSTREAM}/{full_path}"
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            upstream = await client.request(
                request.method, url,
                params=dict(request.query_params),
                content=body, headers=headers,
            )
    except httpx.HTTPError as exc:
        return JSONResponse({"detail": f"upstream error: {exc}"}, status_code=502)
    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP}
    return Response(content=upstream.content, status_code=upstream.status_code,
                    headers=resp_headers)
