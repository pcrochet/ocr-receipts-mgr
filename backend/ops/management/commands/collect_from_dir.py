# backend/ops/management/commands/collect_from_dir.py

from __future__ import annotations
import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.conf import settings

from ocr.models import Receipt
from ocr.services import storage as ocr_storage
from ocr.services.receipts import prepare_collected, finalize_collected_move
from ops.services.jobrun import job_context

SUPPORTED_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".pdf"}

class Command(BaseCommand):
    help = "Collecte des fichiers depuis VAR_DIR/<subdir> et crée des Receipt en état 'collected'."

    def add_arguments(self, parser):
        parser.add_argument("--subdir", default="incoming", help="Sous-dossier (relatif à VAR_DIR)")
        parser.add_argument("--dry-run", action="store_true", help="Ne crée rien, journalise seulement")
        parser.add_argument("--recursive", action="store_true", help="Parcourir récursivement (défaut: oui)", default=True)

    def handle(self, *args, **opts):
        subdir = opts["subdir"].strip().lstrip("/\\") or "incoming"
        dry = opts["dry_run"]
        now = timezone.now()

        with job_context("collect_from_dir", params={"subdir": subdir, "dry_run": dry}) as jc:
            logger = jc.logger
            var_dir = Path(getattr(settings, "VAR_DIR", Path(settings.BASE_DIR) / "var")).resolve()
            base = (var_dir / subdir).resolve()
            if not base.exists() or not base.is_dir():
                raise CommandError(f"Sous-dossier introuvable: {base}")

            created = 0
            duplicates = 0
            scanned = 0

            files = []
            if opts["recursive"]:
                for p in base.rglob("*"):
                    if p.is_file() and p.suffix.lower() in SUPPORTED_EXT:
                        files.append(p)
            else:
                files = [p for p in base.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXT]

            logger.info("Scanning dir=%s  files=%d", base, len(files))

            for abs_path in files:
                scanned += 1
                # Chemin relatif POSIX sous VAR_DIR
                rel_posix = abs_path.relative_to(var_dir).as_posix()
                rel_parent = "/".join(rel_posix.split("/")[:-1]) or subdir
                filename = abs_path.name

                try:
                    # Hash pour dédup
                    content_hash = ocr_storage.compute_sha256(abs_path)
                except Exception as e:
                    logger.warning("Skip unreadable file=%s err=%s", abs_path, e)
                    continue

                if Receipt.objects.filter(content_hash=content_hash).exists():
                    duplicates += 1
                    continue

                if dry:
                    created += 1
                    logger.info("[dry-run] would create receipt: %s", rel_posix)
                    continue

                # Crée le receipt minimal
                r = Receipt.objects.create(
                    state=Receipt.State.COLLECTED,
                    content_hash=content_hash,
                    source_path=rel_parent,
                    original_filename=filename,
                    mime_type=abs_path.suffix.lower().lstrip("."),
                )

                # Complète size/mime si besoin
                try:
                    size, mime = ocr_storage.stat_file(abs_path)
                    r.size_bytes = size
                    if mime:
                        r.mime_type = mime
                    r.save(update_fields=["size_bytes", "mime_type"])
                except Exception:
                    pass

                # Applique la logique move + MAJ source_path
                finalize_collected_move(r.pk, move_date=now.date())

                created += 1

                if created % 50 == 0:
                    logger.info("Progress: created=%d duplicates=%d scanned=%d", created, duplicates, scanned)

            jc.set_metric("created", created)
            jc.set_metric("duplicates", duplicates)
            jc.set_metric("scanned", scanned)
            logger.info("Done: created=%d duplicates=%d scanned=%d", created, duplicates, scanned)

            if dry:
                self.stdout.write(self.style.WARNING(f"[dry-run] created={created} duplicates={duplicates} scanned={scanned}"))
            else:
                self.stdout.write(self.style.SUCCESS(f"created={created} duplicates={duplicates} scanned={scanned}"))
