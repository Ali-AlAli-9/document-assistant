"""Table-of-contents chunks must never reach the retriever.

Measured on the real corpus: 37 of 265 chunks were pure فهرس lines
("1-5 أهداف المشروع .............."). They are near-exact matches for any
question that reuses a section title, so they silently outranked the section
body and pushed the answer out of the top-K. The filter is deliberately
conservative: a chunk is dropped only when EVERY non-empty line is a
dotted-leader line, so prose (including math and bibliography that contain
ellipses or initials) always survives.

Speed note: importing ``langchain_text_splitters`` costs ~145s on this host
(the first import pulls langchain's dependency tree), which dominates the
whole suite's runtime. Like ``test_embedder_loading`` does for
sentence-transformers, this file injects a stand-in module into ``sys.modules``
so ``_chunk_text``'s lazy ``from langchain_text_splitters import ...`` resolves
to a tiny fake splitter. The tests assert OUR filter behavior; langchain's
own chunk-merge algorithm is not what is under test here.

All non-ASCII fixtures are written as \\u escapes: the write path on this host
has mangled literal Arabic before.
"""

import sys
import types

import pytest

from application.commands.upload_document import (
    UploadDocumentHandler,
    _is_toc_only_chunk,
)

pytestmark = pytest.mark.filterwarnings('ignore::DeprecationWarning')

# '1-5 أهداف المشروع' + dotted leader
TOC_HEADING = '1-5 \u0623\u0647\u062F\u0627\u0641 \u0627\u0644\u0645\u0634\u0631\u0648\u0639'
TOC_LINE = TOC_HEADING + ' ' + '.' * 40
# 'يهدف المشروع إلى تصميم خزنة ذكية آمنة.'
PROSE_AR = (
    '\u064A\u0647\u062F\u0641 \u0627\u0644\u0645\u0634\u0631\u0648\u0639 \u0625\u0644\u0649 '
    '\u062A\u0635\u0645\u064A\u0645 \u062E\u0632\u0646\u0629 \u0630\u0643\u064A\u0629 \u0622\u0645\u0646\u0629.'
)


class _FakeSplitter:
    """Minimal RecursiveCharacterTextSplitter stand-in.

    Splits on \\n\\n and greedily merges segments up to chunk_size, the two
    behaviors the ingestion tests rely on. No langchain import => no 145s.
    """

    def __init__(self, chunk_size=500, chunk_overlap=50):
        self.chunk_size = chunk_size

    def split_text(self, text: str) -> list:
        segments = [s for s in text.split('\n\n') if s]
        chunks, buf = [], ''
        for s in segments:
            if buf and len(buf) + len(s) + 2 > self.chunk_size:
                chunks.append(buf)
                buf = ''
            buf = s if not buf else buf + '\n\n' + s
        if buf:
            chunks.append(buf)
        return chunks


@pytest.fixture(autouse=True)
def _serve_langchain_without_importing_it(monkeypatch):
    """Make ``_chunk_text``'s lazy langchain import resolve to the fake."""
    module = types.ModuleType('langchain_text_splitters')
    module.RecursiveCharacterTextSplitter = _FakeSplitter
    monkeypatch.setitem(sys.modules, 'langchain_text_splitters', module)


class TestIsTocOnlyChunk:
    def test_single_toc_line_is_rejected(self):
        assert _is_toc_only_chunk(TOC_LINE) is True

    def test_multi_line_toc_block_is_rejected(self):
        block = '\n'.join([
            '1-1 \u0627\u0644\u0645\u0642\u062F\u0645\u0629' + ' ' + '.' * 40,
            TOC_LINE,
            '1-9 \u062D\u062F\u0648\u062F \u0627\u0644\u0645\u0634\u0631\u0648\u0639' + ' ' + '.' * 40,
        ])
        assert _is_toc_only_chunk(block) is True

    def test_spaced_dots_are_still_leaders(self):
        # some PDFs render the leader as ". . . ."
        spaced = TOC_HEADING + ' ' + '. . . . . . . . . . . . . . . . . . . .'
        assert _is_toc_only_chunk(spaced) is True

    def test_prose_chunk_is_kept(self):
        assert _is_toc_only_chunk(PROSE_AR) is False

    def test_mixed_toc_and_prose_is_kept(self):
        mixed = TOC_LINE + '\n' + PROSE_AR
        assert _is_toc_only_chunk(mixed) is False

    def test_math_with_ellipsis_is_kept(self):
        math = '\n'.join([
            'E = [e1, e2, e3, ... , ed]',
            PROSE_AR,
            PROSE_AR,
        ])
        assert _is_toc_only_chunk(math) is False

    def test_bibliography_initials_are_not_dotted_leaders(self):
        bib = '\n'.join([
            'D., Wieser, E., Taylor, J., Berg, S., Smith, N. J., Kern, R.',
            PROSE_AR,
            PROSE_AR,
        ])
        assert _is_toc_only_chunk(bib) is False

    def test_mixed_index_page_with_page_markers_is_rejected(self):
        # A real فهرس page from the demo corpus: dotted rows + Roman page
        # marker interleaved. Only half the lines are leaders, yet every
        # substantive line is still an index row.
        block = '\n'.join([
            '4-4-9 \u0645\u0639\u0627\u0644\u062C\u0629 \u0646\u0641\u0627\u062F \u0627\u0644\u0645\u062D\u0627\u0648\u0644\u0627\u062A' + ' ' + '.' * 40 + ' 78',
            'VI',
            '4-4-10 \u0627\u0644\u062A\u062D\u0642\u0642 \u0645\u0646 \u0627\u0644\u062D\u0645\u0644 \u0627\u0644\u0643\u0647\u0631\u0628\u0627\u0626\u064A \u0648\u0627\u0644\u062D\u0631\u0627\u0631\u064A' + ' ' + '.' * 40 + ' 78',
            '4-5 \u0627\u0644\u0622\u0641\u0627\u0642 \u0627\u0644\u0645\u0633\u062A\u0642\u0628\u0644\u064A\u0629',
        ])
        assert _is_toc_only_chunk(block) is True

    def test_figure_list_page_is_rejected(self):
        # قائمة الأشكال: rows of "الشكل 3-11) ..... 61" with page markers.
        block = '\n'.join([
            '\u0627\u0644\u0634\u0643\u06443- 11):' + ' .' * 30 + ' 61',
            '\u0627\u0644\u0634\u0643\u06443- 12):' + ' .' * 30 + ' 62',
            'X',
            '\u0627\u0644\u0634\u0643\u06443- 13):' + ' .' * 30 + ' 63',
            '\u0627\u0644\u0634\u0643\u06443- 14):' + ' .' * 30 + ' 64',
        ])
        assert _is_toc_only_chunk(block) is True

    def test_single_dotted_row_in_dominant_prose_is_kept(self):
        block = '\n'.join([
            PROSE_AR,
            PROSE_AR,
            PROSE_AR,
            '\u0634\u0643\u0644' + ' ' + '.' * 40 + ' 12',
        ])
        assert _is_toc_only_chunk(block) is False

    def test_two_dotted_rows_out_of_five_is_kept(self):
        block = '\n'.join([
            TOC_LINE,
            PROSE_AR,
            PROSE_AR,
            PROSE_AR,
            TOC_LINE,
        ])
        assert _is_toc_only_chunk(block) is False

    def test_empty_text_is_kept_by_convention(self):
        assert _is_toc_only_chunk('') is False


class TestChunkTextFiltersToc:
    def _handler(self):
        # _chunk_text uses no dependencies; None wiring is fine for this probe.
        return UploadDocumentHandler(repo=None, vector_store=None, embedder=None)

    def _raw(self, text):
        from core.config import settings
        return _FakeSplitter(
            chunk_size=settings.CHUNK_SIZE,
            chunk_overlap=settings.CHUNK_OVERLAP,
        ).split_text(text)

    def test_toc_only_chunks_are_dropped_and_prose_survives(self):
        toc_lines = [
            f'{i}-{i} \u0627\u0644\u0645\u0642\u062F\u0645\u0629' + ' ' + '.' * 40
            for i in range(1, 27)
        ]
        fihris = '\n\n'.join(toc_lines)
        prose = '\n\n'.join([PROSE_AR] * 30)  # 30 paragraphs -> real chunks
        text = fihris + '\n\n' + prose

        raw = self._raw(text)
        chunks = self._handler()._chunk_text(text)

        dropped = len(raw) - len(chunks)
        assert dropped >= 2, f'expected >=2 pure-index chunks dropped, got {dropped}'
        assert chunks, 'expected some chunks to survive'
        # every surviving chunk must carry prose; none may be pure index
        assert all(not _is_toc_only_chunk(c) for c in chunks)
        # ...and the prose itself must all still be present
        joined = '\n\n'.join(chunks)
        assert PROSE_AR in joined

    def test_chunk_text_with_no_toc_returns_all_chunks(self):
        prose = '\n\n'.join([PROSE_AR] * 30)
        chunks = self._handler()._chunk_text(prose)
        assert chunks
        assert all(not _is_toc_only_chunk(c) for c in chunks)