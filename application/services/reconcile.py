"""Recover documents whose ingest was cut short by a server restart.

The failure this prevents
=========================
Ingest runs synchronously inside the upload request and, with the local e5
model, takes minutes. If the process dies in that window the row stays
``processing`` — and ``processing`` is not retryable
(``Document.is_processable`` accepts only ``pending``/``failed``). The document
is then permanently stuck: the UI shows "قيد المعالجة" for ever, ``chunk_count``
is 0 so retrieval finds nothing, and no button can rescue it.

Why ``failed`` and not deletion
===============================
The uploaded file is on disk and intact, so the work is not lost — it needs
re-running. ``failed`` is the state the UI already knows how to present and
whose "إعادة المعالجة" button exists precisely for this.

Why this runs at BOOT rather than at exit
=========================================
An exit hook cannot help: the process that would notice the interruption is the
one that crashed. Recovery has to happen when a process starts and can see
rows left behind by every process before it.
"""

import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)

#: A row untouched for this long cannot belong to a live ingest: the server
#: restarts in seconds, and a real ingest of a large PDF takes minutes, so the
#: bound has to clear the longest legitimate run with room to spare.
STALE_PROCESSING_MINUTES = 10


def reconcile_interrupted_documents(
    max_age_minutes: int = STALE_PROCESSING_MINUTES,
    now=None,
) -> list[int]:
    """Flip abandoned ``processing`` rows to ``failed``.

    Returns the ids that were recovered, newest first, so a caller can log or
    surface exactly what was rescued. Idempotent: a second call finds nothing,
    which is what makes it safe to run on every boot.
    """
    from documents.models import Document

    now = now or timezone.now()
    cutoff = now - timedelta(minutes=max_age_minutes)

    stale = Document.objects.filter(status='processing', updated_at__lt=cutoff)
    recovered = list(stale.values_list('id', flat=True))
    if recovered:
        stale.update(status='failed')
        logger.warning(
            'Recovered %d interrupted document(s) marked failed: %s',
            len(recovered),
            recovered,
        )
    return recovered
