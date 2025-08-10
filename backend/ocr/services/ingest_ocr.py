# backend/ocr/services/ingest_ocr.py
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Callable, Dict, Optional

from django.db import transaction
from django.utils import timezone

from ocr.models import Receipt, ReceiptLine, ProcessingEvent
from ocr.constants import ReceiptState


def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()

def _read_txt(path: Path) -> str:
    # lecture tolérante
    return path.read_text(encoding="utf-8", errors="ignore")

def ingest_ocr(
    *,
    scope: Dict,
    base_dir: Optional[str] = None,
    dry_run: bool = False,
    log: Optional[Callable[[str], None]] = None,
) -> Dict:
    """
    Traite uniquement les receipts en état 'collected':
      - lit le fichier texte pointé par Receipt.source_file (relatif à base_dir si fourni)
      - renseigne raw_text + sha256
      - (re)crée les ReceiptLine
      - calcule t_ingest_ms
      - passe l'état en 'ocr_done'
      - écrit un ProcessingEvent

    scope = {'all': True} | {'since': 'YYYY-MM-DD'} | {'ids': [uuid, ...]}
    """
    t0 = time.perf_counter()
    base = Path(base_dir) if base_dir else None

    # Sélection des receipts à traiter
    qs = Receipt.objects.filter(state=ReceiptState.COLLECTED)
    if scope.get("ids"):
        qs = qs.filter(id__in=scope["ids"])
    elif scope.get("since"):
        qs = qs.filter(created_at__gte=scope["since"])

    processed = 0
    skipped_missing_file = 0
    errors = 0
    lines_created = 0

    for rec in qs.order_by("created_at").iterator(chunk_size=200):
        started = time.perf_counter()

        # Résoudre le chemin du fichier
        p = Path(rec.source_file)
        if base and not p.is_absolute():
            p = base / p

        if not p.exists():
            skipped_missing_file += 1
            if log:
                log(f"SKIP missing file for receipt={rec.id} source_file={p}")
            continue

        try:
            raw_text = _read_txt(p)
            digest = _sha256_text(raw_text)
            lines = raw_text.splitlines()

            if dry_run:
                processed += 1
                lines_created += len(lines)
                if log:
                    log(f"DRY-RUN ingest {p.name} sha256={digest} lines={len(lines)}")
                # ne change rien en base
                continue

            with transaction.atomic():
                before_state = rec.state

                # (re)créer les lignes proprement (on purge si jamais il y en avait)
                ReceiptLine.objects.filter(receipt=rec).delete()
                objs = [
                    ReceiptLine(
                        receipt=rec,
                        line_number=i,
                        text=line,
                        validation="pending",
                        created_at=timezone.now(),
                        updated_at=timezone.now(),
                    )
                    for i, line in enumerate(lines, start=1)
                ]
                ReceiptLine.objects.bulk_create(objs, batch_size=1000)

                # MAJ du receipt
                t_ms = int((time.perf_counter() - started) * 1000)
                rec.raw_text = raw_text
                rec.sha256 = digest
                rec.t_ingest_ms = t_ms
                rec.state = ReceiptState.OCR_DONE
                rec.updated_at = timezone.now()
                rec.save(update_fields=["raw_text", "sha256", "t_ingest_ms", "state", "updated_at"])

                # Event
                ProcessingEvent.objects.create(
                    receipt=rec,
                    line=None,
                    step="ingest",
                    status="ok",
                    started_at=timezone.now(),
                    finished_at=timezone.now(),
                    duration_ms=t_ms,
                    message=f"ingested {p.name} ({len(lines)} lines)",
                )

            lines_created += len(objs)
            processed += 1
            if log:
                log(f"OK ingest {p.name} sha256={digest} lines={len(lines)} t_ms={t_ms} state:{before_state}->{rec.state}")

        except Exception as e:
            errors += 1
            # Event d’erreur (on loggue même si la transaction a rollback)
            ProcessingEvent.objects.create(
                receipt=rec,
                line=None,
                step="ingest",
                status="error",
                started_at=timezone.now(),
                finished_at=timezone.now(),
                duration_ms=None,
                message=f"{type(e).__name__}: {e}",
            )
            if log:
                log(f"ERROR ingest receipt={rec.id}: {e}")

    return {
        "receipts_processed": processed,
        "skipped_missing_file": skipped_missing_file,
        "lines_created": lines_created,
        "errors_total": errors,
        "duration_seconds": round(time.perf_counter() - t0, 3),
    }
