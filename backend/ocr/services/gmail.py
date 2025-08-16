# backend/services/gmail.py
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import logging
from dataclasses import dataclass
from datetime import datetime, date
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from django.conf import settings
from django.db import transaction, IntegrityError, DataError
from django.utils import timezone

from ocr.models import Receipt
from ops.models import JobRun

# Google API
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger("ops.gmail")


@dataclass
class CollectSummary:
    jobrun_id: int
    started_at: datetime
    finished_at: Optional[datetime]
    status: str  # "running" | "success" | "failed" | "skipped"
    metrics: Dict[str, Any]
    log_path: str


def _cfg() -> Dict[str, Any]:
    return settings.OPS_GMAIL_COLLECT


def _now_paris() -> datetime:
    return timezone.now()


def _ensure_day_dir(base: Path) -> Path:
    day = _now_paris().date().isoformat()  # YYYY-MM-DD
    out = base / day
    out.mkdir(parents=True, exist_ok=True)
    return out


def _sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _write_jsonl(log_dir: Path, rec: Dict[str, Any]) -> str:
    log_path = log_dir / f"collect_from_gmail-{_now_paris().date().isoformat()}.jsonl"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return str(log_path)


def _log_event(metrics: Dict[str, Any], log_dir: Path, level: str, msg: str, **extra):
    # JSONL “brut” pour telemetry (activable par JSONL_ENABLED)
    rec = {
        "ts": _now_paris().isoformat(),
        "level": level,
        "msg": msg,
        "metrics": metrics,
        **extra,
    }
    return _write_jsonl(log_dir, rec)


# ===== Logs texte (lisibles) =================================================

from typing import Optional as _Optional  # alias local pour les annotations du helper


def _human(fmt: str, **kw) -> str:
    """Mini formateur : tailles humaines + format robuste."""
    def _fmt_size(b: _Optional[int]) -> str:
        if b is None:
            return "n/a"
        n: float = float(b)
        units = ("B", "KB", "MB", "GB")
        for i, unit in enumerate(units):
            if n < 1024.0 or i == len(units) - 1:
                if unit == "B":
                    return f"{int(n)}B"
                return f"{n:.1f}{unit}"
            n /= 1024.0
        return f"{n:.1f}GB"

    kw2 = dict(kw)
    if "size" in kw2 and isinstance(kw2["size"], (int, type(None))):
        kw2["size"] = _fmt_size(kw2["size"])  # type: ignore[arg-type]
    return fmt.format(**kw2)


def _log_text(level: str, message: str):
    if level == "error":
        logger.error(message)
    elif level == "warning":
        logger.warning(message)
    elif level == "debug":
        logger.debug(message)
    else:
        logger.info(message)


def _log(metrics: Dict[str, Any], log_dir: Path, level: str, msg: str, **extra):
    """Router: écrit des logs TEXTE (+ JSONL si activé)."""
    # --- Texte humain
    if msg == "query":
        _log_text(level, _human("[GMAIL] Query: {q}", q=extra.get("query", "")))
    elif msg == "message_begin":
        _log_text(level, _human(
            "[GMAIL] Msg {msg_id} from {sender} ({attachments_count} att) -> processing",
            msg_id=extra.get("msg_id", ""),
            sender=extra.get("sender", ""),
            attachments_count=extra.get("attachments_count", 0),
        ))
    elif msg == "decision":
        decision = extra.get("decision")
        filename = extra.get("filename", "")
        if decision in ("too_small_inline", "too_large", "mime_disallowed"):
            _log_text(level, _human(
                "[GMAIL]   quarantine {filename} ({mime}) reason={reason} size={size}",
                filename=filename, mime=extra.get("mime", ""), reason=decision, size=extra.get("size"),
            ))
        elif decision in ("duplicate_hash", "duplicate_id"):
            _log_text(level, _human(
                "[GMAIL]   skip       {filename} ({mime}) reason={reason}",
                filename=filename, mime=extra.get("mime", ""), reason=decision,
            ))
        elif decision == "downloaded":
            _log_text(level, _human(
                "[GMAIL]   downloaded {filename} ({size})",
                filename=filename, size=extra.get("size"),
            ))
        elif decision == "created":
            _log_text(level, _human(
                "[GMAIL]   created    {filename}",
                filename=filename,
            ))
    elif msg == "message_summary":
        _log_text(level, _human(
            "[GMAIL] Msg {msg_id} from {sender} ({seen} att) -> created={created} quarantined={quarantined} dups={dups}",
            msg_id=extra.get("msg_id", ""),
            sender=extra.get("sender", ""),
            seen=extra.get("seen", 0),
            created=extra.get("created", 0),
            quarantined=extra.get("quarantined", 0),
            dups=extra.get("dups", 0),
        ))
    else:
        _log_text(level, f"[GMAIL] {msg} {extra}")

    # --- JSONL optionnel
    if settings.OPS_GMAIL_COLLECT.get("JSONL_ENABLED"):
        _log_event(metrics, log_dir, level, msg, **extra)


# ===== Gmail API helpers =====================================================

def _get_lockfile() -> Path:
    return Path(settings.VAR_SUBDIRS["locks"]) / "collect_from_gmail.lock"


def _acquire_lock() -> bool:
    lockfile = _get_lockfile()
    if lockfile.exists():
        return False
    try:
        lockfile.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except Exception:
        return False


def _release_lock():
    lockfile = _get_lockfile()
    if lockfile.exists():
        try:
            lockfile.unlink()
        except Exception:
            pass


def _gmail_auth(scopes: List[str], cred_dir: Path):
    token_path = cred_dir / "token.json"
    client_secret_path = cred_dir / "client_secret.json"

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes=scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), scopes=scopes)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return service


def _iter_messages(service, query: str, user_id: str = "me") -> Iterable[Dict[str, Any]]:
    page_token = None
    while True:
        resp = service.users().messages().list(userId=user_id, q=query, pageToken=page_token).execute()
        for item in resp.get("messages", []):
            yield item
        page_token = resp.get("nextPageToken")
        if not page_token:
            break


def _get_message(service, msg_id: str, user_id: str = "me") -> Dict[str, Any]:
    return service.users().messages().get(userId=user_id, id=msg_id, format="full").execute()


def _get_attachment(service, msg_id: str, attach_id: str, user_id: str = "me") -> bytes:
    resp = service.users().messages().attachments().get(userId=user_id, messageId=msg_id, id=attach_id).execute()
    data_b64 = resp["data"]
    return base64.urlsafe_b64decode(data_b64.encode("utf-8"))


def _list_attachments_from_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []

    def walk(part):
        mime = part.get("mimeType")
        filename = part.get("filename") or ""
        body = part.get("body", {})
        attach_id = body.get("attachmentId")
        size = body.get("size")
        if attach_id:
            result.append({
                "attachmentId": attach_id,
                "filename": filename,
                "mimeType": mime,
                "size": size,
            })
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    return result


def _extract_headers(payload: Dict[str, Any]) -> Dict[str, str]:
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    return {
        "from": headers.get("from", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
    }


def _parse_gmail_date(date_str: str) -> Optional[datetime]:
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            from datetime import timezone as dtz
            dt = dt.replace(tzinfo=dtz.utc)
        return dt.astimezone(timezone.get_current_timezone())
    except Exception:
        return None


def _build_effective_query(cfg: Dict[str, Any], since: Optional[date]) -> str:
    q = cfg.get("QUERY", "").strip()
    if since:
        q = f"{q} after:{since.strftime('%Y/%m/%d')}"
    if cfg.get("APPLY_LABELS"):
        imported = cfg.get("LABEL_IMPORTED", "pobs/imported")
        quarant = cfg.get("LABEL_QUARANTINE", "pobs/quarantine")
        if f"-label:{imported}" not in q:
            q = f"{q} -label:{imported}"
        if f"-label:{quarant}" not in q:
            q = f"{q} -label:{quarant}"
    return " ".join(q.split())


def _ensure_labels_and_map_ids(service, cfg: Dict[str, Any]) -> Dict[str, str]:
    wanted = {cfg.get("LABEL_IMPORTED", "pobs/imported"), cfg.get("LABEL_QUARANTINE", "pobs/quarantine")}
    name_to_id: Dict[str, str] = {}
    resp = service.users().labels().list(userId="me").execute()
    for lab in resp.get("labels", []):
        if lab.get("name") in wanted:
            name_to_id[lab["name"]] = lab["id"]
    for name in wanted - set(name_to_id.keys()):
        body = {"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
        lab = service.users().labels().create(userId="me", body=body).execute()
        name_to_id[name] = lab["id"]
    return name_to_id


# ===== Collect job ============================================================

def collect_from_gmail(*, dry_run: Optional[bool] = None, max_items: Optional[int] = None, since: Optional[date] = None) -> CollectSummary:
    cfg = _cfg()
    verbose = bool(cfg.get("VERBOSE", False))
    dry_run = cfg["DRY_RUN"] if dry_run is None else dry_run
    max_items = cfg["MAX_ATTACH_PER_RUN"] if max_items is None else max_items

    metrics = {
        "emails_scanned": 0,
        "attachments_seen": 0,
        "attachments_downloaded": 0,
        "receipts_created": 0,
        "duplicates_skipped": 0,
        "quarantined": 0,
        "errors_count": 0,
        "gmail_api_calls": 0,
    }

    log_dir = Path(cfg["LOG_JSONL_DIR"])
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = str(log_dir / f"collect_from_gmail-{_now_paris().date().isoformat()}.jsonl")

    # Lock
    if not _acquire_lock():
        # texte + JSONL optionnel
        _log_text("info", "[GMAIL] Skipped: lock already active")
        if cfg.get("JSONL_ENABLED"):
            _write_jsonl(log_dir, {"ts": _now_paris().isoformat(), "level": "info", "msg": "Skipped: lock already active"})
        jr = JobRun.objects.create(
            job_name="collect_from_gmail",
            status=JobRun.Status.SKIPPED,
            triggered_by="system",
            params={"dry_run": dry_run, "max_items": max_items, "since": since.isoformat() if since else None},
            metrics=metrics,
            log_path=log_path,
        )
        return CollectSummary(jr.pk, jr.started_at, jr.finished_at, jr.status, metrics, log_path)

    jr = JobRun.objects.create(
        job_name="collect_from_gmail",
        status=JobRun.Status.RUNNING,
        triggered_by="system",
        params={"dry_run": dry_run, "max_items": max_items, "since": since.isoformat() if since else None},
        metrics=metrics,
        log_path=log_path,
    )
    status = JobRun.Status.RUNNING

    last_context: Dict[str, Any] = {}

    try:
        # Auth Gmail
        service = _gmail_auth(cfg["SCOPES"], Path(cfg["CREDENTIALS_DIR"]))
        metrics["gmail_api_calls"] += 1

        # Query effective
        query = _build_effective_query(cfg, since)
        _log(metrics, log_dir, "info", "query", query=query)

        # Labels (si on veut en appliquer et si pas en dry-run)
        label_name_to_id: Dict[str, str] = {}
        if not dry_run and (cfg.get("APPLY_LABELS") or cfg.get("MARK_AS_READ")):
            try:
                label_name_to_id = _ensure_labels_and_map_ids(service, cfg)
                metrics["gmail_api_calls"] += 2
            except HttpError as e:
                metrics["errors_count"] += 1
                _log(metrics, log_dir, "error", "labels_init_failed", error=str(e))

        # Filtres
        blacklist = {s.lower() for s in cfg.get("BLACKLIST_SENDERS", []) if s}
        allowed_mimes = set(cfg.get("ALLOWED_MIME_TYPES", []))
        max_size = int(cfg.get("MAX_SIZE_BYTES", 5 * 1024 * 1024))
        min_img_inline = int(cfg.get("MIN_IMAGE_INLINE_BYTES", 20 * 1024))

        incoming_day_dir = _ensure_day_dir(Path(cfg["STORAGE_INCOMING_DIR"]))
        quarantine_day_dir = _ensure_day_dir(Path(cfg["STORAGE_QUARANTINE_DIR"]))

        # Itération messages
        attach_budget = int(max_items) if max_items else 10**9

        for msg_stub in _iter_messages(service, query):
            if attach_budget <= 0:
                break
            metrics["emails_scanned"] += 1

            msg = _get_message(service, msg_stub["id"])
            metrics["gmail_api_calls"] += 1

            payload = msg.get("payload", {})
            headers = _extract_headers(payload)
            sender_hdr = headers["from"]
            subject = headers["subject"]
            received_at = _parse_gmail_date(headers["date"])

            _, sender_email = parseaddr(sender_hdr)
            sender_email_lc = (sender_email or "").strip().lower()

            # Début du message : log compact
            attachments = _list_attachments_from_payload(payload)
            if verbose:
                _log(metrics, log_dir, "debug", "message_begin",
                     msg_id=msg["id"],
                     sender=sender_email or sender_hdr,
                     attachments_count=len(attachments),
                     received_at=received_at.isoformat() if received_at else None)

            # Filtre blacklist
            if sender_email_lc and sender_email_lc in blacklist:
                _log_text("info", f"[GMAIL] Msg {msg['id']} skipped (blacklist) sender={sender_email_lc}")
                if cfg.get("JSONL_ENABLED"):
                    _log_event(metrics, log_dir, "info", "skip_blacklist", msg_id=msg["id"], sender=sender_email_lc)
                continue

            if not attachments:
                if verbose:
                    _log_text("debug", f"[GMAIL] Msg {msg['id']} has no attachments")
                continue

            # Compteurs par message pour labels + résumé
            msg_ingested = 0
            msg_quarantined = 0
            msg_seen = 0
            dups_for_msg = 0

            for att in attachments:
                if attach_budget <= 0:
                    break
                metrics["attachments_seen"] += 1
                msg_seen += 1

                att_id = att["attachmentId"]
                filename = att.get("filename") or f"attachment-{att_id}"
                mime = att.get("mimeType") or ""
                size_raw = att.get("size")
                size = int(size_raw) if isinstance(size_raw, int) else (int(size_raw) if size_raw is not None else 0)

                last_context = {
                    "msg_id": msg["id"],
                    "attachment_id": (att_id[:80] + "…") if att_id and len(att_id) > 80 else att_id,
                    "filename": filename,
                    "mime": mime,
                    "size": size,
                }

                # Filtre MIME
                if allowed_mimes and mime not in allowed_mimes:
                    _log(metrics, log_dir, "info", "decision", decision="mime_disallowed", **last_context)
                    metrics["quarantined"] += 1
                    msg_quarantined += 1
                    continue

                # Filtre inline images trop petites
                if mime.startswith("image/") and isinstance(size, int) and size < min_img_inline:
                    _log(metrics, log_dir, "info", "decision", decision="too_small_inline", **last_context)
                    metrics["quarantined"] += 1
                    msg_quarantined += 1
                    continue

                # Filtre taille max
                if isinstance(size, int) and size > max_size:
                    _log(metrics, log_dir, "info", "decision", decision="too_large", **last_context)
                    metrics["quarantined"] += 1
                    msg_quarantined += 1
                    continue

                # Récupérer le contenu
                try:
                    content = _get_attachment(service, msg["id"], att_id)
                    metrics["gmail_api_calls"] += 1
                except Exception as e:
                    metrics["errors_count"] += 1
                    _log(metrics, log_dir, "error", "fetch_attachment_failed", error=str(e), **last_context)
                    continue

                content_hash = _sha256_bytes(content)

                # Déduplication par gmail_attachment_id + source, puis par content_hash
                if Receipt.objects.filter(source="gmail", gmail_attachment_id=att_id).exists():
                    metrics["duplicates_skipped"] += 1
                    dups_for_msg += 1
                    _log(metrics, log_dir, "info", "decision", decision="duplicate_id", **last_context)
                    continue
                if Receipt.objects.filter(content_hash=content_hash).exists():
                    metrics["duplicates_skipped"] += 1
                    dups_for_msg += 1
                    _log(metrics, log_dir, "info", "decision", decision="duplicate_hash", **last_context)
                    continue

                # Écriture fichier (toujours loggé avec filename)
                dest_path = incoming_day_dir / filename
                if not dry_run:
                    dest_path.write_bytes(content)
                metrics["attachments_downloaded"] += 1
                _log(metrics, log_dir, "info" if not verbose else "debug", "decision", decision="downloaded", **last_context)

                # Création Receipt (loggue filename)
                if not dry_run:
                    try:
                        with transaction.atomic():
                            Receipt.objects.create(
                                state=Receipt.State.INGESTED,
                                content_hash=content_hash,
                                source_path=str(Path("incoming") / incoming_day_dir.name / filename),  # relatif à var/
                                quarantine_path="",
                                ocr_txt_path="",
                                ocr_json_path="",
                                original_filename=filename,
                                mime_type=mime,
                                size_bytes=size,
                                source="gmail",
                                gmail_message_id=msg["id"],
                                gmail_attachment_id=att_id,
                                sender=sender_email or sender_hdr,
                                subject=subject,
                                received_at=received_at,
                            )
                        metrics["receipts_created"] += 1
                        msg_ingested += 1
                        _log(metrics, log_dir, "info" if not verbose else "debug", "decision", decision="created", **last_context)
                    except (IntegrityError, DataError) as e:
                        _log(
                            metrics, log_dir, "error", "create_failed",
                            error=str(e),
                            message_id_len=len(msg["id"]) if msg.get("id") else None,
                            attachment_id_len=len(att_id) if att_id else None,
                            **last_context,
                        )
                        metrics["errors_count"] += 1
                        try:
                            if dest_path.exists():
                                dest_path.unlink()
                        except Exception:
                            pass

                attach_budget -= 1

            # Résumé par message
            _log(
                metrics, log_dir, "info", "message_summary",
                msg_id=msg["id"],
                sender=sender_email or sender_hdr,
                seen=msg_seen,
                created=msg_ingested,
                quarantined=msg_quarantined,
                dups=dups_for_msg,
            )

            # Labels post-traitement
            try:
                if not dry_run and (cfg.get("APPLY_LABELS") or cfg.get("MARK_AS_READ")):
                    mods = {"addLabelIds": [], "removeLabelIds": []}
                    if cfg.get("MARK_AS_READ"):
                        mods["removeLabelIds"].append("UNREAD")

                    if cfg.get("APPLY_LABELS"):
                        if msg_ingested > 0:
                            lab_id = label_name_to_id.get(cfg.get("LABEL_IMPORTED", "pobs/imported"))
                            if lab_id:
                                mods["addLabelIds"].append(lab_id)
                        elif msg_seen > 0 and msg_ingested == 0 and msg_quarantined > 0:
                            lab_id = label_name_to_id.get(cfg.get("LABEL_QUARANTINE", "pobs/quarantine"))
                            if lab_id:
                                mods["addLabelIds"].append(lab_id)

                    if mods["addLabelIds"] or mods["removeLabelIds"]:
                        service.users().messages().modify(userId="me", id=msg["id"], body=mods).execute()
                        metrics["gmail_api_calls"] += 1
                        if verbose:
                            _log(metrics, log_dir, "debug", "labels_modified", msg_id=msg["id"], mods=mods)
            except HttpError as e:
                metrics["errors_count"] += 1
                _log(metrics, log_dir, "error", "labels_modify_failed", error=str(e), msg_id=msg["id"])

        status = JobRun.Status.SUCCESS if metrics["errors_count"] == 0 else JobRun.Status.FAILED

    except Exception as e:
        metrics["errors_count"] += 1
        _log(metrics, log_dir, "error", "unhandled_exception", error=str(e), last_context=last_context)
        status = JobRun.Status.FAILED

    finally:
        jr.refresh_from_db()
        jr.metrics = metrics
        jr.status = status
        jr.finished_at = timezone.now()
        jr.log_path = log_path
        jr.save(update_fields=["metrics", "status", "finished_at", "log_path"])
        _release_lock()

    return CollectSummary(
        jobrun_id=jr.pk,
        started_at=jr.started_at,
        finished_at=jr.finished_at,
        status=jr.status,
        metrics=metrics,
        log_path=log_path,
    )
