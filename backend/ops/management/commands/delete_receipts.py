from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

class Command(BaseCommand):
    help = "Supprime les reçus OCR (et lignes liées). Usage: delete_receipts [--force] [--include-refs]"

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="...")
        parser.add_argument("--include-refs", dest="include_refs", action="store_true", help="...") 

    def handle(self, *args, **opts):
        if not settings.DEBUG and not opts.get("force"):
            raise CommandError("DEBUG=False. Ajoute --force pour exécuter en prod (déconseillé).")

        # Import paresseux pour éviter des imports circulaires
        try:
            from ocr import models as ocr_models
        except Exception as e:
            raise CommandError(f"Impossible d'importer ocr.models : {e}")

        # Détection souple des modèles selon ce qui existe vraiment dans ton app
        model_names_primary = ["ReceiptLine", "Receipt"]  # ordre: enfants -> parent
        model_names_refs = ["Brand", "Store", "Shop"]     # supprimés seulement avec --include-refs

        deleted_total = 0

        def delete_model_if_exists(name: str):
            nonlocal deleted_total
            model = getattr(ocr_models, name, None)
            if model is None:
                return 0, False
            count = model.objects.count()
            model.objects.all().delete()  # suppose on_delete=CASCADE pour les enfants
            deleted_total += count
            self.stdout.write(self.style.WARNING(f"Suppression {name}: {count} lignes"))
            return count, True

        # 1) Reçus & lignes
        for mn in model_names_primary:
            delete_model_if_exists(mn)

        # 2) Références (optionnel)
        if opts["include_refs"]:
            for mn in model_names_refs:
                delete_model_if_exists(mn)

        self.stdout.write(self.style.SUCCESS(f"OK. Total supprimé: {deleted_total}"))
