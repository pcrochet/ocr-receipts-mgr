# backend/ocr/migrations/0003_update_collected_to_ingested.py
from django.db import migrations

def update_collected_to_ingested(apps, schema_editor):
    Receipt = apps.get_model("ocr", "Receipt")
    Receipt.objects.filter(state="collected").update(state="ingested")

class Migration(migrations.Migration):
    dependencies = [
        ("ocr", "0002_initial"),
    ]
    operations = [
        migrations.RunPython(update_collected_to_ingested, reverse_code=migrations.RunPython.noop),
    ]
