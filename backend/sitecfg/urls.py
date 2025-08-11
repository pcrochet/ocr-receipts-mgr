# backend/sitecfg/urls.py

from django.contrib import admin
from django.urls import path
from ocr.admin_views import ocr_tools

urlpatterns = [
    path("admin/ocr-tools/", admin.site.admin_view(ocr_tools), name="ocr_tools"),
    path("admin/", admin.site.urls),
]
