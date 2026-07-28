import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rag_project.settings')
os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'
import django; django.setup()

import chromadb
from core.config import settings

# ChromaDB (persistent)
client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR)
collections = client.list_collections()
print(f'=== ChromaDB Collections ({len(collections)}) ===')
for c in collections:
    print(f'  - {c.name}')
    col = client.get_collection(c.name)
    count = col.count()
    print(f'    Count: {count}')
    if count > 0:
        result = col.get(limit=3)
        print(f'    Sample IDs: {result["ids"][:3]}')
        print(f'    Sample docs (first 100 chars):')
        for i, d in enumerate(result['documents'][:3]):
            print(f'      [{i}] {d[:120]}...')
        print(f'    Sample metadatas: {result["metadatas"][:3]}')
        if result['embeddings']:
            emb = result['embeddings'][0]
            print(f'    Embedding dimension: {len(emb)}')
            print(f'    Embedding first 5 values: {emb[:5]}')

# SQLite
print()
print('=== SQLite Documents ===')
from documents.models import Document
docs = Document.objects.all()
for d in docs:
    print(f'  id={d.id}, title={d.title}, status={d.status}, chunks={d.chunk_count}')

print('\nDone.')
