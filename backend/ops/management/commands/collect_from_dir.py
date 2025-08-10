# ops/management/commands/collect_from_dir.py
from django.core.management.base import BaseCommand
from ocr.services.collect_from_dir import collect_from_dir

class Command(BaseCommand):
    help = "Collecte des .txt et crée des receipts state='collected' (idempotent par sha256)."

    def add_arguments(self, parser):  # <-- renomme 'p' en 'parser'
        parser.add_argument("--base-dir", required=True, help="Dossier racine à scanner")
        parser.add_argument("--pattern", default="*.txt", help="Motif de fichiers (def: *.txt)")
        parser.add_argument("--recursive", action="store_true", help="Scan récursif")
        parser.add_argument("--absolute", action="store_true", help="Stocker seulement le nom (pas relatif)")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        def log(msg: str): self.stdout.write(msg)
        metrics = collect_from_dir(
            base_dir=opts["base_dir"],
            pattern=opts["pattern"],
            recursive=opts["recursive"],
            store_relative=not opts["absolute"],
            dry_run=opts["dry_run"],
            log=log,
        )
        self.stdout.write(self.style.SUCCESS(f"Done: {metrics}"))
