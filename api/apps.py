import atexit
import shutil
import threading
from pathlib import Path

from django.apps import AppConfig


def _cleanup_session():
    try:
        from django.core.files.storage import default_storage
        from documents.models import Document
        from application.di import get_vector_store, clear_conversations

        upload_dir = Path(default_storage.path('uploads'))
        if upload_dir.exists():
            shutil.rmtree(upload_dir, ignore_errors=True)
            print('[cleanup] Deleted media/uploads/')

        count = Document.objects.count()
        Document.objects.all().delete()
        if count:
            print(f'[cleanup] Deleted {count} document(s) from DB')

        try:
            get_vector_store().delete_all()
            print('[cleanup] Cleared ChromaDB collection')
        except Exception:
            pass

        clear_conversations()
        print('[cleanup] Cleared conversations')
    except Exception as e:
        print(f'[cleanup] Error: {e}')


class ApiConfig(AppConfig):
    name = 'api'

    def ready(self):
        from application.di import preload_embedder
        threading.Thread(target=preload_embedder, daemon=True).start()
        atexit.register(_cleanup_session)
