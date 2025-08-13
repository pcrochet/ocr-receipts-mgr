# ops/management/commands/ingest_from_dir.py

from __future__ import annotations
from django.core.management.base import BaseCommand, CommandError
from ops.services.jobrun import job_context
from ocr.services.ingest import ingest_from_dir

class Command(BaseCommand):
    help = "Ingestion initiale: scanne VAR_DIR/<subdir> et crée des Receipt en état 'collected'."

    def add_arguments(self, parser):
        parser.add_argument("--subdir", default="incoming", help="Sous-dossier (relatif à VAR_DIR)")
        parser.add_argument("--dry-run", action="store_true", help="Ne crée rien, journalise seulement")
        parser.add_argument("--no-recursive", action="store_true", help="Ne pas parcourir récursivement")

    def handle(self, *args, **opts):
        subdir = (opts["subdir"] or "incoming").strip().lstrip("/\\") or "incoming"
        dry = bool(opts["dry_run"])
        recursive = not bool(opts["no_recursive"])

        with job_context("ingest_from_dir", params={"subdir": subdir, "dry_run": dry, "recursive": recursive}) as jc:
            try:
                metrics = ingest_from_dir(subdir, recursive=recursive, dry_run=dry, logger=jc.logger)
            except Exception as e:
                raise CommandError(str(e)) from e

            for k, v in metrics.items():
                jc.set_metric(k, v)

            msg = f"created={metrics['created']} duplicates={metrics['duplicates']} scanned={metrics['scanned']}"
            self.stdout.write(self.style.SUCCESS(msg))
