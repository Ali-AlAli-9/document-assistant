from ninja import NinjaAPI
from django.urls import path
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.db import connection
from ninja.security import django_auth
from api.endpoints import documents, chat, auth
from api.endpoints import settings as settings_module
from application.di import get_embedder_status

api = NinjaAPI(title='RAG API', version='1.0.0')

# PROBLEM 7 — MANDATORY registration: /auth is the ONLY public router. Chat,
# documents and settings all require a logged-in Django session (django_auth).
api.add_router('/auth', auth.router, tags=['Auth'])
api.add_router('/documents', documents.router, auth=django_auth, tags=['Documents'])
api.add_router('/chat', chat.router, auth=django_auth, tags=['Chat'])
api.add_router('/settings', settings_module.router, auth=django_auth, tags=['Settings'])

@ensure_csrf_cookie
def get_csrf_token(request):
    return JsonResponse({'detail': 'CSRF cookie set'})

def health_check(request):
    """Public, because the platform's health check needs it — so it must say the
    minimum. ``get_embedder_status()['error']`` is a raw ``str(exc)`` captured
    while loading the model (see application.di), which can carry filesystem
    paths and model configuration; none of that belongs in an unauthenticated
    response."""
    try:
        connection.ensure_connection()
        db_ok = True
    except Exception:
        db_ok = False
    embedder = get_embedder_status()
    return JsonResponse({
        'status': 'ok' if db_ok else 'degraded',
        'database': 'ok' if db_ok else 'error',
        'embedder_state': embedder.get('state', 'unknown'),
    })

def system_status(request):
    """Embedder state for the setup wizard, which polls this from the moment the
    app loads — i.e. before anyone is logged in.

    Returning the same ``{'embedder': {'state': ...}}`` shape either way keeps the
    frontend working unchanged, but the ``error`` string is withheld from
    anonymous callers: ``get_embedder_status()['error']`` is a raw ``str(exc)``
    captured while loading the model (see application.di), and that can carry
    filesystem paths, model configuration and library versions. The wizard only
    ever reads ``state``, so nothing is lost by dropping it.
    """
    embedder = get_embedder_status()
    if not request.user.is_authenticated:
        return JsonResponse({'embedder': {'state': embedder.get('state', 'unknown')}})
    return JsonResponse({
        'embedder': embedder,
    })

urlpatterns = [
    path('csrf/', get_csrf_token, name='csrf'),
    path('health/', health_check, name='health'),
    path('status/', system_status, name='status'),
    path('', api.urls),
]
