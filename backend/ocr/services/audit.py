# backend/ocr/services/audit.py

from __future__ import annotations
from pathlib import Path
from typing import Any, Optional
from django.conf import settings
from django.utils import timezone

def write_admin_log(event: str, *, receipt: Optional[Any] = None, extra: Optional[dict] = None) -> None:
    """
    Écrit une ligne dans VAR_DIR/logs/django-admin-YYYYMMDD.log
    """
    var_dir = Path(getattr(settings, "VAR_DIR", Path(settings.BASE_DIR) / "var"))
    log_dir = var_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    day = timezone.now().strftime("%Y%m%d")
    fpath = log_dir / f"django-admin-{day}.log"
    ts = timezone.now().strftime("%Y-%m-%d %H:%M:%S%z")
    rid = f" receipt_id={getattr(receipt, 'pk', None)}" if receipt is not None else ""
    extra_txt = f" {extra}" if extra else ""
    with fpath.open("a", encoding="utf-8") as f:
        f.write(f"[{ts}] {event}{rid}{extra_txt}\n")
