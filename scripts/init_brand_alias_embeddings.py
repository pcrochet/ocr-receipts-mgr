# scripts/init_brand_alias_embeddings.py
import os
from dotenv import load_dotenv
import psycopg
from sentence_transformers import SentenceTransformer

load_dotenv()
PGHOST=os.getenv("PGHOST","localhost"); PGPORT=int(os.getenv("PGPORT","5432"))
PGUSER=os.getenv("POSTGRES_USER","app"); PGPASS=os.getenv("POSTGRES_PASSWORD","app")
PGDB=os.getenv("POSTGRES_DB","app")
MODEL=os.getenv("EMB_MODEL","sentence-transformers/all-MiniLM-L6-v2")

def vec_to_sql(v): return "[" + ",".join(f"{float(x):.7f}" for x in v) + "]"

def main():
    model = SentenceTransformer(MODEL)
    conn = psycopg.connect(f"host={PGHOST} port={PGPORT} user={PGUSER} password={PGPASS} dbname={PGDB}", autocommit=False)
    with conn, conn.cursor() as cur:
        # vider et regénérer
        cur.execute("DELETE FROM ocr.brand_aliases;")
        cur.execute("SELECT id, name, aliases FROM ocr.brands;")
        brands = cur.fetchall()
        for brand_id, name, aliases in brands:
            items = [name] + (aliases or [])
            embeds = model.encode(items, normalize_embeddings=True)
            for alias, vec in zip(items, embeds):
                cur.execute(
                    "INSERT INTO ocr.brand_aliases (brand_id, alias, embedding) VALUES (%s,%s,%s::vector)",
                    (brand_id, alias, vec_to_sql(vec.tolist()))
                )
    print("Done.")
if __name__ == "__main__":
    main()
