from django.contrib import admin
from .models import JobRun

@admin.register(JobRun)
class JobRunAdmin(admin.ModelAdmin):
    list_display = ("id", "job", "status", "initiated_by", "started_at", "finished_at")
    list_filter = ("job", "status", "initiated_by")
    search_fields = ("id", "job")
    readonly_fields = ("job", "initiated_by", "params", "status", "started_at", "finished_at", "metrics")
