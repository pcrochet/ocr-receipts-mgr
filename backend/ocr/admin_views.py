# backend/ocr/admin_views.py
from pathlib import Path
from django.conf import settings
from django.shortcuts import render
from django.utils import timezone
from ocr.services.collect_from_dir import collect_from_dir

def _get_store_paths():
    base = Path(getattr(settings, "RECEIPTS_STORE_DIR", settings.BASE_DIR / "var")).resolve()
    sub = getattr(settings, "RECEIPTS_SUBDIRS", {"raw":"receipts_raw","json":"receipts_json","logs":"logs","exports":"exports"})
    paths = {
        "base": base,
        "raw": base / sub.get("raw", "receipts_raw"),
        "json": base / sub.get("json", "receipts_json"),
        "logs": base / sub.get("logs", "logs"),
        "exports": base / sub.get("exports", "exports"),
    }
    for d in paths.values():
        d.mkdir(parents=True, exist_ok=True)
    return paths

def ocr_tools(request):
    log_text = ""
    log_file_path = None
    banner = None

    if request.method == "POST":
        base_dir = request.POST.get("base_dir") or str(settings.BASE_DIR / "import")
        recursive = "recursive" in request.POST
        dry_run = "dry_run" in request.POST

        store = _get_store_paths()
        log_file_path = store["logs"] / f"collect_from_dir-{timezone.localdate().isoformat()}.log"

        lines = []
        def ui_log(msg: str):
            ts = timezone.now().strftime("%Y-%m-%d %H:%M:%S")
            line = f"[{ts}] {msg}"
            lines.append(line)
            with log_file_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

        metrics = collect_from_dir(
            base_dir=base_dir,
            pattern="*.txt",
            recursive=recursive,
            store_relative=True,
            dry_run=dry_run,
            log=ui_log,
        )
        ui_log(f"==> Done: {metrics}")

        log_text = "\n".join(lines)
        banner = f"Collecte terminée. Log: {log_file_path}"

    return render(request, "ocr/ocr_tools.html", {
        "log_text": log_text,
        "log_file_path": log_file_path,
        "banner": banner,  # <= affiché juste sous le bouton
    })
