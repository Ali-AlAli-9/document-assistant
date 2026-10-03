"""``manage.py verify_consistency`` — do the three stores still agree?

THE QUESTION
============
Nothing guarantees that these agree:

* ``documents_document`` — rows with a file reference and a ``chunk_count``,
* ``media/uploads/`` — the uploaded files,
* Chroma — the embedded chunks that retrieval actually searches.

A crash between two writes leaves them disagreeing, and every symptom is
silent: retrieval returns nothing for a document whose row says "ready", the
list shows a file that will not open, or the collection grows holding chunks
for documents that no longer exist. ``chunk_count`` cannot detect any of this —
it is the database grading its own homework.

WHY IT EXITS NON-ZERO
=====================
Because the useful outcome is not the text, it is the status. A checker that
only prints is read once by a human; one that returns a failing exit code can
gate a deployment or a cron job. A check that could not RUN is reported as a
failure too — a green result that was never verified is the most dangerous
output this command can produce.
"""

import sys

from django.core.management.base import BaseCommand

from application.services import consistency

ALL_CONSISTENT = (
    "\u0644\u0627 \u0645\u0634\u0627\u0643\u0644: "
    "\u0627\u0644\u0645\u0644\u0641\u0627\u062a \u0648\u0642\u0627\u0639\u062f\u0629 "
    "\u0627\u0644\u0645\u062a\u062c\u0647\u0627\u062a \u0648\u0645\u062d\u062a\u0648\u0649 "
    "\u0627\u0644\u0645\u062e\u0632\u0646 \u0645\u062a\u0633\u0642\u0629."
)
PROBLEMS_FOUND = "\u062a\u0645 \u0625\u0646\u0647\u0627\u0621 \u0627\u0644\u0641\u062d\u0635 \u0645\u0639 \u0645\u0634\u0627\u0643\u0644."
MISSING_FILE_HEADER = "\u0645\u0644\u0641 \u0645\u0641\u0642\u0648\u062f (\u0647\u0646\u0627\u0643 \u0633\u0637\u0631 \u0644\u0643\u0646 \u0627\u0644\u0645\u0644\u0641):"
ORPHAN_FILE_HEADER = "\u0645\u0644\u0641 \u063a\u064a\u0631 \u0645\u0631\u062a\u0628\u0637 \u0628\u0645\u0633\u062a\u0646\u062f:"
CHUNK_MISMATCH_HEADER = (
    "\u0639\u062f\u062f \u0627\u0644\u0645\u0642\u0627\u0637\u0639 "
    "\u0645\u062e\u062a\u0644\u0641 (\u0627\u0644\u0645\u062d\u0641\u0648\u0638 "
    "\u0645\u0642\u0627\u0628\u0644 \u0627\u0644\u0645\u062e\u0632\u0646):"
)
SKIPPED_HEADER = "\u062a\u0639\u0630\u0631 \u0627\u0644\u062a\u062d\u0642\u0642:"

MISSING_FILE_ROW = "  - {title} (id={doc_id}, file={name})"
ORPHAN_FILE_ROW = "  - {name}"
CHUNK_MISMATCH_ROW = "  - {title} (id={doc_id}): recorded {declared}, stored {stored}"
SKIPPED_ROW = "  - {name}"


class Command(BaseCommand):
    help = (
        'Compare the document rows, the stored files and the vector index, and '
        'exit non-zero if they disagree or if a check could not run.'
    )

    def handle(self, *args, **options):
        report = consistency.verify_all()

        if report.missing_files:
            self._write(MISSING_FILE_HEADER, self._missing_lines(report))
        if report.chunk_mismatches:
            self._write(CHUNK_MISMATCH_HEADER, self._mismatch_lines(report))
        if report.orphan_files:
            self._write(ORPHAN_FILE_HEADER, [f'  - {name}' for name in report.orphan_files])
        if report.skipped:
            self._write(SKIPPED_HEADER, [f'  - {name}' for name in report.skipped])

        if report.ok and not report.skipped:
            self.stdout.write(ALL_CONSISTENT)
            return

        self.stderr.write(PROBLEMS_FOUND)
        sys.exit(1)

    def _missing_lines(self, report):
        from documents.models import Document

        titles = dict(Document.objects.filter(id__in=report.missing_files).values_list('id', 'title'))
        names = dict(
            Document.objects.filter(id__in=report.missing_files).values_list('id', 'file')
        )
        return [
            MISSING_FILE_ROW.format(
                doc_id=doc_id,
                title=titles.get(doc_id, ''),
                name=names.get(doc_id, ''),
            )
            for doc_id in report.missing_files
        ]

    def _mismatch_lines(self, report):
        return [
            CHUNK_MISMATCH_ROW.format(
                doc_id=doc_id,
                title=title,
                declared=declared,
                stored=stored,
            )
            for doc_id, title, declared, stored in report.chunk_mismatches
        ]

    def _write(self, header, lines):
        self.stdout.write(header)
        for line in lines:
            self.stdout.write(line)