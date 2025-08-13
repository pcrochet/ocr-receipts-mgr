# backend/ocr/services/ingest.py

from __future__ import annotations
import logging
from pathlib import Path
from typing import Dict, Optional
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from ..models import Receipt
from . import storage
from .receipts import finalize_collected_move

SUPPORTED_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".pdf"}

def ingest_from_dir(
    subdir: str = "incoming",
    *,
    recursive: bool = True,
    dry_run: bool = False,
    logger: Optional[logging.Logger] = None,
) -> Dict[str, int]:
    """
    Scanne VAR_DIR/<subdir>, crée des Receipt en state 'collected',
    déplace les fichiers vers receipts_raw/YYYY-MM-DD/..., renvoie des métriques.
    """
    log = logger or logging.getLogger("ops.ingest_from_dir")
    var_dir = Path(getattr(settings, "VAR_DIR", Path(settings.BASE_DIR) / "var")).resolve()
    base = (var_dir / (subdir.strip().lstrip("/\\") or "incoming")).resolve()

    if not base.exists() or not base.is_dir():
        raise ValidationError(f"Sous-dossier introuvable: {base}")

    files = (
        [p for p in base.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXT]
        if recursive else
        [p for p in base.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXT]
    )

    created = 0
    duplicates = 0
    scanned = 0
    log.info("Scanning dir=%s  files=%d", base, len(files))

    for abs_path in files:
        scanned += 1
        try:
            content_hash = storage.compute_sha256(abs_path)
        except Exception as e:
            log.warning("Skip unreadable file=%s err=%s", abs_path, e)
            continue

        if Receipt.objects.filter(content_hash=content_hash).exists():
            duplicates += 1
            continue

        rel_posix = abs_path.relative_to(var_dir).as_posix()
        rel_parent = "/".join(rel_posix.split("/")[:-1]) or subdir
        filename = abs_path.name

        if dry_run:
            created += 1
            log.info("[dry-run] would create receipt: %s", rel_posix)
            continue

        # Crée le receipt minimal
        r = Receipt.objects.create(
            state=Receipt.State.COLLECTED,
            content_hash=content_hash,
            source_path=rel_parent,
            original_filename=filename,
        )

        # Complète size/mime si possible
        try:
            size, mime = storage.stat_file(abs_path)
            r.size_bytes = size
            if mime:
                r.mime_type = mime
            r.save(update_fields=["size_bytes", "mime_type"])
        except Exception:
            pass

        # Déplacement vers receipts_raw/DATE + MAJ source_path
        finalize_collected_move(r.pk, move_date=timezone.now().date())
        created += 1

        if created % 50 == 0:
            log.info("Progress: created=%d duplicates=%d scanned=%d", created, duplicates, scanned)

    log.info("Done: created=%d duplicates=%d scanned=%d", created, duplicates, scanned)
    return {"created": created, "duplicates": duplicates, "scanned": scanned}
