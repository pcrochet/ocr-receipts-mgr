from contextlib import contextmanager
from django.utils import timezone
from .models import JobRun

@contextmanager
def start_job(job_name: str, *, params: dict | None = None, initiated_by: str = "cli", logger=print):
    jr = JobRun.objects.create(
        job=job_name,
        initiated_by=initiated_by,
        params=params or {},
        status="running",
    )
    prefix = f"[{job_name} #{jr.id}]"
    def log(msg: str):
        logger(f"{prefix} {msg}")

    try:
        yield log, jr
    except Exception as e:
        jr.status = "failed"
        jr.finished_at = timezone.now()
        jr.save(update_fields=["status", "finished_at"])
        log(f"FAILED: {e}")
        raise
