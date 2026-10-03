from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from pathlib import Path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('api.urls')),
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

frontend_dist = Path(__file__).resolve().parent.parent / 'frontend' / 'dist'
if (frontend_dist / 'index.html').exists():
    from django.views.generic import TemplateView
    urlpatterns += [
        # Single-page app: it has no client-side router, so every real UI route
        # is the app root. The negative lookaheads keep a *misspelled* API
        # path or a *missing* asset a real 404 — a missing /static/app.js that
        # returned index.html with a 200 would surface in the browser console
        # only as "Unexpected token '<'", which is far harder to diagnose.
        re_path(
            r'^(?!api/|admin/|static/|media/)(?!.*\.[A-Za-z0-9]{2,5}$).*$',
            TemplateView.as_view(
                template_name='index.html',
                extra_context={},
            ),
        ),
    ]
