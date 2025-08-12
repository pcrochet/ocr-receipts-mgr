# backend/ocr/services/storage.py

from __future__ import annotations
import hashlib
import mimetypes
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Tuple

from django.conf import settings
from django.core.exceptions import ValidationError

def var_dir() -> Path:
    return Path(getattr(settings, "VAR_DIR", Path(settings.BASE_DIR) / "var")).resolve()

def normalize_rel_posix(path_str: str) -> PurePosixPath:
    p = PurePosixPath((path_str or "").strip().lstrip("/\\"))
    if any(part in ("..", "") for part in p.parts):
        raise ValidationError(f"Chemin relatif invalide: {path_str!r}")
    return p

def rel_join(dir_posix: str, filename: str) -> PurePosixPath:
    d = normalize_rel_posix(dir_posix)
    fn = (filename or "").strip()
    if not fn:
        raise ValidationError("Nom de fichier requis.")
    p = d if d.name.lower() == fn.lower() else (d / fn)
    return p

def resolve_under_var(rel_posix: PurePosixPath) -> Path:
    abs_path = (var_dir() / Path(*rel_posix.parts)).resolve()
    if not str(abs_path).startswith(str(var_dir())):
        raise ValidationError("Chemin hors VAR_DIR interdit.")
    return abs_path

def compute_sha256(abs_path: Path) -> str:
    h = hashlib.sha256()
    with abs_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def stat_file(abs_path: Path) -> Tuple[int, str]:
    size = abs_path.stat().st_size
    mime, _ = mimetypes.guess_type(abs_path.name)
    return size, (mime or "")

@dataclass
class MoveResult:
    src_rel: PurePosixPath
    dst_rel: PurePosixPath
    moved: bool

def move_into_receipts_raw(src_rel: PurePosixPath, d: date, keep_subdirs: bool = True) -> MoveResult:
    """
    Déplace VAR/<src_rel> vers VAR/receipts_raw/YYYY-MM-DD[/subdirs]/filename.
    Idempotent : si déjà sous receipts_raw, ne fait rien.
    """
    parts = list(src_rel.parts)
    if parts and parts[0].lower() == "receipts_raw":
        return MoveResult(src_rel=src_rel, dst_rel=src_rel, moved=False)

    subparts = parts[1:-1] if (keep_subdirs and len(parts) > 2) else []
    dst_rel = PurePosixPath("receipts_raw") / d.isoformat()
    if subparts:
        dst_rel = dst_rel.joinpath(*subparts)
    dst_rel = dst_rel / parts[-1]

    src_abs = resolve_under_var(src_rel)
    dst_abs = resolve_under_var(dst_rel)
    dst_abs.parent.mkdir(parents=True, exist_ok=True)

    if not src_abs.exists() or not src_abs.is_file():
        return MoveResult(src_rel=src_rel, dst_rel=dst_rel, moved=False)

    if dst_abs.exists() and dst_abs.is_file():
        if src_abs.samefile(dst_abs):
            return MoveResult(src_rel=src_rel, dst_rel=dst_rel, moved=False)

    shutil.move(str(src_abs), str(dst_abs))
    return MoveResult(src_rel=src_rel, dst_rel=dst_rel, moved=True)
