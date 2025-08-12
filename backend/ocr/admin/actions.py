# backend/ocr/admin/act

from __future__ import annotations
from django.contrib import admin
from ..models import Receipt
from ..services.audit import write_admin_log

@admin.action(description="Marquer 'Brand identifiée'")
def mark_brand_identified(modeladmin, request, queryset):
    ids = list(queryset.values_list("id", flat=True))
    count = queryset.update(state=Receipt.State.BRAND_STORE_IDENTIFIED)
    write_admin_log("Action admin: mark_brand_identified", extra={"count": count, "ids": ids})

@admin.action(description="Réinitialiser Brand (NULL) et repasser en 'ingested'")
def reset_brand(modeladmin, request, queryset):
    ids = []
    for r in queryset:
        r.brand = None
        r.state = Receipt.State.INGESTED
        r.save(update_fields=["brand", "state"])
        ids.append(r.pk)
    write_admin_log("Action admin: reset_brand", extra={"count": len(ids), "ids": ids})
