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
        re_path(r'^.*$', TemplateView.as_view(
            template_name='index.html',
            extra_context={},
        )),
    ]
