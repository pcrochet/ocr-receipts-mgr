from __future__ import annotations
import hashlib, os, time
from pathlib import Path
from typing import Callable, Dict, Optional
from django.utils import timezone
from django.db import transaction
from ocr.models import Receipt, ProcessingEvent
from ocr.constants import ReceiptState

def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()

def collect_from_dir(
    *,
    base_dir: str,
    pattern: str = "*.txt",
    recursive: bool = False,
    store_relative: bool = True,
    dry_run: bool = False,
    log: Optional[Callable[[str], None]] = None,
) -> Dict[str, int | float]:
    """
    Scanne un dossier et crée des Receipts state='collected' (sans lignes, sans raw_text).
    - base_dir: dossier racine
    - pattern: motif de fichiers (ex: '*.txt')
    - recursive: parcourt en profondeur si True
    - store_relative: si True, on stocke un chemin relatif dans source_file (par rapport à base_dir), sinon juste le nom
    - idempotence: dédoublonnage par sha256 (unique en base)
    """
    t0 = time.perf_counter()
    root = Path(base_dir)
    files = []
    if recursive:
        for dirpath, _, filenames in os.walk(root):
            d = Path(dirpath)
            files.extend(sorted((d / f) for f in filenames if (d / f).match(pattern)))
    else:
        files = sorted(root.glob(pattern))

    created = 0
    skipped = 0
    errors = 0

    for p in files:
        try:
            digest = _sha256_file(p)
            if Receipt.objects.filter(sha256=digest).exists():
                skipped += 1
                if log:
                    log(f"SKIP duplicate sha256 for {p}")
                continue

            # source_file: relatif (par défaut) ou juste le nom
            if store_relative:
                try:
                    src = str(p.relative_to(root))
                except ValueError:
                    src = p.name
            else:
                src = p.name

            if dry_run:
                created += 1
                if log:
                    log(f"DRY-RUN collect {src} sha256={digest}")
                continue

            with transaction.atomic():
                rec = Receipt.objects.create(
                    # id/uuid_root: defaults DB (gen_random_uuid)
                    source_file=src,
                    sha256=digest,
                    raw_text=None,  # important: on laisse à NULL
                    state=ReceiptState.COLLECTED,
                    created_at=timezone.now(),
                    updated_at=timezone.now(),
                )
                ProcessingEvent.objects.create(
                    receipt=rec,
                    step="collect",
                    status="ok",
                    started_at=timezone.now(),
                    finished_at=timezone.now(),
                    duration_ms=0,
                    message=f"collected {src}",
                )
                created += 1
                if log:
                    log(f"OK collect {src} sha256={digest}")

        except Exception as e:
            errors += 1
            if log:
                log(f"ERROR collect {p}: {e}")

    return {
        "files_seen": len(files),
        "receipts_created": created,
        "duplicates_skipped": skipped,
        "errors_total": errors,
        "duration_seconds": round(time.perf_counter() - t0, 3),
    }
