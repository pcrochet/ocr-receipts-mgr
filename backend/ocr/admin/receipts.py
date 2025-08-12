# backend/ocr

from __future__ import annotations
from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from ..models import Receipt, ReceiptLine
from ..services.receipts import prepare_collected, finalize_collected_move
from ..services.audit import write_admin_log

class ReceiptAdminForm(forms.ModelForm):
    class Meta:
        model = Receipt
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        state = cleaned.get("state")
        if state == Receipt.State.COLLECTED:
            errors = {}
            if not (cleaned.get("source_path") or "").strip():
                errors["source_path"] = "Requis en état 'collected'."
            if not (cleaned.get("original_filename") or "").strip():
                errors["original_filename"] = "Requis en état 'collected'."
            if errors:
                raise ValidationError(errors)
        return cleaned

class ReceiptLineInline(admin.TabularInline):
    model = ReceiptLine
    extra = 0
    fields = ("line_no", "description", "quantity", "unit", "unit_price", "line_total", "vat_rate", "brand_text")
    ordering = ("line_no",)

@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    form = ReceiptAdminForm

    list_display = ("id", "state", "purchase_date", "brand", "total_amount", "original_filename", "created_at")
    list_filter = ("state", "brand")
    search_fields = ("original_filename", "content_hash", "store_name_raw")
    date_hierarchy = "purchase_date"
    inlines = [ReceiptLineInline]
    ordering = ("-created_at",)
    readonly_fields = ("content_hash", "created_at", "updated_at")

    def get_changeform_initial_data(self, request):
        return {"state": Receipt.State.COLLECTED, "source_path": "incoming"}

    def get_fields(self, request, obj=None):
        minimal = ["state", "source_path", "original_filename", "content_hash", "created_at", "updated_at"]
        full = [
            "state",
            "source_path",
            "original_filename",
            "content_hash",
            "purchase_date",
            "currency",
            "total_amount",
            "brand",
            "store_name_raw",
            "mime_type",
            "size_bytes",
            "ocr_txt_path",
            "ocr_json_path",
            "quarantine_path",
            "metadata",
            "created_at",
            "updated_at",
        ]
        if obj is None or obj.state == Receipt.State.COLLECTED:
            return minimal
        return full

    def get_readonly_fields(self, request, obj=None):
        base_ro = list(super().get_readonly_fields(request, obj))
        ro_if_collected = [
            "content_hash",
            "purchase_date",
            "currency",
            "total_amount",
            "brand",
            "store_name_raw",
            "mime_type",
            "size_bytes",
            "ocr_txt_path",
            "ocr_json_path",
            "quarantine_path",
            "metadata",
            "created_at",
            "updated_at",
        ]
        if obj is None or (obj and obj.state == Receipt.State.COLLECTED):
            return list(set(base_ro + ro_if_collected))
        return base_ro

    def save_model(self, request, obj: Receipt, form, change):
        # prépare hash/size/mime si collected
        if obj.state == Receipt.State.COLLECTED:
            prepare_collected(obj)

        old_state = None
        if change:
            try:
                old_state = type(obj).objects.only("state").get(pk=obj.pk).state
            except type(obj).DoesNotExist:
                old_state = None

        super().save_model(request, obj, form, change)

        if not change:
            write_admin_log("Receipt created", receipt=obj, extra={"state": obj.state})
        elif old_state and old_state != obj.state:
            write_admin_log("Receipt state changed", receipt=obj, extra={"from": old_state, "to": obj.state})

        # Déplacement vers receipts_raw/YYYY-MM-DD après COMMIT
        if obj.state == Receipt.State.COLLECTED:
            move_date = (obj.created_at.date() if obj.created_at else timezone.now().date())
            transaction.on_commit(lambda: finalize_collected_move(obj.pk, move_date))
