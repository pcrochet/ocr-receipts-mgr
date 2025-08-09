#!/usr/bin/env python3
"""
Vectorisation des tickets et lignes -> Postgres (pgvector)
- Sélectionne receipts sans embedding, calcule l'embedding du ticket + de ses lignes.
- Stocke dans ocr.receipts.embedding et ocr.receipt_lines.embedding.
- Ajoute un event 'embed' avec la durée.
Requis: sentence-transformers, torch, psycopg, python-dotenv
"""
import os, time, argparse
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

from sentence_transformers import SentenceTransformer

load_dotenv()
PGHOST = os.getenv("PGHOST", "localhost")
PGPORT = int(os.getenv("PGPORT", "5432"))
PGUSER = os.getenv("POSTGRES_USER", "app")
PGPASS = os.getenv("POSTGRES_PASSWORD", "app")
PGDB   = os.getenv("POSTGRES_DB", "app")

MODEL_NAME = os.getenv("EMB_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
EMB_DIM    = int(os.getenv("EMB_DIM", "384"))

def vec_to_sql(v) -> str:
    # pgvector accepte le format texte: [0.1,0.2,...]
    return "[" + ",".join(f"{float(x):.7f}" for x in v) + "]"

def fetch_receipts_without_embedding(conn, limit: int):
    q = """
    SELECT id, raw_text
    FROM ocr.receipts
    WHERE embedding IS NULL
    ORDER BY created_at
    LIMIT %s
    """
    return list(conn.execute(q, (limit,)).fetchall())

def fetch_lines_for_receipt(conn, receipt_id):
    q = """
    SELECT id, text
    FROM ocr.receipt_lines
    WHERE receipt_id = %s AND embedding IS NULL
    ORDER BY line_number
    """
    return list(conn.execute(q, (receipt_id,)).fetchall())

def main():
    ap = argparse.ArgumentParser(description="Vectorize receipts & lines into pgvector")
    ap.add_argument("--batch", type=int, default=16, help="Nb receipts par itération (def: 16)")
    ap.add_argument("--limit", type=int, default=2000000, help="Nb max receipts à traiter")
    args = ap.parse_args()

    print(f"Loading model: {MODEL_NAME} …")
    model = SentenceTransformer(MODEL_NAME)

    conn_str = f"host={PGHOST} port={PGPORT} user={PGUSER} password={PGPASS} dbname={PGDB}"
    total = 0
    with psycopg.connect(conn_str, autocommit=False, row_factory=dict_row) as conn:
        while total < args.limit:
            receipts = fetch_receipts_without_embedding(conn, min(args.batch, args.limit - total))
            if not receipts:
                break

            for rec in receipts:
                rid = rec["id"]; text = rec["raw_text"] or ""
                t0 = time.perf_counter()

                # 1) embedding ticket
                v_rec = model.encode(text, normalize_embeddings=True).tolist()
                vs_rec = vec_to_sql(v_rec)

                # 2) embeddings lignes
                lines = fetch_lines_for_receipt(conn, rid)
                texts = [row["text"] or "" for row in lines]
                if texts:
                    v_lines = model.encode(texts, normalize_embeddings=True)
                else:
                    v_lines = []

                # 3) write
                with conn.transaction():
                    conn.execute(
                        "UPDATE ocr.receipts SET embedding = %s::vector WHERE id = %s",
                        (vs_rec, rid),
                    )
                    if lines:
                        up_sql = "UPDATE ocr.receipt_lines SET embedding = %s::vector WHERE id = %s"
                        for vec, row in zip(v_lines, lines):
                            conn.execute(up_sql, (vec_to_sql(vec.tolist()), row["id"]))

                    t_ms = int((time.perf_counter() - t0) * 1000)
                    # store timing + event
                    conn.execute("UPDATE ocr.receipts SET t_embed_ms = %s WHERE id = %s", (t_ms, rid))
                    conn.execute(
                        """
                        INSERT INTO ocr.processing_events (receipt_id, step, status, started_at, finished_at, duration_ms, message)
                        VALUES (%s, 'embed', 'ok', NOW(), NOW(), %s, %s)
                        """,
                        (rid, t_ms, f"embedded receipt + {len(lines)} lines"),
                    )

                total += 1

            print(f"Processed {total} receipts…")

    print("Done.")

if __name__ == "__main__":
    main()
