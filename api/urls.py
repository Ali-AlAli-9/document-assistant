from ninja import NinjaAPI
from django.urls import path
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.db import connection
from api.endpoints import documents, chat
from api.endpoints import settings as settings_module
from application.di import get_embedder_status

api = NinjaAPI(title='RAG API', version='1.0.0')

api.add_router('/documents', documents.router, tags=['Documents'])
api.add_router('/chat', chat.router, tags=['Chat'])
api.add_router('/settings', settings_module.router, tags=['Settings'])

@ensure_csrf_cookie
def get_csrf_token(request):
    return JsonResponse({'detail': 'CSRF cookie set'})

def health_check(request):
    try:
        connection.ensure_connection()
        db_ok = True
    except Exception:
        db_ok = False
    embedder = get_embedder_status()
    return JsonResponse({
        'status': 'ok' if db_ok else 'degraded',
        'database': 'ok' if db_ok else 'error',
        'embedder': embedder,
    })

def system_status(request):
    embedder = get_embedder_status()
    return JsonResponse({
        'embedder': embedder,
    })

urlpatterns = [
    path('csrf/', get_csrf_token, name='csrf'),
    path('health/', health_check, name='health'),
    path('status/', system_status, name='status'),
    path('', api.urls),
]
