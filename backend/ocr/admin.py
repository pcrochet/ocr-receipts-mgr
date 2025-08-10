# backend/ocr/admin.py

from django.contrib import admin
from .models import Receipt, ReceiptLine, Brand

@admin.action(description="Valider la brand sélectionnée")
def mark_brand_validated(modeladmin, request, queryset):
    queryset.update(state="brand-validated")

@admin.action(description="Réinitialiser la brand (à NULL) et repasser en 'ingested'")
def reset_brand(modeladmin, request, queryset):
    for r in queryset:
        r.brand = None
        r.state = "ingested"
        r.save(update_fields=["brand", "state"])

class ReceiptLineInline(admin.TabularInline):
    model = ReceiptLine
    fields = ("line_number", "text", "item_name", "quantity", "unit", "price_eur", "category", "validation")
    extra = 0
    can_delete = False
    show_change_link = False

@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ("id", "source_file", "state", "brand_name", "t_brand_ms", "created_at")
    list_filter = ("state",)
    search_fields = ("source_file",)
    inlines = [ReceiptLineInline]
    actions = [mark_brand_validated, reset_brand]
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Brand")
    def brand_name(self, obj):
        try:
            return (obj.brand or {}).get("name")
        except Exception:
            return None

@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "aliases_preview")
    search_fields = ("name",)
    readonly_fields = ("id", "created_at", "updated_at")

    @admin.display(description="Aliases")
    def aliases_preview(self, obj):
        try:
            return ", ".join(obj.aliases or [])
        except Exception:
            return ""
