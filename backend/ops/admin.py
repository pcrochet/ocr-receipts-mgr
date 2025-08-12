# backend/ops/admin.py
from __future__ import annotations
from django.contrib import admin
from .models import JobRun

@admin.register(JobRun)
class JobRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "job_name",
        "status",
        "triggered_by",
        "started_at",
        "finished_at",
        "duration_ms",
        "log_path",
    )
    list_filter = ("job_name", "status", "triggered_by", "started_at")
    search_fields = ("job_name", "params", "metrics", "error_message", "log_path")
    readonly_fields = (
        "job_name",
        "status",
        "triggered_by",
        "params",
        "metrics",
        "log_path",
        "error_message",
        "started_at",
        "finished_at",
        "created_at",
        "duration_ms",  # propriété OK en readonly
    )
    fieldsets = (
        (None, {"fields": ("job_name", "status", "triggered_by", "started_at", "finished_at", "duration_ms")}),
        ("Params & Metrics", {"fields": ("params", "metrics")}),
        ("Logs & Errors", {"fields": ("log_path", "error_message")}),
        ("Meta", {"fields": ("created_at",)}),
    )

    # On empêche la création/suppression depuis l’admin : JobRun est un log d’exécution
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
