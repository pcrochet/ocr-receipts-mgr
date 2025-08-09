from django.db import models
from django.utils import timezone
import uuid

class Brand(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    aliases = models.JSONField(default=list)  # stocké sous forme de TEXT[] → on lira/écrira via SQL Brut si besoin, sinon laisser en lecture seule
    meta = models.JSONField(default=dict)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'ocr.brands'
        verbose_name = "Brand"
        verbose_name_plural = "Brands"

class BrandAlias(models.Model):
    id = models.BigAutoField(primary_key=True)
    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, db_column="brand_id")
    alias = models.CharField(max_length=255)
    # embedding ignoré en admin
    created_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "ocr.brand_aliases"

class Receipt(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    uuid_root = models.UUIDField()
    source_file = models.TextField()
    sha256 = models.TextField(unique=True)
    raw_text = models.TextField()
    # embedding ignoré en admin
    brand = models.JSONField(null=True, blank=True)  # {"brand_id":..., "name":..., "score":..., "alias":...}
    state = models.CharField(max_length=32)
    t_ingest_ms = models.IntegerField(null=True, blank=True)
    t_embed_ms = models.IntegerField(null=True, blank=True)
    t_brand_ms = models.IntegerField(null=True, blank=True)
    t_parse_ms = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "ocr.receipts"

class ReceiptLine(models.Model):
    id = models.BigAutoField(primary_key=True)
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, db_column="receipt_id", related_name="lines")
    line_number = models.IntegerField()
    text = models.TextField()
    item_name = models.TextField(null=True, blank=True)
    item_brand = models.TextField(null=True, blank=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    unit = models.CharField(max_length=64, null=True, blank=True)
    price_eur = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    category = models.CharField(max_length=128, null=True, blank=True)
    validation = models.CharField(max_length=16)  # 'pending'|'validated'|'rejected'
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "ocr.receipt_lines"
        unique_together = (("receipt", "line_number"),)

class ProcessingEvent(models.Model):
    id = models.BigAutoField(primary_key=True)
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, db_column="receipt_id", null=True, blank=True)
    line = models.ForeignKey(ReceiptLine, on_delete=models.CASCADE, db_column="line_id", null=True, blank=True)
    step = models.CharField(max_length=32)
    status = models.CharField(max_length=16)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.IntegerField(null=True, blank=True)
    message = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "ocr.processing_events"
