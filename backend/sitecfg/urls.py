# backend/sitecfg/urls.py

from django.contrib import admin
from django.urls import path
from ocr.admin_views import receipts_management, run_ingest_from_dir

urlpatterns = [
    # 1) Nos vues "admin custom" en premier
path("admin/receipts-management/", admin.site.admin_view(receipts_management), name="receipts_management"),
path("admin/receipts-management/run/ingest/", admin.site.admin_view(run_ingest_from_dir), name="ocr_run_ingest_from_dir"),

    # 2) Puis l'admin Django (qui a un catch-all)
    path("admin/", admin.site.urls),
]
