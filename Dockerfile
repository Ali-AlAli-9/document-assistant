FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# CPU-only PyTorch, installed first so sentence-transformers/transformers find
# it already satisfied. The default Linux wheel from PyPI bundles the whole CUDA
# stack (~2.5GB); the host has no GPU, and the CPU wheel is roughly a quarter
# of that — which matters a lot for build time on a free CI builder.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch

# Install Node.js for frontend build
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Bake the embedding model into the image. On a free host the instance may be
# stopped for hours between two visits, and downloading the model on every cold
# start would make the first visitor wait on huggingface.co (or fail outright if
# it is unreachable).
#
# EMBEDDING_REPO, not EMBEDDING_MODEL: Render passes the service's env vars into
# the BUILD environment, and EMBEDDING_MODEL is a runtime setting whose value is
# a bare model name. Naming the build arg the same thing let that runtime value
# override a correct repo id and the first Render build died with
# "Repository Not Found ... /api/models/all-MiniLM-L6-v2". Different name, and
# the build arg can no longer be clobbered by a runtime setting.
ARG EMBEDDING_REPO=intfloat/multilingual-e5-small
# Pinned to an exact sha, and the same one verified to load with HF_HUB_OFFLINE=1
# from a local copy — so the image and a developer's machine hold the same
# weights. Left floating, snapshot_download takes whatever main points at on the
# day the build runs: an upstream push would change what the assistant answers,
# with no line of our code changing and no test failing. A full 40-character
# sha, so the pin cannot rot into a moving tag.
ARG EMBEDDING_REVISION=614241f622f53c4eeff9890bdc4f31cfecc418b3
ARG EMBEDDING_MODEL_DIR=/app/models
ENV EMBEDDING_MODEL_DIR=${EMBEDDING_MODEL_DIR}
# The destination folder is the repo id's basename (multilingual-e5-small), which
# is what resolve_local_model_path() looks for at runtime, so the two cannot drift.
#
# The allow list is the whole point of this step being hand-written. Only these
# nine files are ever opened by the loader; everything else in the repository is
# dead weight in the image, paid for on the free builder and on every deploy push
# and pull. Measured: this list bakes 482MB, of which model.safetensors is 460MB
# (fp32).
#
# e5 is a XLM-R checkpoint, so its tokenizer is SentencePiece, not WordPiece:
# sentencepiece.bpe.model replaces vocab.txt, and config_sentence_transformers.json
# is dropped because this checkpoint does not publish it (that file belongs to
# sentence-transformers 3+ and the previous, English-only model did have one).
# Neither removal is cosmetic. The guard below fails the BUILD on any listed file
# that was not written, so a leftover entry from the old model breaks deploys
# rather than merely bloating them.
#
# The list is a literal, not an ARG, on purpose: it is a correctness requirement,
# not a knob. A knob invites someone to trim it, and trimming it is exactly the
# change that turns a successful build into a failure in front of a company.
# It is the set of files that loads from a local directory today, minus the
# README, which nothing reads.
#
# A plain -c one-liner, not a heredoc: heredocs need the
# docker/dockerfile:1.4+ frontend, and a build must not depend on which
# builder version Render happens to run.
RUN python -c "import os,sys; from huggingface_hub import snapshot_download; repo=os.environ['EMBEDDING_REPO']; rev=os.environ['EMBEDDING_REVISION']; dest=os.path.join(os.environ['EMBEDDING_MODEL_DIR'], repo.rstrip('/').split('/')[-1]); wanted=['1_Pooling/config.json','config.json','model.safetensors','modules.json','sentence_bert_config.json','sentencepiece.bpe.model','special_tokens_map.json','tokenizer.json','tokenizer_config.json']; snapshot_download(repo, revision=rev, allow_patterns=wanted, local_dir=dest); missing=[n for n in wanted if not os.path.isfile(os.path.join(dest,n))]; sys.exit('baked model is missing: '+', '.join(missing)) if missing else None; print('baked', repo, '@', rev, '->', dest, '(%d files, %.0f MB)' % (len(wanted), sum(os.path.getsize(os.path.join(dest,n)) for n in wanted)/1048576))"

# Nothing should reach for the network at runtime; fail fast and loudly instead
# of hanging a request while a download times out.
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

COPY . .

# Build frontend
RUN cd frontend && npm install && npm run build && cd ..

# collectstatic needs Django's settings, and SECRET_KEY is a required setting on
# purpose: a default would mean shipping one public key to every deployment that
# forgets to set it. Render passes its env vars as BUILD ARGS, and Docker only
# exposes a build arg to RUN when the Dockerfile declares a matching ARG — so no
# real SECRET_KEY exists at build time, and none is needed: collectstatic only
# uses it to sign nothing and hash a few filenames. The placeholder is scoped to
# this single command, so it is not stored in the image's environment, and a
# container started without a real key still fails loudly instead of guessing.
RUN SECRET_KEY=build-time-only-collectstatic-placeholder python manage.py collectstatic --noinput

EXPOSE 8000

# Migrations run at every start (idempotent) because managed free hosts give no
# pre-deploy hook and the database is created fresh on the container disk.
#
# One gthread worker, not four sync workers. Every worker process loads its own
# copy of the embedding model, and multilingual-e5-small's fp32 weights alone are
# 460MB — so a second worker is not a tuning knob that could be turned down,
# it cannot load. The SSE chat stream needs threads rather than processes.
#
# --timeout 600, raised from 180: ingestion runs synchronously inside the upload
# request and takes minutes with this model. At 180 the worker was killed
# mid-ingest, stranding the row as `processing` — a state no button can retry.
# --forwarded-allow-ips lets gunicorn honour X-Forwarded-Proto from the host's
# proxy so request.is_secure() stays correct behind the TLS terminator.
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn rag_project.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 1 --worker-class gthread --threads 4 --timeout 600 --forwarded-allow-ips='*'"]
