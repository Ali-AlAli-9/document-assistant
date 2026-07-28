import os
import logging
from domain.entities.document import Document, DocumentStatus
from domain.ports.document_repository import DocumentRepositoryPort
from documents.models import Document as DocumentModel
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)


class DjangoDocumentRepository(DocumentRepositoryPort):
    def _to_entity(self, model: DocumentModel) -> Document:
        return Document(
            id=model.id,
            title=model.title,
            file_path=model.file.path if model.file else '',
            status=DocumentStatus(model.status),
            chunk_count=model.chunk_count,
            file_size=model.file_size,
            uploaded_at=model.uploaded_at,
        )

    def save(self, document: Document) -> Document:
        if document.id:
            try:
                model = DocumentModel.objects.get(id=document.id)
            except DocumentModel.DoesNotExist:
                logger.error(f'Document {document.id} not found for update')
                raise
            model.status = document.status.value
            model.chunk_count = document.chunk_count
            model.save()
        else:
            model = DocumentModel.objects.create(
                title=document.title,
                status=document.status.value,
                file_size=document.file_size,
            )
            if document.file_path and os.path.exists(document.file_path):
                try:
                    with open(document.file_path, 'rb') as f:
                        model.file.save(os.path.basename(document.file_path), ContentFile(f.read()))
                    model.file_size = os.path.getsize(model.file.path) if model.file else document.file_size
                    model.save(update_fields=['file_size'])
                except Exception:
                    logger.exception(f'Failed to save file for document {document.id}')
                    raise
                else:
                    try:
                        os.remove(document.file_path)
                    except OSError:
                        logger.warning(f'Could not remove temp file {document.file_path}')
        return self._to_entity(model)

    def get_by_id(self, doc_id: int) -> Document | None:
        try:
            return self._to_entity(DocumentModel.objects.get(id=doc_id))
        except DocumentModel.DoesNotExist:
            return None

    def get_all(self) -> list[Document]:
        return [self._to_entity(d) for d in DocumentModel.objects.all()]

    def delete(self, doc_id: int) -> None:
        try:
            model = DocumentModel.objects.get(id=doc_id)
            if model.file:
                model.file.delete(save=False)
            model.delete()
        except DocumentModel.DoesNotExist:
            pass
