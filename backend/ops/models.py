# backend/ops/models.py

from django.db import models

class JobRun(models.Model):
    STATUS_CHOICES = [
        ("running", "running"),
        ("ok", "ok"),
        ("failed", "failed"),
    ]
    id = models.BigAutoField(primary_key=True)
    job = models.CharField(max_length=64, db_index=True)
    initiated_by = models.CharField(max_length=32, default="cli")  # cli|admin|beat
    params = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="running")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    metrics = models.JSONField(null=True, blank=True)  # ex: counters retournés par le service

    class Meta:
        indexes = [
            models.Index(fields=["job", "status", "started_at"]),
        ]

    def __str__(self):
        return f"{self.job} #{self.id} ({self.status})"
