# scripts/detect_brand.py
import os, re, time, argparse
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json
from dotenv import load_dotenv

load_dotenv()
PGHOST=os.getenv("PGHOST","localhost"); PGPORT=int(os.getenv("PGPORT","5432"))
PGUSER=os.getenv("POSTGRES_USER","app"); PGPASS=os.getenv("POSTGRES_PASSWORD","app")
PGDB=os.getenv("POSTGRES_DB","app")

def best_brand_for_receipt(conn, receipt_id):
    rows = list(conn.execute("""
        SELECT id, text, embedding FROM ocr.receipt_lines
        WHERE receipt_id = %s
    """, (receipt_id,)).fetchall())
    if not rows:
        return None

    aliases = list(conn.execute("""
        SELECT ba.embedding AS emb, ba.alias AS alias, b.id AS brand_id, b.name AS brand_name
        FROM ocr.brand_aliases ba
        JOIN ocr.brands b ON b.id = ba.brand_id
    """).fetchall())
    if not aliases:
        return None

    best = {"brand_id": None, "brand_name": None, "score_vec": 0.0, "alias": None}

    for a in aliases:
        emb_alias = a["emb"]           # <- lire via clés
        alias     = a["alias"]
        brand_id  = a["brand_id"]
        brand_name= a["brand_name"]

        row = conn.execute(
            """
            SELECT 1 - (rl.embedding <=> %s::vector) AS cos
            FROM ocr.receipt_lines rl
            WHERE rl.receipt_id = %s AND rl.embedding IS NOT NULL
            ORDER BY rl.embedding <-> %s::vector
            LIMIT 1
            """,
            (emb_alias, receipt_id, emb_alias)
        ).fetchone()
        if not row:
            continue
        cos = float(row["cos"] or 0.0)

        if cos > best["score_vec"]:
            best.update({"brand_id": brand_id, "brand_name": brand_name, "score_vec": cos, "alias": alias})

    raw = "\n".join((r["text"] or "") for r in rows)
    regex_bonus = 0.0
    if best["brand_name"]:
        # +0.2 si l'alias exact est trouvé, +0.1 si le nom de marque est trouvé
        if re.search(rf"\b{re.escape(best['alias'])}\b", raw, re.IGNORECASE): regex_bonus += 0.2
        if re.search(rf"\b{re.escape(best['brand_name'])}\b", raw, re.IGNORECASE): regex_bonus += 0.1
        regex_bonus = min(regex_bonus, 0.3)

    # 5) score final
    score_final = max(0.0, min(1.0, 0.8*best["score_vec"] + 0.2*regex_bonus))
    return {
        "brand_id": best["brand_id"],
        "name": best["brand_name"],
        "score_vec": round(best["score_vec"], 4),
        "regex_bonus": round(regex_bonus, 3),
        "score": round(score_final, 4),
        "alias": best["alias"],
    }

def main():
    ap = argparse.ArgumentParser(description="Detect store brand for receipts")
    ap.add_argument("--limit", type=int, default=1000, help="Nb max de tickets à traiter")
    args = ap.parse_args()

    conn = psycopg.connect(
        f"host={PGHOST} port={PGPORT} user={PGUSER} password={PGPASS} dbname={PGDB}",
        autocommit=False, row_factory=dict_row
    )

    total=0
    with conn:
        # ne traite que ceux sans brand
        receipts = list(conn.execute("""
            SELECT id FROM ocr.receipts
            WHERE brand IS NULL
            ORDER BY created_at
            LIMIT %s
        """, (args.limit,)).fetchall())

        for r in receipts:
            rid = r["id"]
            t0 = time.perf_counter()
            try:
                res = best_brand_for_receipt(conn, rid)
                if res and res["brand_id"]:
                    conn.execute(
                        "UPDATE ocr.receipts SET brand=%s::jsonb, state='brand-2-validate', t_brand_ms=%s WHERE id=%s",
                        (Json(res), int((time.perf_counter()-t0)*1000), rid)
                    )
                    conn.execute(
                        "INSERT INTO ocr.processing_events (receipt_id, step, status, started_at, finished_at, duration_ms, message) VALUES (%s,'brand','ok',NOW(),NOW(),%s,%s)",
                        (rid, int((time.perf_counter()-t0)*1000), f"brand={res['name']} score={res['score']} via {res['alias']}")
                    )
                else:
                    conn.execute(
                        "INSERT INTO ocr.processing_events (receipt_id, step, status, started_at, finished_at, duration_ms, message) VALUES (%s,'brand','error',NOW(),NOW(),NULL,'no-brand-found')",
                        (rid,)
                    )
                total += 1
            except Exception as e:
                # rollback la transaction courante avant d'écrire l'event
                conn.rollback()
                conn.execute(
                    "INSERT INTO ocr.processing_events (receipt_id, step, status, started_at, finished_at, duration_ms, message) VALUES (%s,'brand','error',NOW(),NOW(),NULL,%s)",
                    (rid, str(e))
                )
    print(f"Processed {total} receipts.")

if __name__ == "__main__":
    main()
