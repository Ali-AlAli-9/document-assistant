# Document Assistant

استفسر عن مستنداتك بالعربية.

Upload PDF, TXT, or DOCX and ask questions in Arabic. The system extracts text, creates embeddings, and uses an LLM to answer based on your documents — fully offline or cloud-powered.

## Why this project?

Most RAG tools send your documents to a cloud provider. If you're dealing with **confidential contracts, medical records, or internal company data**, that's a problem.

**Document Assistant** gives you a choice:
- **Ollama** — fully offline, your data never leaves your machine
- **Gemini** — cloud-powered, no local model needed (just an API key)

Pick what fits your needs. Switch providers at any time without restarting the server.

## Features

- **Arabic-first UI** — full RTL support, Arabic labels and error messages
- **Per-account keys, encrypted at rest** — each user brings his own Gemini key; it is stored encrypted (Fernet, key derived from `SECRET_KEY` via HKDF-SHA256), never returned to the browser, never rendered in the admin, and a database dump yields no working credential
- **Multi-document retrieval** — search queries run per-document then merge results for diverse coverage
- **2 LLM providers** — Ollama, Gemini — switchable at runtime
- **Streaming responses** — real-time SSE streaming for chat
- **Embedder preloading** — model loads on server startup, instant first query
- **Embedding provider switch** — `local` (multilingual `e5-small`, offline, free, no quotas, correct Arabic) or `gemini` (Google AI Studio `embedContent` API — optional quality upgrade, quota-limited)
- **Corruption recovery** — ChromaDB auto-recovers from index corruption
- **Prompt injection protection** — filters common injection patterns
- **Rate limiting** — per-IP rate limits on chat and upload endpoints
- **Clean Architecture** — domain layer has zero framework dependencies

## Requirements

| Tool | Version | Required for |
|------|---------|--------------|
| Python | 3.10+ | Backend |
| Node.js | 18+ | Frontend build (once) |
| Ollama | latest | Only if using Ollama (local LLM) |

## Embedding Model

The app loads a sentence-transformers snapshot that must be available locally
(`models/` folder or the HuggingFace cache). **The default is
`intfloat/multilingual-e5-small`** (~460MB of fp32 weights) — free, offline,
**no API quotas**, and Arabic-capable. It replaced the previous
`all-MiniLM-L6-v2` default, which was English-only: it tokenized Arabic
letter-by-letter and collapsed retrieval to noise (measured: the correct section
ranked 167/265). Measured on the demo corpus (228 chunks, 9 queries), e5-small
gives **8/9 Hits@5 and 4/9 Hits@1** — on par with the Gemini API and with better
top-1 positioning.

```powershell
# Download once (needs internet) — or just let sentence-transformers fetch it
# into the HuggingFace cache automatically on the first query.
pip install huggingface-hub
python -c "from huggingface_hub import snapshot_download; snapshot_download('intfloat/multilingual-e5-small', local_dir='models/multilingual-e5-small')"
```

Then set in `.env`:

```
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=intfloat/multilingual-e5-small
```

**Changing the model means re-ingesting.** Both models emit 384-dim vectors, so
the Chroma collection stays dimension-valid, but vectors from different models
are not comparable — old chunks and new ones would be ranked against each other
in the same collection. Empty the collection (`manage.py reset_demo`) before
uploading anything with a different model.

**Optional — Gemini API** (`EMBEDDING_PROVIDER=gemini`, with `GEMINI_API_KEY`):
uses Google AI Studio `embedContent` (`gemini-embedding-001`, 768-dim). Excellent
Arabic semantics, but it consumes the free daily quota (a burst of ingestion can
hit `429 quota exceeded` until the daily reset). Only needed if you want the API
quality ceiling and accept rate-limit risk. See
`infrastructure/embedding/gemini_embedding_adapter.py`.

## Quick Start

```powershell
git clone <repo-url>
cd document-assistant-main

# 1. Virtual environment
python -m venv venv
venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Build frontend
cd frontend
npm install
npm run build
cd ..

# 4. Run migrations
python manage.py migrate

# 5. Start the server
python manage.py runserver 8000
```

Open: **http://localhost:8000**

The embedding model must be available locally — recommended:
`intfloat/multilingual-e5-small` (~460MB, Arabic-capable, free) in
`models/multilingual-e5-small/` or in the HuggingFace cache — see
[Embedding Model](#embedding-model) below.

## Manual Setup

### Option A: Ollama (Offline / Privacy)

1. Download & install from [ollama.com](https://ollama.com)
2. Pull a model:
   ```powershell
   ollama pull llama3.2:3b
   ```
3. Edit `.env`:
   ```ini
   LLM_PROVIDER=ollama
   OLLAMA_MODEL=llama3.2:3b
   ```

### Option B: Gemini (Cloud)

1. Get a free API key from [aistudio.google.com](https://aistudio.google.com)
2. Edit `.env`:
   ```ini
   LLM_PROVIDER=gemini
   GEMINI_API_KEY=your_key_here
   GEMINI_MODEL=gemini-2.5-flash
   ```

### After either option

```powershell
python manage.py runserver 8000
```

## Usage

### Web UI
1. Open http://localhost:8000
2. Upload documents (PDF, TXT, or DOCX) — multiple documents supported
3. Wait for status to become **جاهز** (Ready)
4. Type your question in Arabic and press Send

### API

All endpoints at `/api/`:

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/documents` | List all documents |
| `POST` | `/api/documents/upload` | Upload a file |
| `GET` | `/api/documents/{id}` | Get single document |
| `DELETE` | `/api/documents/{id}` | Delete a document + its chunks |
| `POST` | `/api/documents/{id}/retry` | Retry failed ingestion |
| `POST` | `/api/chat/ask` | Ask a question (non-streaming) |
| `POST` | `/api/chat/stream` | Ask a question (SSE streaming) |
| `POST` | `/api/settings/validate-key` | Test an API key |
| `GET` | `/api/status/` | Embedder loading status |
| `GET` | `/api/health/` | Health check |
| `GET` | `/api/csrf/` | Set CSRF cookie |

The provider and the user's own Gemini key live **server-side on the account**, encrypted at rest. The browser sends neither, so no key is exposed to the page, to a proxy log, or to a request trace. `X-Provider` and `X-API-Key` were the earlier transport and no endpoint reads them any more.

## Architecture

```
┌─────────────────────────────────────┐
│           Browser (React)           │
│  ┌──────────────┐                   │
│  │ SetupWizard  │  (أول تشغيل فقط)  │
│  └──────────────┘                   │
└────────────────┬────────────────────┘
                 │ HTTP / SSE
┌────────────────┴────────────────────┐
│       Django REST API (Ninja)       │
│  ┌──────────────────────────────┐   │
│  │ Per-document retrieval       │   │
│  │ search each doc → merge      │   │
│  └──────────────────────────────┘   │
└────────────────┬────────────────────┘
                 │
     ┌───────────┼───────────┐
     ▼           ▼           ▼
┌─────────┐ ┌─────────┐ ┌──────────────┐
│ ChromaDB│ │Sentence │ │ LLM          │
│ (Vector)│ │Transf.  │ │ (Ollama /    │
│         │ │(Embed)  │ │  Gemini) │
│         │ │ local   │ │          │
└─────────┘ └─────────┘ └──────────────┘
```

- **Frontend**: React 18 + Vite, served by Django
- **Backend**: Django 6 + django-ninja (Clean Architecture)
- **Vector DB**: ChromaDB (PersistentClient — survives restarts)
- **Embeddings**: sentence-transformers / multilingual `e5-small` (384-dim — Arabic-capable, free, no quotas; loaded from local `models/` or the HuggingFace cache) with optional Gemini API
- **LLM**: Ollama / Gemini — switchable at runtime per account from the settings panel. Each account supplies its own Gemini key; there is no server-wide fallback for chat, so one visitor can never spend another's quota (`tests/api/test_no_shared_key_fallback.py`)

## Project Structure

```
document-assistant-main/
├── api/                          # REST API layer
│   ├── endpoints/
│   │   ├── documents.py          # Document CRUD + upload
│   │   ├── chat.py               # Chat endpoints (ask, stream)
│   │   └── settings.py           # API key validation
│   ├── urls.py                   # Router setup
│   └── apps.py                   # AppConfig — preloads embedder
├── application/                  # USE CASES
│   ├── commands/
│   │   ├── upload_document.py    # Text extraction, chunking, embedding
│   │   └── ask_question.py       # Per-document retrieval + LLM call
│   └── di.py                     # Dependency injection (manual)
├── domain/                       # DOMAIN (zero dependencies)
│   ├── entities/
│   │   ├── document.py           # Document entity + status enum
│   │   ├── conversation.py       # Conversation + message history
│   │   └── chunk.py              # Chunk entity
│   └── ports/
│       ├── llm.py                # LLMPort (generate + stream)
│       ├── embedding.py          # EmbeddingPort
│       ├── vector_store.py       # VectorStorePort
│       └── document_repository.py
├── infrastructure/               # ADAPTERS
│   ├── llm/
│   │   ├── ollama_adapter.py     # Ollama (HTTP)
│   │   └── gemini_adapter.py     # Gemini (OpenAI-compat)
│   ├── embedding/
│   │   └── sentence_transformer_adapter.py  # Local model loading
│   ├── vector_store/
│   │   └── chroma_adapter.py     # ChromaDB + corruption recovery
│   └── repository/
│       └── django_document_repo.py
├── core/                         # CROSS-CUTTING
│   ├── config.py                 # Pydantic BaseSettings
│   ├── exceptions.py             # Exception hierarchy
│   ├── logging.py                # Configurable logging
│   └── middleware.py             # Rate limiter
├── models/                       # EMBEDDING MODEL (not in git — download separately)
│   └── multilingual-e5-small/   # ~460MB, see README instructions
├── frontend/                     # REACT SPA
│   └── src/
│       ├── App.jsx               # Main app + embedder status
│       ├── api/client.js         # API client + SSE streaming
│       └── components/
│           ├── SetupWizard.jsx   # First-run wizard
│           ├── SettingsPanel.jsx # Provider/API key settings
│           ├── DocumentUpload.jsx # Drag-and-drop upload
│           ├── DocumentList.jsx  # Document list + status
│           ├── ChatInterface.jsx # Streaming chat
│           └── ChatMessage.jsx   # Message bubble + sources
├── documents/                    # Django app (ORM)
├── rag_project/                  # Django project config
├── chroma_db/                    # ChromaDB data (auto-created)
├── .env.example                  # All settings documented
├── requirements.txt
├── docker-compose.yml
└── manage.py
```

## Configuration

All settings are in `.env` (copy from `.env.example`). Key settings:

### Database
| Setting | Default | Description |
|---------|---------|-------------|
| `DB_NAME` | `sqlite` | `sqlite` for dev, any other value uses PostgreSQL |
| `DB_USER` | `postgres` | PostgreSQL user |
| `DB_PASSWORD` | (empty) | PostgreSQL password |

### Embedding
| Setting | Default | Description |
|---------|---------|-------------|
| `EMBEDDING_PROVIDER` | `local` | `local` (sentence-transformers) or `gemini` (embedContent API) |
| `EMBEDDING_MODEL` | `intfloat/multilingual-e5-small` | The one local model. Changing it requires re-ingesting (see [Embedding Model](#embedding-model)); a Gemini model id when `EMBEDDING_PROVIDER=gemini` |
| `EMBEDDING_MODEL_DIR` | `models` | Local directory containing the model |
| `EMBEDDING_DIMENSION` | `768` | Output dimensionality for API embeddings only (local models set their own) |

### Retrieval
| Setting | Default | Description |
|---------|---------|-------------|
| `RETRIEVAL_TOP_K` | `20` | Max chunks per document in search |
| `RETRIEVAL_SCORE_THRESHOLD` | `0.1` | Minimum similarity score |
| `RETRIEVAL_SECTION_EXPAND` | `3` | When a matched chunk carries a section heading (e.g. "4-4-1 ..."), pull that many following sibling chunks into context so the section body reaches the LLM (breadth questions like "ابحث في كل الفصول عن الصعوبات والتحديات"). `0` disables expansion |
| `RETRIEVAL_MAX_CONTEXT_CHUNKS` | `50` | Hard ceiling on chunks handed to the LLM after top-K + expansion, deduplicated |

### LLM
| Setting | Default | Description |
|---------|---------|-------------|
| `LLM_PROVIDER` | `ollama` | `ollama` or `gemini` |
| `LLM_TIMEOUT` | `60` | Request timeout (seconds) |
| `LLM_MAX_RETRIES` | `2` | Max retries on failure |

### Conversation
| Setting | Default | Description |
|---------|---------|-------------|
| `CONVERSATION_HISTORY_LENGTH` | `6` | Max messages kept |
| `LLM_MAX_CONTEXT_CHARS` | `4000` | Max context characters |
| `CONVERSATION_TTL_MINUTES` | `30` | Idle conversation TTL (minutes) |
| `CONVERSATIONS_MAX_PER_SESSION` | `10` | Max conversations kept per session (LRU) |

> **Server-exit cleanup:** on real server shutdown (`runserver` / gunicorn / uvicorn / daphne), run-time data is wiped so each server run starts fresh — conversations, `media/uploads/`, document records, the ChromaDB collection and `django_session` rows. **User accounts are never touched.** Management commands (`migrate`, `check`, …) and pytest runs never trigger cleanup. `RAG_CLEANUP_ON_EXIT=1` forces cleanup on, `0` forces it off.
>
> On a hosted service you **must** set `RAG_CLEANUP_ON_EXIT=0` — a managed free
> instance is stopped and woken constantly, and the cleanup would erase what
> visitors uploaded every time. The app logs a warning when it is armed in a
> non-DEBUG server.

### Upload
| Setting | Default | Description |
|---------|---------|-------------|
| `MAX_UPLOAD_SIZE` | `52428800` (50MB) | Max file size |
| `ALLOWED_EXTENSIONS` | `.pdf, .txt, .docx` | Allowed file types |

### Logging
| Setting | Default | Description |
|---------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Python logging level |

### Demo cleanup
| Setting | Default | Description |
|---------|---------|-------------|
| `RAG_CLEANUP_ON_EXIT` | unset (OFF) | `1` re-arms the destructive exit hook. Requires a server process as well, so it can never fire during a management command. Legacy path — see below. Prefer `manage.py reset_demo`. |

### Secret handling

`SECRET_KEY` is not only the Django signing key: it also derives the encryption
key for stored provider keys (HKDF-SHA256 → Fernet). Two consequences:

- **Never commit it** — it is in `.gitignore` via `.env`; on Render it is
  generated automatically (`generateValue: true` in `render.yaml`).
- **Rotating it invalidates stored keys** — users must re-enter their Gemini
  key. That is deliberate: the alternative is a second long-lived secret whose
  only job is to be rotated less often.

## Operating the demo data

Four stores hold what the app knows: document rows, the uploaded files, the
Chroma collection, and conversations/sessions. Nothing guarantees they agree, and
when they disagree every symptom is silent — retrieval just answers badly.

### `manage.py reset_demo`

Clears run-time data. **User accounts are never touched.** It asks first; pass
`--yes` for automation.

```powershell
# Everything (documents, vectors, conversations, sessions)
python manage.py reset_demo

# Scoped: only documents whose title starts with demo-, uploaded by user 3
python manage.py reset_demo --yes --prefix demo- --owner-id 3
```

A filter that matches nothing deletes nothing. A mistyped `--prefix` therefore
cannot be read as "no filter" and wipe everything.

Deletion order is vectors → rows → files, and a failure at the vector step
aborts the whole reset. Chunks are the only part that cannot be rebuilt from the
uploaded file, so they are removed first: if the store refuses, the document
survives intact rather than becoming a row whose chunks nothing points at.

This used to be an `atexit` hook that fired on any server shutdown — one `Ctrl+C`
destroyed everything above. It is now a command you type. `RAG_CLEANUP_ON_EXIT=1`
still re-arms the hook, but it is a legacy path: importing chromadb is not safe
during interpreter shutdown (OpenTelemetry submits work to an already-closed
thread pool), so when the store cannot be opened at exit the hook deletes
nothing and says so. Prefer the command.

### `manage.py verify_consistency`

Compares the three stores and exits non-zero when they disagree:

```powershell
python manage.py verify_consistency
echo $LASTEXITCODE     # 0 = consistent, 1 = a problem or a check that could not run
```

It reports three things separately, because the remedy differs:

- **missing file** — the row points at a file that is gone → re-upload
- **orphan file** — a file in `media/uploads/` that no row claims → delete it
- **chunk mismatch** — `chunk_count` disagrees with the chunks actually stored →
  re-ingest

`chunk_count` cannot detect this on its own; it is the database grading its own
homework. Only a read of the real index can. A check that could **not run** is
reported as a failure too — a green result that was never verified is the most
dangerous output this command can give.

### `manage.py reconcile_documents`

Ingest runs synchronously inside the upload request and takes minutes with the
local model. Stop the server in that window and the row stays `processing` — a
state nothing can retry, so the document is stuck for ever. This marks rows
untouched for longer than 10 minutes as `failed`, which restores the retry button.

It also runs automatically on server start (`--minutes N` to override).

### Writer lock

Two clients writing to Chroma at once corrupt the SQLite file behind it. Every
write path (upload, retry, delete, `reset_demo`) takes a lock file next to the
Chroma directory, and a second writer is refused with a `409` naming the holder.
A lock left by a dead process is reclaimed automatically. If the lock file
cannot be created at all, the app logs a warning and proceeds — an unwritable
lock location must not take every upload down with it.

## Docker

```powershell
docker compose up --build
```

Services:
- **db**: PostgreSQL 16 Alpine (persistent volume)
- **backend**: Django + Gunicorn (1 `gthread` worker, 4 threads, 180s timeout)

A single worker is deliberate: every worker process loads its own copy of the
embedding model (hundreds of MB resident with the PyTorch runtime), and the SSE
chat stream needs threads rather than processes.

## Deployment (free hosting)

The repository ships a `Dockerfile` and a `render.yaml` blueprint, so it deploys
to Render's free tier with a public HTTPS URL:

```powershell
git init && git add . && git commit -m "deploy"
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

Then in Render: **New → Blueprint → pick the repository → Apply**.

Two variables matter more than the rest:

| Variable | Value | Why |
|----------|-------|-----|
| `RAG_CLEANUP_ON_EXIT` | `0` | Otherwise every server stop/sleep-wake deletes uploaded files, document rows and the vector store (see the cleanup note above) |
| `TRUST_PROXY_HEADERS` | `true` | Behind the platform's proxy `REMOTE_ADDR` is the proxy itself, which would merge every visitor into one rate-limit bucket |

Full walkthrough, free-tier caveats (sleeps after 15 min idle, ephemeral disk)
and a troubleshooting table: **[DEPLOY.md](DEPLOY.md)**.

## How Retrieval Works

When you ask a question with multiple documents uploaded:

1. **Per-document search**: The query runs against each document separately (top 3 chunks per document)
2. **Merge & rank**: Results from all documents are merged and sorted by similarity score
3. **Score filtering**: Chunks below the threshold are dropped (fallback: top 3 overall)
4. **Context building**: Surviving chunks are assembled into the LLM prompt
5. **LLM answer**: The model answers based on the multi-document context

This ensures that **all uploaded documents contribute** to the answer, not just the one with the highest overall similarity.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Page is blank | Run `cd frontend && npm run build`, restart Django |
| "No module named..." | Run `pip install -r requirements.txt` |
| Embedding model fails | Download the model — see [Embedding Model](#embedding-model) section |
| Ollama not responding | Run `ollama serve` or reinstall from [ollama.com](https://ollama.com) |
| Gemini API error | Check your API key in Settings. Get one at [aistudio.google.com](https://aistudio.google.com) |
| ChromaDB corruption | Delete `chroma_db/` folder and restart — auto-recreates |
| "I don't have information" | Upload more documents or rephrase — the system searches each document independently |
| Rate limit exceeded | Wait 1 minute — limits are 20 req/min for chat, 10 req/min for upload |

## License

MIT
"# document-assistant" 
