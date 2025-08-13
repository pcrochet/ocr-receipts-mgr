# backend/ocr/admin_views.py

from __future__ import annotations
from django.contrib import messages
from django.shortcuts import redirect, render  # ⬅️ import render
from django.urls import reverse
from django.utils.safestring import mark_safe

from .models import Receipt
from .services.ingest import ingest_from_dir
from ops.services.jobrun import job_context


def receipts_management(request):
    # Dashboard simple: compte par état
    data = []
    for key, label in Receipt.State.choices:
        data.append({
            "key": key,
            "label": label,
            "count": Receipt.objects.filter(state=key).count(),
        })
    # ⬅️ RENDRE le template (ne pas se rediriger soi-même)
    return render(request, "ocr/receipts_management.html", {"data": data})


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
