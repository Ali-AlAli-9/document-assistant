import logging
import os
import re
import uuid
import threading
from ninja import Router, File, Schema
from ninja.files import UploadedFile
from ninja.responses import Response

from application.di import get_vector_store
from core.config import settings
from core.exceptions import VectorStoreError
from domain.entities.document import Document
from infrastructure.vector_store import writer_lock

logger = logging.getLogger(__name__)

router = Router()
_upload_lock = threading.Lock()


def _get_repo():
    from application.di import get_document_repo
    return get_document_repo()


def _sanitize_filename(name: str) -> str:
    name = re.sub(r'[^\w\s\-.]', '_', name)
    name = re.sub(r'\s+', '_', name)
    name = name.strip('_.')
    if not name:
        name = 'unnamed'
    return name


class DocumentOut(Schema):
    id: int
    title: str
    status: str
    chunk_count: int
    file_size: int
    uploaded_at: str


@router.get('', response=list[DocumentOut])
def list_documents(request):
    try:
        docs = _get_repo().get_all(owner_id=request.user.pk)
    except Exception:
        logger.exception('Failed to list documents')
        return Response({'detail': 'Database error'}, status=500)
    return [
        DocumentOut(
            id=d.id,
            title=d.title,
            status=d.status.value,
            chunk_count=d.chunk_count,
            file_size=d.file_size,
            uploaded_at=d.uploaded_at.isoformat() if d.uploaded_at else '',
        )
        for d in docs
    ]


@router.post('/upload', response={201: dict})
def upload_document(request, file: UploadedFile = File(...)):
    if not _upload_lock.acquire(blocking=False):
        return Response({'detail': 'يرجى الانتظار حتى انتهاء الرفع الحالي'}, status=409)
    try:
        ext = os.path.splitext(file.name)[1].lower()
        if ext not in settings.ALLOWED_EXTENSIONS:
            return Response({'detail': f'Extension {ext} not allowed'}, status=400)
        if file.size > settings.MAX_UPLOAD_SIZE:
            return Response({'detail': f'File too large. Max {settings.MAX_UPLOAD_SIZE // (1024*1024)}MB'}, status=413)

        safe_name = _sanitize_filename(file.name)
        prefixed_name = f'{uuid.uuid4().hex[:8]}_{safe_name}'

        from django.core.files.storage import default_storage
        path = default_storage.save(f'uploads/{prefixed_name}', file)
        full_path = default_storage.path(path)

        repo = _get_repo()
        document = repo.save(
            Document(
                title=safe_name,
                file_path=full_path,
                file_size=file.size,
            ),
            owner_id=request.user.pk,
        )
        try:
            from application.di import get_upload_handler
            handler = get_upload_handler()
            document = handler.handle_by_id(document.id)
        except writer_lock.VectorStoreBusy as exc:
            # Another process is writing to the store. The document is left
            # pending so the upload can be retried once the lock frees —
            # marking it failed would both be a lie and block the retry button.
            logger.warning('Upload of %s deferred: %s', safe_name, exc)
            return Response({'detail': str(exc)}, status=409)
        except Exception:
            logger.exception('Failed to process document synchronously')
            try:
                document.mark_failed()
                repo.save(document)
            except Exception:
                logger.exception('Failed to mark document as failed')
        return {'detail': 'Upload complete', 'id': document.id, 'title': safe_name, 'status': document.status.value}
    finally:
        _upload_lock.release()


@router.get('/{doc_id}', response=DocumentOut)
def get_document(request, doc_id: int):
    # owner_id: a document owned by someone else must be indistinguishable
    # from one that never existed, so this returns 404 and never 403.
    try:
        doc = _get_repo().get_by_id(doc_id, owner_id=request.user.pk)
    except Exception:
        logger.exception('Failed to get document')
        return Response({'detail': 'Database error'}, status=500)
    if not doc:
        return Response({'detail': 'Not found'}, status=404)
    return DocumentOut(
        id=doc.id,
        title=doc.title,
        status=doc.status.value,
        chunk_count=doc.chunk_count,
        file_size=doc.file_size,
        uploaded_at=doc.uploaded_at.isoformat() if doc.uploaded_at else '',
    )


@router.post('/{doc_id}/retry', response={200: dict})
def retry_document_endpoint(request, doc_id: int):
    try:
        doc = _get_repo().get_by_id(doc_id, owner_id=request.user.pk)
    except Exception:
        logger.exception('Failed to get document')
        return Response({'detail': 'Database error'}, status=500)
    if not doc:
        return Response({'detail': 'Not found'}, status=404)
    if doc.status.value != 'failed':
        return Response({'detail': 'Only failed documents can be retried'}, status=400)
    try:
        from application.di import get_upload_handler
        handler = get_upload_handler()
        doc = handler.retry_by_id(doc.id)
    except writer_lock.VectorStoreBusy as exc:
        # Still failed, so the user can try again the moment the lock frees.
        return Response({'detail': str(exc)}, status=409)
    except Exception:
        logger.exception('Failed to retry document')
        return Response({'detail': 'فشلت إعادة معالجة المستند'}, status=500)
    return {'detail': 'تمت إعادة المعالجة بنجاح', 'title': doc.title, 'doc_id': doc.id}


@router.delete('/{doc_id}', response={204: None})
def delete_document(request, doc_id: int):
    # The vector store is keyed by document id, so a document that is not this
    # account's must never reach the delete — otherwise another user's chunks
    # disappear from the shared collection.
    owned = _get_repo().get_by_id(doc_id, owner_id=request.user.pk)
    if not owned:
        return Response({'detail': 'Not found'}, status=404)
    # Vectors BEFORE the row. Chunks are the only part that cannot be rebuilt
    # from the uploaded file, so they go first; if the store refuses, the row
    # survives and the user can simply try again. The reverse order would leave
    # chunks that nothing points at, with no record that they ever existed.
    try:
        with writer_lock.vector_store_writer(f'delete: {owned.title}'):
            get_vector_store().delete_by_document(doc_id)
    except writer_lock.VectorStoreBusy as exc:
        return Response({'detail': str(exc)}, status=409)
    except VectorStoreError as e:
        logger.warning(f'Vector store cleanup failed for doc {doc_id}: {e}')
    except Exception:
        logger.warning(f'Vector store cleanup failed for doc {doc_id}')
    try:
        _get_repo().delete(doc_id, owner_id=request.user.pk)
    except Exception:
        logger.exception('Failed to delete document from DB')
        return Response({'detail': 'Database error'}, status=500)


