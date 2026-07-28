from unittest.mock import patch, MagicMock

import pytest
from domain.entities.document import Document, DocumentStatus
from domain.entities.chunk import Chunk
from application.commands.upload_document import (
    UploadDocumentCommand,
    UploadDocumentHandler,
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

    @pytest.mark.skip(reason='requires langchain which is not installed')
    def test_chunk_text_returns_empty_for_empty_input(self, mock_repo, mock_vector_store, mock_embedder):
        handler = UploadDocumentHandler(mock_repo, mock_vector_store, mock_embedder)
        chunks = handler._chunk_text('')
        assert chunks == []

    @pytest.mark.skip(reason='requires langchain which is not installed')
    def test_chunk_text_splits_long_text(self, mock_repo, mock_vector_store, mock_embedder):
        handler = UploadDocumentHandler(mock_repo, mock_vector_store, mock_embedder)
        text = ' '.join(['word'] * 2000)
        chunks = handler._chunk_text(text)
        assert len(chunks) > 0
        assert all(len(c) <= 500 for c in chunks)
