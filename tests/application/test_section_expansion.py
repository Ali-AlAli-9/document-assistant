"""Section expansion for RAG breadth questions.

Measured on the demo corpus: asking "\u0627\u0628\u062D\u062B \u0641\u064A \u0643\u0644 \u0627\u0644\u0641\u0635\u0648\u0644 \u0639\u0646
\u0627\u0644\u0635\u0639\u0648\u0628\u0627\u062A \u0648\u0627\u0644\u062A\u062D\u062F\u064A\u0627\u062A" retrieved the section
heading (chunk 197: "4-4 \u0627\u0644\u0635\u0639\u0648\u0628\u0627\u062A \u0648\u0627\u0644\u062A\u062D\u062F\u064A\u0627\u062A") but
none of the section body, so the LLM could cite at most the single paragraph
that was retrieved, and "all the challenges" stayed out of the answer.
Expanding each section-anchor chunk with its following sibling chunks (in
reading order) puts the section body into context.

All non-ASCII fixtures use \\u escapes (the write path mangled literal Arabic).
"""
import pytest

from application.commands.ask_question import (
    AskQuestionCommand,
    AskQuestionHandler,
    _is_section_anchor,
)
from domain.ports.vector_store import SearchResult

# "4-4 الصعوبات والتحديات"
HEADING_NUMBERED = '4-4 \u0627\u0644\u0635\u0639\u0648\u0628\u0627\u062A \u0648\u0627\u0644\u062A\u062D\u062F\u064A\u0627\u062A'
# "الفصل الرابع"
HEADING_CHAPTER = '\u0627\u0644\u0641\u0635\u0644 \u0627\u0644\u0631\u0627\u0628\u0639'
# "تتأثر عمليات التعرف بتغير شدة الإضاءة واتجاهها."
PROSE_AR = ('\u062A\u062A\u0623\u062B\u0631 \u0639\u0645\u0644\u064A\u0627\u062A \u0627\u0644\u062A\u0639\u0631\u0641 '
            '\u0628\u062A\u063A\u064A\u0631 \u0634\u062F\u0629 \u0627\u0644\u0625\u0636\u0627\u0621\u0629 '
            '\u0648\u0627\u062A\u062C\u0627\u0647\u0647\u0627.')
# فهرس row: "3-6-2 شرط الثبات وبناء التسلسل ...... 55"
TOC_ROW = ('3-6-2 \u0634\u0631\u0637 \u0627\u0644\u062B\u0628\u0627\u062A \u0648\u0628\u0646\u0627\u0621 '
           '\u0627\u0644\u062A\u0633\u0644\u0633\u0644' + ' ' + '.' * 40 + ' 55')
# "الشكل 3-3): منطق تحميل البصمة"
FIGURE_CAPTION = ('\u0627\u0644\u0634\u0643\u06443- 3): \u0645\u0646\u0637\u0642 '
                  '\u062A\u062D\u0645\u064A\u0644 \u0627\u0644\u0628\u0635\u0645\u0629')
# "4-4-1 تغير ظروف الإضاءة"
SUB_HEADING = '4-4-1 \u062A\u063A\u064A\u0631 \u0638\u0631\u0648\u0641 \u0627\u0644\u0625\u0636\u0627\u0621\u0629'


class TestIsSectionAnchor:
    def test_numbered_heading_is_anchor(self):
        assert _is_section_anchor(HEADING_NUMBERED) is True

    def test_chapter_heading_is_anchor(self):
        assert _is_section_anchor(HEADING_CHAPTER) is True

    def test_sub_sub_numbered_heading_is_anchor(self):
        assert _is_section_anchor(SUB_HEADING) is True

    def test_toc_dotted_row_is_not_anchor(self):
        assert _is_section_anchor(TOC_ROW) is False

    def test_figure_caption_is_not_anchor(self):
        assert _is_section_anchor(FIGURE_CAPTION) is False

    def test_prose_is_not_anchor(self):
        assert _is_section_anchor(PROSE_AR) is False

    def test_prose_number_then_word_is_not_anchor(self):
        # "2023 لأغراض ضبط" — a bare number is not a numbered heading.
        assert _is_section_anchor('2023 \u0644\u0623\u063A\u0631\u0627\u0636 \u0636\u0628\u0637') is False

    def test_heading_within_prose_chunk_is_anchor(self):
        # Demo chunk 197 starts with the tail of the previous section, then
        # shows the heading mid-chunk; the chunk must still expand.
        chunk = '\n'.join([PROSE_AR, HEADING_CHAPTER, HEADING_NUMBERED, SUB_HEADING])
        assert _is_section_anchor(chunk) is True

    def test_math_ellipsis_is_not_anchor(self):
        assert _is_section_anchor('E = [e1, e2, e3, ... , ed]') is False

    def test_empty_is_not_anchor(self):
        assert _is_section_anchor('') is False


def make_result(index, content, score=0.9):
    return SearchResult(
        content=content,
        score=score,
        metadata={'doc_id': '27', 'source': 'final.pdf', 'index': index, 'chunk_index': index},
    )


class TestExpandSections:
    def test_anchor_pulls_following_siblings(self, mock_vector_store, mock_embedder, mock_llm):
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)
        anchor = make_result(197, HEADING_NUMBERED)
        mock_vector_store.fetch_chunks.return_value = [
            make_result(198, PROSE_AR, score=0.0),
            make_result(199, PROSE_AR, score=0.0),
        ]

        out = handler._expand_sections(27, [anchor])

        assert [r.metadata['index'] for r in out] == [197, 198, 199]
        # expansions inherit the anchor score so the merged ranking keeps them
        # next to their anchor
        assert all(r.score == 0.9 for r in out[1:])
        mock_vector_store.fetch_chunks.assert_called_once_with(27, [198, 199, 200])

    def test_neighbor_already_retrieved_is_deduplicated(self, mock_vector_store, mock_embedder, mock_llm):
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)
        anchor = make_result(197, HEADING_NUMBERED)
        sibling = make_result(198, PROSE_AR)
        mock_vector_store.fetch_chunks.return_value = [
            make_result(198, PROSE_AR, score=0.0),
            make_result(199, PROSE_AR, score=0.0),
        ]

        out = handler._expand_sections(27, [anchor, sibling])

        indices = [r.metadata['index'] for r in out]
        assert indices.count(198) == 1
        assert indices.count(199) == 1
        assert len(out) == 3

    def test_overlapping_anchor_neighborhoods_are_deduplicated(self, mock_vector_store, mock_embedder, mock_llm):
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)
        anchor_a = make_result(197, HEADING_NUMBERED)
        anchor_b = make_result(200, SUB_HEADING)
        mock_vector_store.fetch_chunks.side_effect = [
            [make_result(198, PROSE_AR, score=0.0), make_result(199, PROSE_AR, score=0.0)],
            [make_result(201, PROSE_AR, score=0.0)],
        ]

        out = handler._expand_sections(27, [anchor_a, anchor_b])

        indices = [r.metadata['index'] for r in out]
        assert indices == [197, 200, 198, 199, 201]

    def test_non_anchor_result_is_untouched(self, mock_vector_store, mock_embedder, mock_llm):
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)
        out = handler._expand_sections(27, [make_result(5, PROSE_AR)])
        assert len(out) == 1
        mock_vector_store.fetch_chunks.assert_not_called()

    def test_expand_setting_zero_disables_expansion(self, mock_vector_store, mock_embedder, mock_llm, monkeypatch):
        monkeypatch.setattr('core.config.settings.RETRIEVAL_SECTION_EXPAND', 0)
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)
        out = handler._expand_sections(27, [make_result(197, HEADING_NUMBERED)])
        assert len(out) == 1
        mock_vector_store.fetch_chunks.assert_not_called()

    def test_anchor_without_index_is_skipped(self, mock_vector_store, mock_embedder, mock_llm):
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)
        r = SearchResult(content=HEADING_NUMBERED, score=0.9, metadata={})
        out = handler._expand_sections(27, [r])
        assert len(out) == 1
        mock_vector_store.fetch_chunks.assert_not_called()

    def test_total_context_cap_is_honoured(self, mock_vector_store, mock_embedder, mock_llm, monkeypatch):
        monkeypatch.setattr('core.config.settings.RETRIEVAL_MAX_CONTEXT_CHUNKS', 4)
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)
        mock_vector_store.fetch_chunks.return_value = [
            make_result(i, PROSE_AR, score=0.0) for i in (198, 200, 202, 204, 206)
        ]

        out = handler._expand_sections(27, [make_result(197, HEADING_NUMBERED)])

        assert len(out) <= 4

    def test_retrieve_puts_section_body_into_prompt(self, mock_vector_store, mock_embedder, mock_llm):
        mock_vector_store.search.return_value = [make_result(197, HEADING_NUMBERED)]
        mock_vector_store.fetch_chunks.return_value = [
            make_result(198, 'BODY LIGHTING DETAIL'),
            make_result(199, 'BODY THREAD DETAIL'),
        ]
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)

        result = handler.handle(AskQuestionCommand(question='\u0627\u0628\u062D\u062B \u0639\u0646 \u0627\u0644\u0635\u0639\u0648\u0628\u0627\u062A'))

        prompt_arg = mock_llm.generate.call_args[0][0]
        assert HEADING_NUMBERED in prompt_arg
        assert 'BODY LIGHTING DETAIL' in prompt_arg
        assert 'BODY THREAD DETAIL' in prompt_arg
        # the body chunks surface as sources too
        assert any(s['content'].startswith('BODY LIGHTING') for s in result.sources)
        assert any(s['content'].startswith('BODY THREAD') for s in result.sources)