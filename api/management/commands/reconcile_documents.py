"""``manage.py reconcile_documents`` — rescue rows abandoned by a dead process.

WHY
===
Ingest runs synchronously inside the upload request and, with the local
embedding model, takes minutes. Stop the server in that window and the row stays
``processing`` — a state nothing can retry (``Document.is_processable`` accepts
only ``pending`` and ``failed``). The document is then stuck for ever: the UI
shows "processing" indefinitely, ``chunk_count`` stays 0 so retrieval finds
nothing, and no button rescues it.

The startup hook does this automatically. This command is the same repair on
demand, and it prints exactly what it recovered — which is what you need when
someone reports a stuck document and you need to see whether the row really was
abandoned, and which ones.

It reports and never raises: a recovery command that failed loudly on a database
with nothing to fix would be useless.
"""

from django.core.management.base import BaseCommand

from application.services.reconcile import (
    STALE_PROCESSING_MINUTES,
    reconcile_interrupted_documents,
)

RECOVERED_HEADER = (
    "\u062a\u0645\u062a \u0627\u0633\u062a\u0639\u0627\u062f\u0629 "
    "{count} \u0645\u0633\u062a\u0646\u062f \u0639\u0627\u0644\u0642\u0629 "
    "\u0648\u062a\u0645 \u062a\u0639\u064a\u064a\u0646\u0647\u0627 \u0643\u0641\u0627\u0634\u0644: "
)
RECOVERED_ROW = "  - id={doc_id}"
NOTHING_STUCK = "\u0644\u0627 \u0645\u0633\u062a\u0646\u062f\u0627\u062a \u0639\u0627\u0644\u0642\u0629."


class Command(BaseCommand):
    help = (
        'Mark documents left in "processing" by an interrupted ingest as '
        'failed, so they become retryable again.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--minutes',
            type=int,
            default=None,
            help=(
                'Treat rows untouched for longer than this as abandoned '
                f'(default: {STALE_PROCESSING_MINUTES}).'
            ),
        )

    def handle(self, *args, **options):
        max_age = options.get('minutes')
        if max_age is None:
            recovered = reconcile_interrupted_documents()
        else:
            recovered = reconcile_interrupted_documents(max_age_minutes=max_age)

        if not recovered:
            self.stdout.write(NOTHING_STUCK)
            return

        self.stdout.write(RECOVERED_HEADER.format(count=len(recovered)))
        for doc_id in recovered:
            self.stdout.write(RECOVERED_ROW.format(doc_id=doc_id))