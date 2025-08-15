# backend/services/gmail.py
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from django.conf import settings
from django.db import transaction, IntegrityError
from django.utils import timezone

from ocr.models import Receipt
from ops.models import JobRun

# Google API
# pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


@dataclass
class CollectSummary:
    jobrun_id: int
    started_at: datetime
    finished_at: Optional[datetime]
    status: str  # ou Literal["running","success","failed","skipped"] si tu veux être strict
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
    rec = {
        "ts": _now_paris().isoformat(),
        "level": level,
        "msg": msg,
        "metrics": metrics,
        **extra,
    }
    return _write_jsonl(log_dir, rec)


def _get_lockfile() -> Path:
    return Path(settings.VAR_SUBDIRS["locks"]) / "collect_from_gmail.lock"


def _acquire_lock() -> bool:
    lockfile = _get_lockfile()
    if lockfile.exists():
        # lock simple (best-effort)
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
            # Refresh
            from google.auth.transport.requests import Request
            creds.refresh(Request())
        else:
            # Interactive local flow (DEV) — en PROD, on copie le token.json déjà généré
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), scopes=scopes)
            creds = flow.run_local_server(port=0)
        # Save
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
    # Parcourt récursivement le payload MIME et renvoie une liste d'attachements {id, filename, mimeType, size}
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
    # Gmail renvoie un Date RFC2822. On tente un parse permissif.
    try:
        # email.utils.parsedate_to_datetime dispo en Py3.3+
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            # on l’assume UTC si absent
            from datetime import timezone as dtz
            dt = dt.replace(tzinfo=dtz.utc)
        return dt.astimezone(timezone.get_current_timezone())
    except Exception:
        return None


def collect_from_gmail(*, dry_run: Optional[bool] = None, max_items: Optional[int] = None, since: Optional[date] = None) -> CollectSummary:
    cfg = _cfg()
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
        # On loggue aussi l'événement "skipped" pour traçabilité
        _write_jsonl(log_dir, {
            "ts": _now_paris().isoformat(),
            "level": "info",
            "msg": "Skipped: lock already active",
        })
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

    status = JobRun.Status.RUNNING  # ← pour satisfaire Pylance (valeur sûre par défaut)

    started = time.time()
    try:
        # Auth Gmail
        service = _gmail_auth(cfg["SCOPES"], Path(cfg["CREDENTIALS_DIR"]))
        metrics["gmail_api_calls"] += 1

        # Query
        query = cfg["QUERY"]
        if since:
            # Gmail: after:YYYY/MM/DD (format US)
            query = f"{query} after:{since.strftime('%Y/%m/%d')}"
        _log_event(metrics, log_dir, "info", f"Query: {query}")

        allowed_senders = set(map(str.lower, cfg.get("ALLOWED_SENDERS", [])))
        allowed_mimes = set(cfg.get("ALLOWED_MIME_TYPES", []))
        max_size = int(cfg.get("MAX_SIZE_BYTES", 20 * 1024 * 1024))

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
            sender = headers["from"]
            subject = headers["subject"]
            received_at = _parse_gmail_date(headers["date"])

            # Filtre sender (si whitelist non vide)
            if allowed_senders and not any(part.strip("<>").lower() in allowed_senders for part in sender.replace('"', '').split()):
                _log_event(metrics, log_dir, "info", "Sender not allowed; skipping message", sender=sender, msg_id=msg["id"])
                continue

            # Lister les attachments
            attachments = _list_attachments_from_payload(payload)
            if not attachments:
                continue

            for att in attachments:
                if attach_budget <= 0:
                    break
                metrics["attachments_seen"] += 1

                att_id = att["attachmentId"]
                filename = att.get("filename") or f"attachment-{att_id}"
                mime = att.get("mimeType") or ""
                size = int(att.get("size") or 0)

                # Filtres MIME / taille
                if allowed_mimes and mime not in allowed_mimes:
                    _log_event(metrics, log_dir, "info", "MIME not allowed; quarantining", filename=filename, mime=mime, size=size)
                    metrics["quarantined"] += 1
                    if not dry_run:
                        # On récupère quand même le binaire pour le déposer en quarantine
                        try:
                            content = _get_attachment(service, msg["id"], att_id)
                            metrics["gmail_api_calls"] += 1
                            (quarantine_day_dir / filename).write_bytes(content)
                        except Exception as e:
                            metrics["errors_count"] += 1
                            _log_event(metrics, log_dir, "error", "Failed to fetch disallowed attachment", filename=filename, error=str(e))
                    continue

                if size > max_size:
                    _log_event(metrics, log_dir, "info", "Too large; quarantining", filename=filename, size=size)
                    metrics["quarantined"] += 1
                    continue

                # Récupérer le contenu
                try:
                    content = _get_attachment(service, msg["id"], att_id)
                    metrics["gmail_api_calls"] += 1
                except Exception as e:
                    metrics["errors_count"] += 1
                    _log_event(metrics, log_dir, "error", "Failed to fetch attachment", filename=filename, error=str(e))
                    continue

                content_hash = _sha256_bytes(content)

                # Déduplication par gmail_attachment_id + source, puis par content_hash
                exists = Receipt.objects.filter(source="gmail", gmail_attachment_id=att_id).exists()
                if exists:
                    metrics["duplicates_skipped"] += 1
                    _log_event(metrics, log_dir, "info", "Duplicate by gmail_attachment_id", attachment_id=att_id, filename=filename)
                    continue
                exists = Receipt.objects.filter(content_hash=content_hash).exists()
                if exists:
                    metrics["duplicates_skipped"] += 1
                    _log_event(metrics, log_dir, "info", "Duplicate by content_hash", content_hash=content_hash, filename=filename)
                    continue

                # Écriture fichier
                dest_path = incoming_day_dir / filename
                if not dry_run:
                    dest_path.write_bytes(content)
                metrics["attachments_downloaded"] += 1

                # Création Receipt
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
                                sender=sender,
                                subject=subject,
                                received_at=received_at,
                            )
                        metrics["receipts_created"] += 1
                    except IntegrityError as e:
                        metrics["duplicates_skipped"] += 1
                        _log_event(metrics, log_dir, "info", "IntegrityError on create (probable dup)", error=str(e))
                        # on peut supprimer le fichier écrit si nécessaire
                        try:
                            if dest_path.exists():
                                dest_path.unlink()
                        except Exception:
                            pass

                attach_budget -= 1

            # Option : marquer le message comme lu / labelliser (après traitement)
            try:
                if cfg.get("APPLY_LABELS") or cfg.get("MARK_AS_READ"):
                    mods = {"addLabelIds": [], "removeLabelIds": []}
                    if cfg.get("MARK_AS_READ"):
                        mods["removeLabelIds"].append("UNREAD")
                    # Labels nommés ne marchent pas directement; ici, on utilise UNREAD builtin.
                    # Pour des labels custom (pobs/imported), il faudrait mapper name->id (users.labels.list + create si absent).
                    if mods["addLabelIds"] or mods["removeLabelIds"]:
                        service.users().messages().modify(userId="me", id=msg["id"], body=mods).execute()
                        metrics["gmail_api_calls"] += 1
            except HttpError as e:
                metrics["errors_count"] += 1
                _log_event(metrics, log_dir, "error", "Failed to modify message labels", error=str(e))

        status = JobRun.Status.SUCCESS if metrics["errors_count"] == 0 else JobRun.Status.FAILED

    except Exception as e:
        metrics["errors_count"] += 1
        _log_event(metrics, log_dir, "error", "Unhandled exception", error=str(e))
        status = JobRun.Status.FAILED

    finally:
        elapsed = int((time.time() - started) * 1000)
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
