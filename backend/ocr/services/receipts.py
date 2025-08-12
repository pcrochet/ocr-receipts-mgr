# backend/ocr/services/receipts.py

from __future__ import annotations
from datetime import date
from pathlib import PurePosixPath
from typing import Optional

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from ..models import Receipt
from . import storage
from .audit import write_admin_log

def _build_rel_file(source_path: str, original_filename: str) -> PurePosixPath:
    return storage.rel_join(source_path, original_filename)

def prepare_collected(receipt: Receipt) -> None:
    """
    En état 'collected':
      - vérifie le fichier sous VAR_DIR
      - calcule content_hash, size_bytes, mime_type
    Ne sauvegarde PAS l'objet.
    """
    if receipt.state != Receipt.State.COLLECTED:
        return
    rel_file = _build_rel_file(receipt.source_path, receipt.original_filename)
    abs_path = storage.resolve_under_var(rel_file)
    if not abs_path.exists() or not abs_path.is_file():
        raise ValidationError({
            "original_filename": f"Fichier introuvable sous VAR_DIR : {abs_path}",
            "source_path": "Vérifie le dossier relatif (ex: incoming/2025-08-12).",
        })
    receipt.content_hash = storage.compute_sha256(abs_path)
    size, mime = storage.stat_file(abs_path)
    receipt.size_bytes = size
    if not receipt.mime_type:
        receipt.mime_type = mime

def finalize_collected_move(receipt_pk: int, move_date: Optional[date] = None) -> None:
    """
    Après commit DB : déplace le fichier vers receipts_raw/YYYY-MM-DD/... et met à jour source_path.
    """
    r = Receipt.objects.get(pk=receipt_pk)
    if r.state != Receipt.State.COLLECTED:
        return

    rel_file = _build_rel_file(r.source_path, r.original_filename)
    d = move_date or (r.created_at.date() if r.created_at else timezone.now().date())

    result = storage.move_into_receipts_raw(rel_file, d, keep_subdirs=True)
    if result.moved:
        new_parent = PurePosixPath(*result.dst_rel.parts[:-1]).as_posix() or "receipts_raw"
        r.source_path = new_parent
        r.save(update_fields=["source_path", "updated_at"])
        write_admin_log("File moved incoming -> receipts_raw", receipt=r,
                        extra={"from": rel_file.as_posix(), "to": result.dst_rel.as_posix()})
    else:
        write_admin_log("File not moved (already under receipts_raw or missing)", receipt=r,
                        extra={"path": result.dst_rel.as_posix()})
