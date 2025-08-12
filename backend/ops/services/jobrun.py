# backend/ops/services/jobrun.py
from __future__ import annotations

import hashlib
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, cast, Tuple, Any

from django.conf import settings
from django.db import connection
from django.utils import timezone

from ..models import JobRun


def _var_logs_dir() -> Path:
    var = Path(getattr(settings, "VAR_DIR", Path(settings.BASE_DIR) / "var"))
    d = var / "logs" / "ops"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _job_log_path(job_name: str) -> Path:
    day = timezone.now().strftime("%Y-%m-%d")
    return _var_logs_dir() / f"{job_name}-{day}.log"


def _advisory_key(job_name: str) -> int:
    h = hashlib.sha1(job_name.encode("utf-8")).digest()
    k = int.from_bytes(h[:8], "big", signed=False)
    return k % (2**63 - 1)


def _pg_try_lock(key: int) -> bool:
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", [key])
        row: Optional[Tuple[Any, ...]] = cur.fetchone()
        if not row:
            # Défensif pour Pylance + robustesse
            return False
        return bool(row[0])


def _pg_unlock(key: int) -> bool:
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", [key])
        row: Optional[Tuple[Any, ...]] = cur.fetchone()
        # On renvoie True si on ne sait pas, mais on évite l'indexation sur None
        return bool(row[0]) if row else True


def _metrics_copy(run: JobRun) -> Dict[str, Any]:
    """
    Retourne une COPIE dict des métriques (jamais None / non-dict),
    pour satisfier Pylance et éviter les mutations in-place sur un JSONField.
    """
    m = run.metrics
    if isinstance(m, dict):
        return dict(cast(Dict[str, Any], m))
    return {}


@dataclass
class JobContext:
    run: JobRun
    logger: logging.Logger

    def set_metric(self, key: str, value: Any) -> None:
        m: Dict[str, Any] = _metrics_copy(self.run)
        m[key] = value
        self.run.metrics = m
        self.run.save(update_fields=["metrics"])

    def inc(self, key: str, by: int = 1) -> None:
        m: Dict[str, Any] = _metrics_copy(self.run)
        current = int(m.get(key, 0) or 0)
        m[key] = current + by
        self.run.metrics = m
        self.run.save(update_fields=["metrics"])


@contextmanager
def job_context(
    job_name: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    triggered_by: str = "cli",
    use_lock: bool = True,
) -> Iterator[JobContext]:
    params = params or {}
    lock_key = _advisory_key(job_name) if use_lock else None
    acquired = False

    if lock_key is not None:
        acquired = _pg_try_lock(lock_key)
        if not acquired:
            jr = JobRun.objects.create(
                job_name=job_name,
                status=JobRun.Status.SKIPPED,
                triggered_by=triggered_by,
                params=params,
                log_path=str(_job_log_path(job_name)),
            )
            yield JobContext(jr, logging.getLogger(f"ops.{job_name}.skipped"))
            return

    log_path = _job_log_path(job_name)

    # Logger dédié (nettoie les anciens handlers file pour éviter les doublons)
    logger = logging.getLogger(f"ops.{job_name}")
    logger.setLevel(logging.INFO)
    fh = logging.FileHandler(log_path)
    fmt = logging.Formatter("[%(levelname)s] %(asctime)s %(name)s: %(message)s")
    fh.setFormatter(fmt)
    for h in list(logger.handlers):
        if isinstance(h, logging.FileHandler):
            logger.removeHandler(h)
            h.close()
    logger.addHandler(fh)

    run = JobRun.objects.create(
        job_name=job_name,
        status=JobRun.Status.RUNNING,
        triggered_by=triggered_by,
        params=params,
        log_path=str(log_path),
    )
    ctx = JobContext(run=run, logger=logger)
    logger.info("Job started  run_id=%s  params=%s", run.pk, params)

    try:
        yield ctx
    except Exception as e:
        run.status = JobRun.Status.FAILED
        run.error_message = str(e)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error_message", "finished_at"])
        logger.exception("Job failed: %s", e)
        raise
    else:
        run.status = JobRun.Status.SUCCESS
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at"])
        logger.info("Job finished success  run_id=%s  duration_ms=%s", run.pk, run.duration_ms)
    finally:
        logger.removeHandler(fh)
        fh.close()
        if lock_key is not None and acquired:
            _pg_unlock(lock_key)
