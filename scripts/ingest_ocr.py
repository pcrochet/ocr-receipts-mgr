#!/usr/bin/env python3
"""
Ingestion OCR -> Postgres (sans embeddings, étape 2)
- Parcourt un dossier d'entrée (--input)
- Pour chaque .txt : calcule sha256, insère receipt + lignes, t_ingest_ms, event "ingest"
- Idempotence: UNIQUE(sha256) sur ocr.receipts -> les doublons sont ignorés proprement
"""
import argparse
import hashlib
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.rows import tuple_row
from dotenv import load_dotenv

# --- Config DB depuis .env (compatible Windows / WSL2) ---
load_dotenv()  # lit le .env à la racine du repo
PGHOST = os.getenv("PGHOST", "localhost")
PGPORT = int(os.getenv("PGPORT", "5432"))
PGUSER = os.getenv("POSTGRES_USER", "app")
PGPASS = os.getenv("POSTGRES_PASSWORD", "app")
PGDB   = os.getenv("POSTGRES_DB", "app")

LOG_DIR_DEFAULT = "data/logs"

def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()

def read_txt(path: Path) -> str:
    # Supporte CRLF/LF et encodages bizarres sans planter
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        return f.read()

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def log_line(log_dir: Path, msg: str) -> None:
    ensure_dir(log_dir)
    day = datetime.now().strftime("%Y-%m-%d")
    fp = log_dir / f"ingest_{day}.log"
    with fp.open("a", encoding="utf-8") as f:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{ts}] {msg}\n")

def ingest_one(conn: psycopg.Connection, txt_path: Path, log_dir: Path) -> None:
    started = time.perf_counter()
    raw_text = read_txt(txt_path)
    digest = sha256_text(raw_text)
    lines = raw_text.splitlines()  # conserve les lignes telles quelles

    try:
        with conn.transaction():
            # 1) receipt (met t_ingest_ms=0 provisoirement)
            rec_sql = """
                INSERT INTO ocr.receipts (uuid_root, source_file, sha256, raw_text, state, t_ingest_ms)
                VALUES (gen_random_uuid(), %s, %s, %s, 'ingested', 0)
                RETURNING id
            """
            cur = conn.execute(rec_sql, (txt_path.name, digest, raw_text))
            (receipt_id,) = cur.fetchone()  # tuple_row par défaut

            # 2) lines
            line_sql = """
                INSERT INTO ocr.receipt_lines (receipt_id, line_number, text)
                VALUES (%s, %s, %s)
                ON CONFLICT (receipt_id, line_number) DO NOTHING
            """
            for i, line in enumerate(lines, start=1):
                conn.execute(line_sql, (receipt_id, i, line))

            # 3) timings + event
            t_ms = int((time.perf_counter() - started) * 1000)
            conn.execute("UPDATE ocr.receipts SET t_ingest_ms=%s WHERE id=%s", (t_ms, receipt_id))
            conn.execute(
                """
                INSERT INTO ocr.processing_events (receipt_id, step, status, started_at, finished_at, duration_ms, message)
                VALUES (%s, 'ingest', 'ok', NOW(), NOW(), %s, %s)
                """,
                (receipt_id, t_ms, f"ingested {txt_path.name} ({len(lines)} lines)"),
            )

        log_line(log_dir, f"OK ingest {txt_path.name} sha256={digest} lines={len(lines)} t_ms={t_ms}")

    except psycopg.errors.UniqueViolation:
        # doublon sur sha256 : skip silencieux + log + event
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO ocr.processing_events (step, status, started_at, finished_at, duration_ms, message)
                VALUES ('ingest', 'ok', NOW(), NOW(), 0, %s)
                """,
                (f"skip duplicate {txt_path.name} sha256={digest}",),
            )
        log_line(log_dir, f"SKIP duplicate {txt_path.name} sha256={digest}")

def main():
    ap = argparse.ArgumentParser(description="Ingestion OCR -> Postgres (sans embeddings)")
    ap.add_argument("--input", required=True, help="Répertoire contenant des .txt")
    ap.add_argument("--log", default=LOG_DIR_DEFAULT, help="Répertoire logs (def: data/logs)")
    args = ap.parse_args()

    inbox = Path(args.input)
    if not inbox.is_dir():
        print(f"Erreur: --input {inbox} n'est pas un répertoire", file=sys.stderr)
        sys.exit(2)
    log_dir = Path(args.log); ensure_dir(log_dir)

    conn_str = f"host={PGHOST} port={PGPORT} user={PGUSER} password={PGPASS} dbname={PGDB}"
    try:
        with psycopg.connect(conn_str, autocommit=False, row_factory=tuple_row) as conn:
            count = 0
            for p in sorted(inbox.glob("*.txt")):
                ingest_one(conn, p, log_dir)
                count += 1
            print(f"Ingestion terminée: {count} fichiers.")
    except Exception as e:
        print(f"Échec connexion/traitement: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
