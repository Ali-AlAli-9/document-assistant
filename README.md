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
- **Multi-document retrieval** — search queries run per-document then merge results for diverse coverage
- **2 LLM providers** — Ollama, Gemini — switchable at runtime
- **Streaming responses** — real-time SSE streaming for chat
- **Embedder preloading** — model loads on server startup, instant first query
- **Local embedding model** — `all-MiniLM-L6-v2` loads from `models/` at startup, no internet needed at runtime
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

The `all-MiniLM-L6-v2` embedding model (~87MB) is **not included** in the repository.
After cloning, download it and place it in `models/all-MiniLM-L6-v2/`:

```powershell
# Option 1: From HuggingFace (requires internet once)
pip install huggingface-hub
python -c "from huggingface_hub import snapshot_download; snapshot_download('sentence-transformers/all-MiniLM-L6-v2', local_dir='models/all-MiniLM-L6-v2')"
```

```powershell
# Option 2: Copy from an existing installation
# If you already have the model on another machine, copy the folder:
# models/all-MiniLM-L6-v2/
```

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

The embedding model (~87MB) must be placed in `models/all-MiniLM-L6-v2/` — see [Embedding Model](#embedding-model) below.

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

The frontend automatically sends `X-Provider` and `X-API-Key` headers based on user settings.

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
- **Embeddings**: sentence-transformers / all-MiniLM-L6-v2 (384-dim, loaded from local `models/`)
- **LLM**: Ollama / Gemini — switchable at runtime via `X-Provider` header

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
│   └── all-MiniLM-L6-v2/        # ~87MB, see README instructions
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
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Model name |
| `EMBEDDING_MODEL_DIR` | `models` | Local directory containing the model |

### Retrieval
| Setting | Default | Description |
|---------|---------|-------------|
| `RETRIEVAL_TOP_K` | `10` | Max chunks per document in search |
| `RETRIEVAL_SCORE_THRESHOLD` | `0.1` | Minimum similarity score |

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

### Upload
| Setting | Default | Description |
|---------|---------|-------------|
| `MAX_UPLOAD_SIZE` | `52428800` (50MB) | Max file size |
| `ALLOWED_EXTENSIONS` | `.pdf, .txt, .docx` | Allowed file types |

### Logging
| Setting | Default | Description |
|---------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Python logging level |

## Docker

```powershell
docker compose up --build
```

Services:
- **db**: PostgreSQL 16 Alpine (persistent volume)
- **backend**: Django + Gunicorn (4 workers, 120s timeout)

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
