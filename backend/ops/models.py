# backend/ops/models.py

from __future__ import annotations
from django.db import models
from django.db.models import Index
from django.utils import timezone

class JobRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED  = "failed",  "Failed"
        SKIPPED = "skipped", "Skipped"

    job_name     = models.CharField(max_length=64, db_index=True)
    status       = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    triggered_by = models.CharField(max_length=64, blank=True)     # "admin", "cli", "celery", "system", etc.
    params       = models.JSONField(default=dict, blank=True)
    metrics      = models.JSONField(default=dict, blank=True)

    log_path     = models.CharField(max_length=512, blank=True)
    error_message= models.TextField(blank=True)

    started_at   = models.DateTimeField(default=timezone.now, db_index=True)
    finished_at  = models.DateTimeField(null=True, blank=True)

    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "job_runs"
        indexes = [
            Index(fields=["job_name", "status", "started_at"], name="ix_jobruns_main"),
        ]

    @property
    def duration_ms(self) -> int | None:
        if not self.finished_at or not self.started_at:
            return None
        delta = self.finished_at - self.started_at
        return int(delta.total_seconds() * 1000)
