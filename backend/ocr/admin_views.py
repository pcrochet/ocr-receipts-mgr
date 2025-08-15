# backend/ocr/admin_views.py

from __future__ import annotations
from django.contrib import messages
from django.shortcuts import redirect, render  # ⬅️ import render
from django.urls import reverse
from django.utils.safestring import mark_safe
import os
from django.conf import settings

from .models import Receipt
from .services.ingest import ingest_from_dir
from ops.services.jobrun import job_context

from pathlib import Path
from ocr.services.gmail import collect_from_gmail
from ops.models import JobRun



def receipts_management(request):
    # Compter fichiers incoming (non récursif)
    incoming_dir = os.path.join(settings.BASE_DIR, "var", "incoming")
    incoming_count = sum(1 for f in os.listdir(incoming_dir)
                         if os.path.isfile(os.path.join(incoming_dir, f)))

    # Dashboard : exclure 'collected'
    data = []
    for key, label in Receipt.State.choices:
        if key == "collected":
            continue
        data.append({
            "key": key,
            "label": label,
            "count": Receipt.objects.filter(state=key).count(),
        })

    return render(request, "ocr/receipts_management.html", {
        "data": data,
        "incoming_count": incoming_count
    })


def run_ingest_from_dir(request):
    if request.method != "POST":
        return redirect("receipts_management")

    subdir = (request.POST.get("subdir") or "incoming").strip()
    dry = bool(request.POST.get("dry_run"))          # "on" -> True, absent -> False
    recursive = bool(request.POST.get("recursive"))  # "on" -> True, absent -> False

    with job_context(
        "ingest_from_dir",
        params={"subdir": subdir, "dry_run": dry, "recursive": recursive},
        triggered_by="admin",
    ) as jc:
        metrics = ingest_from_dir(subdir, recursive=recursive, dry_run=dry, logger=jc.logger)
        for k, v in metrics.items():
            jc.set_metric(k, v)
        run_url = reverse("admin:ops_jobrun_change", args=[jc.run.pk])

    msg = (
        f"Ingestion terminée: created={metrics['created']} "
        f"duplicates={metrics['duplicates']} scanned={metrics['scanned']}. "
        f"<a href='{run_url}'>Voir le JobRun</a>"
    )
    messages.success(request, mark_safe(msg))
    return redirect("receipts_management")


def collect_from_gmail_view(request):
    if request.method != "POST":
        return redirect(reverse("receipts_management"))

    dry_run = bool(request.POST.get("dry_run"))
    max_items_raw = request.POST.get("max_items")
    max_items = int(max_items_raw) if (max_items_raw and max_items_raw.isdigit()) else None

    summary = collect_from_gmail(dry_run=dry_run, max_items=max_items)

    level = messages.INFO if summary.status == "success" else messages.ERROR
    messages.add_message(
        request,
        level,
        (
            f"Collecte Gmail — status={summary.status} | "
            f"created={summary.metrics.get('receipts_created')} | "
            f"downloaded={summary.metrics.get('attachments_downloaded')} | "
            f"duplicates={summary.metrics.get('duplicates_skipped')} | "
            f"errors={summary.metrics.get('errors_count')} | "
            f"log: {summary.log_path}"
        ),
    )
    return redirect(reverse("receipts_management"))
