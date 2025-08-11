# backend/ocr/services/collect_from_dir.py

from __future__ import annotations
import hashlib, os, time, shutil
from pathlib import Path
from typing import Callable, Dict, Optional
from django.utils import timezone
from django.db import transaction
from django.conf import settings
from ocr.models import Receipt, ProcessingEvent
from ocr.constants import ReceiptState

def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()

def _ensure_store_dirs():
    base = Path(getattr(settings, "RECEIPTS_STORE_DIR", settings.BASE_DIR / "var" / "receipts")).resolve()
    sub = getattr(settings, "RECEIPTS_SUBDIRS", {
        "raw": "receipts_raw",
        "json": "receipts_json",
        "logs": "logs",
        "exports": "exports",
    })
    raw = base / sub.get("raw", "receipts_raw")
    jsn = base / sub.get("json", "receipts_json")
    logs = base / sub.get("logs", "logs")
    exp = base / sub.get("exports", "exports")
    for d in (base, raw, jsn, logs, exp):
        d.mkdir(parents=True, exist_ok=True)
    return {"base": base, "raw": raw, "json": jsn, "logs": logs, "exports": exp}

def _dest_path_for(raw_dir: Path, src: Path, digest: str) -> Path:
    """
    Construit un chemin destination dans receipts_raw.
    Évite les collisions de nom en suffixant avec 8 hex du sha256 si nécessaire.
    """
    candidate = raw_dir / src.name
    if candidate.exists():
        candidate = raw_dir / f"{src.stem}__{digest[:8]}{src.suffix}"
    return candidate

def collect_from_dir(
    *,
    base_dir: str,
    pattern: str = "*.txt",
    recursive: bool = False,
    store_relative: bool = True,   # NOTE: désormais relatif au store (et plus à base_dir)
    dry_run: bool = False,
    log: Optional[Callable[[str], None]] = None,
) -> Dict[str, int | float]:
    """
    Scanne un dossier et crée des Receipts state='collected' (sans lignes, sans raw_text).
    - base_dir: dossier racine à scanner
    - pattern: motif de fichiers (ex: '*.txt')
    - recursive: parcourt en profondeur si True
    - store_relative: si True, on stocke un chemin RELATIF AU STORE dans source_file
    - idempotence: dédoublonnage par sha256 (unique en base)
    - À présent: déplace chaque fichier collecté vers <RECEIPTS_STORE_DIR>/receipts_raw/
    """
    t0 = time.perf_counter()
    root = Path(base_dir)
    store = _ensure_store_dirs()

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
    moved = 0

    for p in files:
        try:
            digest = _sha256_file(p)
            if Receipt.objects.filter(sha256=digest).exists():
                skipped += 1
                if log:
                    log(f"SKIP duplicate sha256 for {p}")
                continue

            # Calcule destination dans le store
            dest_path = _dest_path_for(store["raw"], p, digest)
            dest_rel_to_store = dest_path.relative_to(store["base"])  # e.g. "receipts_raw/xxx.txt"

            if dry_run:
                created += 1
                if log:
                    log(f"DRY-RUN collect {p} -> {dest_rel_to_store} sha256={digest}")
                continue

            # Déplace le fichier AVANT la création DB pour éviter des incohérences si move échoue
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dest_path))
            moved += 1

            with transaction.atomic():
                rec = Receipt.objects.create(
                    # id/uuid_root: defaults DB (gen_random_uuid)
                    source_file=str(dest_rel_to_store) if store_relative else str(dest_path),
                    sha256=digest,
                    raw_text=None,  # on laisse à NULL
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
                    message=f"collected from {p} -> {dest_rel_to_store}",
                )
                created += 1
                if log:
                    log(f"OK collect {p} -> {dest_rel_to_store} sha256={digest}")

        except Exception as e:
            errors += 1
            if log:
                log(f"ERROR collect {p}: {e}")

    return {
        "files_seen": len(files),
        "receipts_created": created,
        "duplicates_skipped": skipped,
        "files_moved": moved,
        "errors_total": errors,
        "duration_seconds": round(time.perf_counter() - t0, 3),
    }
