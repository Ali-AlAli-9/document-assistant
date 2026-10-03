from unittest.mock import patch, MagicMock

import pytest
from domain.entities.document import Document, DocumentStatus
from domain.entities.chunk import Chunk
from application.commands.upload_document import (
    UploadDocumentCommand,
    UploadDocumentHandler,
    _looks_visually_reversed_arabic,
    _normalize_pdf_arabic,
    _reverse_visual_order_line,
)
from core.exceptions import FileNotAllowedError, IngestionError


pytestmark = pytest.mark.filterwarnings('ignore::DeprecationWarning')


class TestUploadDocumentHandler:
    def test_upload_successful(self, mock_repo, mock_vector_store, mock_embedder):
        mock_embedder.encode.return_value = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        handler = UploadDocumentHandler(mock_repo, mock_vector_store, mock_embedder)

        with patch.object(handler, '_extract_text', return_value='chunk1 chunk2'):
            with patch.object(handler, '_chunk_text', return_value=['chunk1', 'chunk2']):
                result = handler.handle(
                    UploadDocumentCommand(title='test.pdf', file_path='/tmp/test.pdf', file_size=100)
                )

        assert result.status == DocumentStatus.READY
        assert result.chunk_count == 2
        assert mock_vector_store.add.called
        call_args = mock_vector_store.add.call_args[0][0]
        assert len(call_args) == 2
        assert all(isinstance(c, Chunk) for c in call_args)

    def test_invalid_file_extension(self, mock_repo, mock_vector_store, mock_embedder):
        handler = UploadDocumentHandler(mock_repo, mock_vector_store, mock_embedder)
        with pytest.raises(FileNotAllowedError, match='not allowed'):
            handler.handle(
                UploadDocumentCommand(title='test.exe', file_path='/tmp/test.exe', file_size=100)
            )

    def test_ingestion_failure_marks_document_failed(self, mock_repo, mock_vector_store, mock_embedder):
        handler = UploadDocumentHandler(mock_repo, mock_vector_store, mock_embedder)

        with patch.object(handler, '_extract_text', return_value='text'):
            with patch.object(handler, '_chunk_text', side_effect=Exception('chunk error')):
                with pytest.raises(IngestionError):
                    handler.handle(
                        UploadDocumentCommand(title='test.txt', file_path='/tmp/test.txt', file_size=50)
                    )

        # verify save was called with failed status
        save_calls = mock_repo.save.call_args_list
        last_saved = save_calls[-1][0][0]
        assert last_saved.status == DocumentStatus.FAILED

    def test_upload_empty_file(self, mock_repo, mock_vector_store, mock_embedder):
        handler = UploadDocumentHandler(mock_repo, mock_vector_store, mock_embedder)

        with patch.object(handler, '_extract_text', return_value=''):
            with patch.object(handler, '_chunk_text', return_value=[]):
                result = handler.handle(
                    UploadDocumentCommand(title='empty.txt', file_path='/tmp/empty.txt', file_size=0)
                )

        assert result.status == DocumentStatus.READY
        assert result.chunk_count == 0
        mock_vector_store.add.assert_called_once_with([])

    def test_repo_save_called_for_status_transitions(self, mock_repo, mock_vector_store, mock_embedder):
        mock_repo.save.side_effect = None
        docs = {}
        def fake_save(doc):
            saved = Document(
                id=len(docs) + 1,
                title=doc.title,
                status=doc.status,
                chunk_count=doc.chunk_count,
                file_path=doc.file_path,
                file_size=doc.file_size,
            )
            docs[saved.id] = saved
            return saved

        mock_repo.save.side_effect = fake_save
        handler = UploadDocumentHandler(mock_repo, mock_vector_store, mock_embedder)

        with patch.object(handler, '_extract_text', return_value='hello'):
            with patch.object(handler, '_chunk_text', return_value=['hello']):
                result = handler.handle(
                    UploadDocumentCommand(title='test.pdf', file_path='/tmp/test.pdf', file_size=100)
                )

        assert result.status == DocumentStatus.READY
        assert result.chunk_count == 1
        assert mock_vector_store.add.called

    def test_extract_text_dispatches_by_extension(self, mock_repo, mock_vector_store, mock_embedder):
        handler = UploadDocumentHandler(mock_repo, mock_vector_store, mock_embedder)
        with pytest.raises(IngestionError, match='Unsupported'):
            handler._extract_text('/tmp/test.xyz')

    def test_chunk_text_returns_empty_for_empty_input(self, mock_repo, mock_vector_store, mock_embedder):
        handler = UploadDocumentHandler(mock_repo, mock_vector_store, mock_embedder)
        chunks = handler._chunk_text('')
        assert chunks == []

    def test_chunk_text_splits_long_text(self, mock_repo, mock_vector_store, mock_embedder):
        handler = UploadDocumentHandler(mock_repo, mock_vector_store, mock_embedder)
        text = ' '.join(['word'] * 2000)
        chunks = handler._chunk_text(text)
        assert len(chunks) > 0
        assert all(len(c) <= 500 for c in chunks)


class TestArabicPdfOrderNormalization:
    """Arabic PDFs stored in visual order come out letter-reversed with pypdf.

    These guard `_normalize_pdf_arabic`: visual-order text is detected and
    rebuilt to logical order, normal text is never touched, Latin/digit runs
    survive intact and pypdf artifacts (NUL, private-use glyphs) are removed.
    """

    LOGICAL = 'في الكيمياء العضوية من أفضل الكتب على الإطلاق، صف تاسع 2023'

    def test_detects_visually_reversed_arabic(self, mock_repo, mock_vector_store, mock_embedder):
        visual = _reverse_visual_order_line(self.LOGICAL)
        assert visual != self.LOGICAL
        assert _looks_visually_reversed_arabic(visual) is True
        assert _looks_visually_reversed_arabic(self.LOGICAL) is False

    def test_reversed_arabic_restored_to_logical_order(self, mock_repo, mock_vector_store, mock_embedder):
        visual = _reverse_visual_order_line(self.LOGICAL)
        restored = _normalize_pdf_arabic(visual)
        assert restored == self.LOGICAL

    def test_digits_and_latin_runs_kept_intact(self, mock_repo, mock_vector_store, mock_embedder):
        # Numbers inside a visual-order line must come back in reading order.
        visual = _reverse_visual_order_line(self.LOGICAL)
        restored = _normalize_pdf_arabic(visual)
        assert '2023' in restored
        assert '3202' not in restored

    def test_normal_arabic_untouched(self, mock_repo, mock_vector_store, mock_embedder):
        assert _normalize_pdf_arabic(self.LOGICAL) == self.LOGICAL

    def test_non_arabic_text_untouched(self, mock_repo, mock_vector_store, mock_embedder):
        latin = 'Introduction to organic chemistry 2023\nFormula: C6H12O6'
        assert _normalize_pdf_arabic(latin) == latin

    def test_latin_lines_skipped_in_mixed_text(self, mock_repo, mock_vector_store, mock_embedder):
        visual = _reverse_visual_order_line(self.LOGICAL)
        mixed = f'{visual}\npure latin line 2023\n{visual}'
        fixed = _normalize_pdf_arabic(mixed)
        lines = fixed.split('\n')
        assert lines[0] == self.LOGICAL
        assert lines[1] == 'pure latin line 2023'
        assert lines[2] == self.LOGICAL

    def test_pypdf_artifacts_stripped(self, mock_repo, mock_vector_store, mock_embedder):
        dirty = f'في\x00الكيمياء\uf0c1 العضوية'
        fixed = _normalize_pdf_arabic(dirty)
        assert '\x00' not in fixed
        assert '\uf0c1' not in fixed
        assert '\u0600' <= fixed[0] <= '\u06FF'  # still coherent Arabic
