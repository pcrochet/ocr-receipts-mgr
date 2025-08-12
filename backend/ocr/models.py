from __future__ import annotations

from decimal import Decimal
from django.db import models
from django.db.models import Q, UniqueConstraint
from django.db.models.functions import Lower
from django.core.validators import MinValueValidator
from django.utils import timezone
from django.contrib.postgres.fields import ArrayField


class Brand(models.Model):
    """
    Chaîne/marque (Intermarché, Auchan, etc.)
    Unicité insensible à la casse, aliases stockés en ArrayField.
    """
    name = models.CharField(max_length=150)
    aliases = ArrayField(
        base_field=models.CharField(max_length=100),
        default=list,
        blank=True,
        help_text="Variantes/alias détectés (OCR).",
    )
    website = models.URLField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "brands"
        constraints = [
            UniqueConstraint(Lower("name"), name="uq_brand_name_ci"),
        ]
        indexes = [
            models.Index(Lower("name"), name="ix_brand_name_ci"),
        ]

    def __str__(self) -> str:
        return self.name


class Receipt(models.Model):
    """
    Ticket de caisse avec machine à états.
    Les chemins sont RELATIFS à VAR_DIR (ex: incoming/2025-08-12/IMG_1234.jpg).
    """
    class State(models.TextChoices):
        COLLECTED = "collected", "Collected"
        INGESTED = "ingested", "Ingested"
        OCR_DONE = "ocr_done", "OCR done"
        VECTORIZED = "vectorized", "Vectorized"
        BRAND_STORE_IDENTIFIED = "brand_store_identified", "Brand/Store identified"

    # État
    state = models.CharField(max_length=32, choices=State.choices, default=State.COLLECTED, db_index=True)

    # Déduplication
    content_hash = models.CharField(max_length=64, unique=True, help_text="SHA-256 du binaire source.")

    # Fichiers / chemins
    source_path = models.CharField(max_length=512, help_text="Ex: incoming/2025-08-12/IMG_1234.jpg")
    quarantine_path = models.CharField(max_length=512, blank=True)
    ocr_txt_path = models.CharField(max_length=512, blank=True)
    ocr_json_path = models.CharField(max_length=512, blank=True)

    original_filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100, blank=True)
    size_bytes = models.BigIntegerField(null=True, blank=True, validators=[MinValueValidator(0)])

    # Métier
    purchase_date = models.DateField(null=True, blank=True, db_index=True)
    currency = models.CharField(max_length=3, default="EUR")
    total_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)]
    )

    store_name_raw = models.CharField(max_length=255, blank=True, help_text="Texte brut (OCR) avant mapping.")
    brand = models.ForeignKey("Brand", null=True, blank=True, on_delete=models.SET_NULL, related_name="receipts")

    # Divers
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "receipts"
        constraints = [
            # total >= 0 si présent
            models.CheckConstraint(
                check=Q(total_amount__gte=0) | Q(total_amount__isnull=True),
                name="ck_receipt_total_ge0",
            ),
        ]
        indexes = [
            models.Index(fields=["state"], name="ix_receipt_state"),
            models.Index(fields=["purchase_date"], name="ix_receipt_pdate"),
            models.Index(fields=["brand_id"], name="ix_receipt_brand"),
        ]

    def __str__(self) -> str:
        return f"Receipt #{self.pk} [{self.state}]"


class ReceiptLine(models.Model):
    """
    Ligne d'article d'un ticket.
    """
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField(help_text="Position/numéro de ligne dans le ticket.")
    description = models.CharField(max_length=512)
    quantity = models.DecimalField(max_digits=10, decimal_places=3, default=Decimal("1"), validators=[MinValueValidator(0)])
    unit = models.CharField(max_length=32, blank=True, help_text="ex: x125g, 500g, L, kg, unité…")
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    line_total = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, help_text="ex: 5.50 pour 5,5%")
    brand_text = models.CharField(max_length=100, blank=True, help_text="Marque brute si détectée.")
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "receipts_lines"
        constraints = [
            models.UniqueConstraint(fields=["receipt", "line_no"], name="uq_receipt_line_no"),
            models.CheckConstraint(check=Q(quantity__gte=0), name="ck_line_qty_ge0"),
            models.CheckConstraint(check=Q(unit_price__gte=0) | Q(unit_price__isnull=True), name="ck_line_unit_price_ge0"),
            models.CheckConstraint(check=Q(line_total__gte=0) | Q(line_total__isnull=True), name="ck_line_total_ge0"),
        ]
        indexes = [
            models.Index(fields=["receipt", "line_no"], name="ix_line_receipt_no"),
        ]

    def __str__(self) -> str:
        rid = getattr(self, "receipt_id", None) or (self.receipt.pk if hasattr(self, "receipt") else None)  # <-- CHANGÉ
        return f"ReceiptLine #{self.pk} r={rid} #{self.line_no}"

class ProcessingEvent(models.Model):
    """
    Journal technique des étapes de traitement (sera enrichi à l'Étape 4).
    """
    class Step(models.TextChoices):
        COLLECT_FROM_GMAIL = "collect_from_gmail", "Collect from Gmail"
        COLLECT_FROM_DIR = "collect_from_dir", "Collect from directory"
        COMPUTE_OCR = "compute_ocr", "Compute OCR"
        VECTORIZ_RECEIPTS = "vectorize_receipts", "Vectorize receipts"
        GUESS_BRAND = "guess_brand", "Guess brand"

    class Status(models.TextChoices):
        STARTED = "started", "Started"
        SUCCESS = "success", "Success"
        ERROR = "error", "Error"

    receipt = models.ForeignKey("Receipt", on_delete=models.CASCADE, null=True, blank=True)
    line = models.ForeignKey("ReceiptLine", on_delete=models.CASCADE, null=True, blank=True)
    step = models.CharField(max_length=32, choices=Step.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.STARTED)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.IntegerField(null=True, blank=True)
    message = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "processing_events"
        indexes = [
            models.Index(fields=["step", "status", "started_at"]),
            models.Index(fields=["receipt_id", "started_at"]),
        ]
