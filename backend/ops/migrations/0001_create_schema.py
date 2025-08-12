from django.db import migrations

class Migration(migrations.Migration):
    initial = False
    dependencies = []
    operations = [
        migrations.RunSQL("CREATE SCHEMA IF NOT EXISTS pobs;", reverse_sql=""),
    ]
