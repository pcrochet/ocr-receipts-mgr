# backend/ops/management/commands/collect_from_gmail.py
from __future__ import annotations

from datetime import datetime
from typing import Optional

from django.core.management.base import BaseCommand, CommandParser

# Service générique (on a mis le service sous ocr/services/gmail.py)
from ocr.services.gmail import collect_from_gmail


class Command(BaseCommand):
    help = "Collecte depuis Gmail les pièces jointes de tickets et crée des Receipts (state=ingested)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simuler sans écrire ni fichiers ni en base.",
        )
        parser.add_argument(
            "--max",
            type=int,
            default=None,
            help="Nombre maximum de pièces jointes à traiter.",
        )
        parser.add_argument(
            "--since",
            type=str,
            default=None,
            help="Filtrer les emails après YYYY-MM-DD (ex: 2025-08-01).",
        )

    def handle(self, *args, **options):
        dry_run: bool = bool(options.get("dry_run"))
        max_items: Optional[int] = options.get("max")
        since_str: Optional[str] = options.get("since")

        since = None
        if since_str:
            try:
                since = datetime.strptime(since_str, "%Y-%m-%d").date()
            except ValueError:
                self.stderr.write(self.style.ERROR("Format invalide pour --since (attendu YYYY-MM-DD)."))
                return

        summary = collect_from_gmail(dry_run=dry_run, max_items=max_items, since=since)

        # Résumé humain
        self.stdout.write(
            self.style.SUCCESS(
                f"JobRun #{summary.jobrun_id} status={summary.status} | "
                f"created={summary.metrics.get('receipts_created')} | "
                f"downloaded={summary.metrics.get('attachments_downloaded')} | "
                f"duplicates={summary.metrics.get('duplicates_skipped')} | "
                f"errors={summary.metrics.get('errors_count')}"
            )
        )
        self.stdout.write(f"Log JSONL: {summary.log_path}")
