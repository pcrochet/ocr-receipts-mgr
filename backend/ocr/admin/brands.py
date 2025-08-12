# backend/ocr/ad

from __future__ import annotations
from django.contrib import admin
from ..models import Brand

@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "alias_count", "website", "updated_at")
    search_fields = ("name",)
    ordering = ("name",)
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Aliases")
    def alias_count(self, obj):
        return len(obj.aliases or [])
