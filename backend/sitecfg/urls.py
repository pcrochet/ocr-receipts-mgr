from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from ocr.admin_views import ocr_tools

urlpatterns = [
    path("admin/ocr-tools/", admin.site.admin_view(ocr_tools), name="ocr_tools"),
    path("admin/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
