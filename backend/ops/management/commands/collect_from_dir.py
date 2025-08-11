from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
from pathlib import Path

from ocr.services.collect_from_dir import collect_from_dir

def _store_paths():
    base = Path(getattr(settings, "RECEIPTS_STORE_DIR", settings.BASE_DIR / "var")).resolve()
    sub = getattr(settings, "RECEIPTS_SUBDIRS", {"raw":"receipts_raw","json":"receipts_json","logs":"logs","exports":"exports"})
    logs = base / sub.get("logs", "logs")
    logs.mkdir(parents=True, exist_ok=True)
    return logs

class Command(BaseCommand):
    help = "Collecte des .txt et crée des receipts state='collected' (idempotent par sha256)."

    def add_arguments(self, parser):
        parser.add_argument("--base-dir", required=True, help="Dossier racine à scanner")
        parser.add_argument("--pattern", default="*.txt", help="Motif de fichiers (def: *.txt)")
        parser.add_argument("--recursive", action="store_true", help="Scan récursif")
        parser.add_argument("--absolute", action="store_true", help="Stocker chemin absolu")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        logs_dir = _store_paths()
        log_file = logs_dir / f"collect_from_dir-{timezone.localdate().isoformat()}.log"

        def log(msg: str):
            ts = timezone.now().strftime("%Y-%m-%d %H:%M:%S")
            line = f"[{ts}] {msg}"
            self.stdout.write(line)
            with log_file.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

        metrics = collect_from_dir(
            base_dir=opts["base_dir"],
            pattern=opts["pattern"],
            recursive=opts["recursive"],
            store_relative=not opts["absolute"],
            dry_run=opts["dry_run"],
            log=log,
        )
        log(f"==> Done: {metrics}")
        self.stdout.write(self.style.SUCCESS(f"Log fichier : {log_file}"))
