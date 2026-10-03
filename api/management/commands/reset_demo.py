"""``manage.py reset_demo`` — delete the demo data, on purpose.

WHY THIS IS A COMMAND
=====================
This work used to happen in an ``atexit`` hook, which made the lifetime of the
data equal to the lifetime of the process. One ``Ctrl+C`` during development
destroyed the uploaded files, every document row, the whole vector collection,
every conversation and every session — which is exactly what happened to the demo
PDF, and what a spin-down on a hosted service would do to a visitor's upload.

A command fixes what a hook cannot:

* it is visible in the shell history, so a deletion is always attributable,
* it can be scoped (``--prefix``, ``--owner-id``) instead of all-or-nothing,
* it can refuse to run unattended.

Accounts are never touched: the people who log in outlive the files.

WHAT IT WILL NOT DO
===================
It will not run while another process is writing to the vector store. Chroma
persists through SQLite, and two concurrent writers corrupt it — so the reset
reports who holds the lock instead of proceeding.
"""

from django.core.management.base import BaseCommand

from application.services import cleanup
from infrastructure.vector_store import writer_lock

PROMPT = (
    "\u0633\u064a\u062a\u0645 \u062d\u0630\u0641 \u0627\u0644\u0645\u0644\u0641\u0627\u062a "
    "\u0627\u0644\u0645\u0631\u0641\u0648\u0639\u0629 \u0648\u0643\u0644 \u0627\u0644\u0645\u0633\u062a\u0646\u062f\u0627\u062a "
    "\u0648\u0645\u062d\u062a\u0648\u0649 \u0642\u0627\u0639\u062f\u0629 \u0627\u0644\u0645\u062a\u062c\u0647\u0627\u062a "
    "\u0648\u0627\u0644\u0645\u062d\u0627\u062f\u062b\u0627\u062a \u0648\u0627\u0644\u062c\u0644\u0633\u0627\u062a. "
    "\u0644\u0646 \u062a\u062a\u0623\u062b\u0631 \u062d\u0633\u0627\u0628\u0627\u062a "
    "\u0627\u0644\u0645\u0633\u062a\u062e\u062f\u0645\u064a\u0646. \u0647\u0644 "
    "\u062a\u0631\u064a\u062f \u0627\u0644\u0645\u062a\u0627\u0628\u0639\u0629\u061f "
    "(\u064a / yes): "
)
ABORTED = "\u0623\u0648\u0642\u0641\u062a \u0627\u0644\u0639\u0645\u0644\u064a\u0629: \u0644\u0645 \u064a\u064f\u062d\u0630\u0641 \u0634\u064a\u0621."
HINT = (
    "\u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u062d\u0630\u0641. "
    "\u0644\u062a\u062c\u0627\u0648\u0632 \u0627\u0644\u0633\u0624\u0627\u0644 "
    "\u0627\u0633\u062a\u062e\u062f\u0645 --yes."
)
DONE = "\u062a\u0645 \u0627\u0644\u062d\u0630\u0641."
VECTOR_HEADER = (
    "\u0623\u062e\u0637\u0627\u0621 \u0641\u064a \u0642\u0627\u0639\u062f\u0629 "
    "\u0627\u0644\u0645\u062a\u062c\u0647\u0627\u062a (vector store) \u2014 "
    "\u0644\u0645 \u064a\u064f\u062d\u0630\u0641 \u0634\u064a\u0621 \u0645\u0646 \u0642\u0627\u0639\u062f\u0629 "
    "\u0627\u0644\u0628\u064a\u0627\u0646\u0627\u062a:"
)
VECTOR_COUNT_LINE = (
    "\u0645\u0633\u062a\u0646\u062f\u0627\u062a \u0627\u0644\u0645\u062d\u0630\u0648\u0641\u0629: "
    "{count}"
)
FILES_COUNT_LINE = "\u0627\u0644\u0645\u0644\u0641\u0627\u062a \u0627\u0644\u0645\u062d\u0630\u0648\u0641\u0629: {count}"
CHAT_COUNT_LINE = (
    "\u0627\u0644\u0645\u062d\u0627\u062f\u062b\u0627\u062a \u0648\u0627\u0644\u062c\u0644\u0633\u0627\u062a: "
    "\u062a\u0645 \u0627\u0644\u0645\u0639\u0627\u0632\u0629"
)

_CONFIRMATIONS = {'y', 'yes'}


class Command(BaseCommand):
    help = (
        'Delete run-time demo data (uploaded files, documents, vector store, '
        'conversations, sessions). User accounts are never touched. Asks for '
        'confirmation unless --yes is given.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--yes', '-y',
            action='store_true',
            help='Delete without asking for confirmation.',
        )
        parser.add_argument(
            '--prefix',
            default=None,
            help='Only documents whose title starts with this prefix.',
        )
        parser.add_argument(
            '--owner-id',
            type=int,
            default=None,
            help='Only documents uploaded by this user id.',
        )

    def handle(self, *args, **options):
        from documents.models import Document

        documents = list(Document.objects.all())
        selected = cleanup.select_document_ids(
            documents,
            title_prefix=options['prefix'],
            owner_id=options['owner_id'],
        )
        selected = [doc for doc in documents if doc.id in set(selected)]

        if not options['yes'] and not self._confirmed():
            self.stdout.write(ABORTED)
            self.stdout.write(HINT)
            return

        try:
            # Two writers corrupt Chroma's SQLite file, so the reset refuses to
            # start rather than becoming the second one.
            with writer_lock.vector_store_writer('reset_demo'):
                report = cleanup.run_cleanup(
                    title_prefix=options['prefix'],
                    owner_id=options['owner_id'],
                    documents=documents,
                )
        except writer_lock.VectorStoreBusy as exc:
            self.stderr.write(str(exc))
            return

        if report['vector_errors']:
            self.stderr.write(VECTOR_HEADER)
            for message in report.get('vector_error_messages') or report['vector_errors']:
                self.stderr.write(f'  - {message}')
            return

        self.stdout.write(DONE)
        self.stdout.write(VECTOR_COUNT_LINE.format(count=report['documents']))
        self.stdout.write(FILES_COUNT_LINE.format(count=report['files']))
        self.stdout.write(CHAT_COUNT_LINE)

    def _confirmed(self) -> bool:
        """Ask once. A non-interactive shell counts as "no"."""
        self.stdout.write(PROMPT)
        try:
            answer = input().strip().lower()
        except EOFError:
            return False
        return answer in _CONFIRMATIONS