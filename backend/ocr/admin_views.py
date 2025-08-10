# backend/ocr/admin_views.py
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.core.management import call_command
from django.shortcuts import redirect, render
from django import forms

class CollectForm(forms.Form):
    base_dir = forms.CharField(label="Base dir", required=True)
    pattern = forms.CharField(label="Pattern", required=False, initial="*.txt")
    recursive = forms.BooleanField(label="Récursif", required=False, initial=False)
    absolute = forms.BooleanField(label="Stocker nom seul (pas relatif)", required=False, initial=False)
    dry_run = forms.BooleanField(label="Dry run", required=False, initial=False)

class IngestForm(forms.Form):
    base_dir = forms.CharField(label="Base dir", required=True)
    since = forms.CharField(label="Since (YYYY-MM-DD)", required=False)
    ids = forms.CharField(label="IDs (UUIDs séparés par espace)", required=False)
    dry_run = forms.BooleanField(label="Dry run", required=False, initial=False)

@staff_member_required
def ocr_tools(request):
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "collect":
                form = CollectForm(request.POST)
                if form.is_valid():
                    cd = form.cleaned_data
                    call_command(
                        "collect_from_dir",
                        **{
                            "base-dir": cd["base_dir"],
                            "pattern": cd["pattern"] or "*.txt",
                            "recursive": cd["recursive"],
                            "absolute": cd["absolute"],
                            "dry-run": cd["dry_run"],
                        },
                    )
                    messages.success(request, "Commande collect_from_dir exécutée.")
                else:
                    messages.error(request, f"Form collect invalide: {form.errors}")

            elif action == "ingest":
                form = IngestForm(request.POST)
                if form.is_valid():
                    cd = form.cleaned_data
                    ids = (cd["ids"] or "").strip()
                    call_command(
                        "ingest_ocr",
                        **{
                            "base-dir": cd["base_dir"],
                            "since": cd["since"] or None,
                            "ids": ids.split() if ids else None,
                            "dry-run": cd["dry_run"],
                        },
                    )
                    messages.success(request, "Commande ingest_ocr exécutée.")
                else:
                    messages.error(request, f"Form ingest invalide: {form.errors}")

        except Exception as e:
            messages.error(request, f"Échec: {e}")

        from django.urls import reverse
        return redirect(reverse("admin:ocr_tools"))

    return render(
        request,
        "ocr/ocr_tools.html",
        {"collect_form": CollectForm(), "ingest_form": IngestForm()},
    )
