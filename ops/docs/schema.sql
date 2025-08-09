-- === Préambule : extensions requises ===
CREATE EXTENSION IF NOT EXISTS vector;          -- pgvector
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS pgcrypto;        -- gen_random_uuid()

-- === Types d’état ===
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'receipt_state') THEN
    CREATE TYPE receipt_state AS ENUM (
      'ingested',
      'brand-2-validate','brand-validated',
      'products-2-validate','products-validated',
      'error'
    );
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'line_validation') THEN
    CREATE TYPE line_validation AS ENUM ('pending','validated','rejected');
  END IF;
END$$;

-- === Schéma logique (optionnel) ===
CREATE SCHEMA IF NOT EXISTS ocr;

-- === Fonction/trigger pour updated_at ===
CREATE OR REPLACE FUNCTION ocr.set_updated_at()
RETURNS trigger AS $$
BEGIN
  NEW.updated_at := NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- === Table des marques ===
CREATE TABLE IF NOT EXISTS ocr.brands (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name       TEXT NOT NULL UNIQUE,
  -- alias textuels pour heuristiques regex / recherche textuelle
  aliases    TEXT[] DEFAULT '{}',
  -- métadonnées libres (ex: source, site web, codes internes)
  meta       JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
DROP TRIGGER IF EXISTS trg_brands_updated ON ocr.brands;
CREATE TRIGGER trg_brands_updated
BEFORE UPDATE ON ocr.brands
FOR EACH ROW EXECUTE FUNCTION ocr.set_updated_at();

-- Aliases avec embeddings (1 ligne = 1 alias)
CREATE TABLE IF NOT EXISTS ocr.brand_aliases (
  id         BIGSERIAL PRIMARY KEY,
  brand_id   UUID NOT NULL REFERENCES ocr.brands(id) ON DELETE CASCADE,
  alias      TEXT NOT NULL,
  embedding  VECTOR(384),             -- optionnel, pour recherche vectorielle directe
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_brand_alias
  ON ocr.brand_aliases(brand_id, alias);

-- === Tickets (receipts) ===
CREATE TABLE IF NOT EXISTS ocr.receipts (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  uuid_root    UUID NOT NULL DEFAULT gen_random_uuid(),   -- identifiant métier si besoin
  source_file  TEXT NOT NULL,                             -- nom du .txt d’origine
  sha256       TEXT NOT NULL,                             -- idempotence
  raw_text     TEXT NOT NULL,                             -- OCR brut
  embedding    VECTOR(384),                               -- embedding du ticket complet
  brand        JSONB DEFAULT NULL,                        -- {"brand_id": "...", "name":"...", "score":0.87, "method":"hybrid"}
  embedding_meta JSONB NOT NULL DEFAULT '{"model":"all-MiniLM-L6-v2","dim":384}'::jsonb,
  state        receipt_state NOT NULL DEFAULT 'ingested',

  -- timings ms (mesurés côté pipeline et enregistrés ici)
  t_ingest_ms  INTEGER,
  t_embed_ms   INTEGER,
  t_brand_ms   INTEGER,
  t_parse_ms   INTEGER,

  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT uq_receipts_sha256 UNIQUE (sha256)
);
DROP TRIGGER IF EXISTS trg_receipts_updated ON ocr.receipts;
CREATE TRIGGER trg_receipts_updated
BEFORE UPDATE ON ocr.receipts
FOR EACH ROW EXECUTE FUNCTION ocr.set_updated_at();

-- Index pratiques
CREATE INDEX IF NOT EXISTS idx_receipts_state       ON ocr.receipts(state);
CREATE INDEX IF NOT EXISTS idx_receipts_sourcefile  ON ocr.receipts(source_file);
CREATE INDEX IF NOT EXISTS idx_receipts_brand_name  ON ocr.receipts ((brand->>'name'));

-- ANN sur l’embedding du ticket (cosine)
-- NOTE: créer l’index IVFFLAT après avoir inséré un minimum de lignes améliore la qualité des listes.
CREATE INDEX IF NOT EXISTS idx_receipts_embedding_ivfflat
ON ocr.receipts
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- === Lignes de ticket ===
CREATE TABLE IF NOT EXISTS ocr.receipt_lines (
  id              BIGSERIAL PRIMARY KEY,
  receipt_id      UUID NOT NULL REFERENCES ocr.receipts(id) ON DELETE CASCADE,
  line_number     INT  NOT NULL,
  text            TEXT NOT NULL,

  -- Extraction V1 (heuristiques)
  item_name       TEXT,
  item_brand      TEXT,
  quantity        NUMERIC,           -- null si inconnu ; sinon 1.0 par défaut
  unit            TEXT,              -- ex: "x125g", "500g", "L"
  price_eur       NUMERIC(10,2),
  category        TEXT,

  -- Vectorisation
  embedding       VECTOR(384),
  embedding_meta  JSONB NOT NULL DEFAULT '{"model":"all-MiniLM-L6-v2","dim":384}'::jsonb,

  validation      line_validation NOT NULL DEFAULT 'pending',

  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT uq_line_unique_per_receipt UNIQUE (receipt_id, line_number)
);
DROP TRIGGER IF EXISTS trg_receipt_lines_updated ON ocr.receipt_lines;
CREATE TRIGGER trg_receipt_lines_updated
BEFORE UPDATE ON ocr.receipt_lines
FOR EACH ROW EXECUTE FUNCTION ocr.set_updated_at();

-- ANN sur l’embedding des lignes
CREATE INDEX IF NOT EXISTS idx_receipt_lines_embedding_ivfflat
ON ocr.receipt_lines
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

CREATE INDEX IF NOT EXISTS idx_receipt_lines_receipt  ON ocr.receipt_lines(receipt_id);
CREATE INDEX IF NOT EXISTS idx_receipt_lines_category ON ocr.receipt_lines(category);

-- === Journal des traitements (traçabilité fine + perf) ===
CREATE TABLE IF NOT EXISTS ocr.processing_events (
  id            BIGSERIAL PRIMARY KEY,
  receipt_id    UUID REFERENCES ocr.receipts(id) ON DELETE CASCADE,
  line_id       BIGINT REFERENCES ocr.receipt_lines(id) ON DELETE CASCADE,
  step          TEXT NOT NULL,         -- "ingest" | "embed" | "brand" | "parse" | ...
  status        TEXT NOT NULL,         -- "ok" | "error"
  started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at   TIMESTAMPTZ,
  duration_ms   INTEGER,
  message       TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_receipt ON ocr.processing_events(receipt_id);
CREATE INDEX IF NOT EXISTS idx_events_step    ON ocr.processing_events(step);
CREATE INDEX IF NOT EXISTS idx_events_status  ON ocr.processing_events(status);

-- === Requêtes utilitaires ===

-- Similarité (cosine) sur tickets
-- SELECT id, 1 - (embedding <=> $1::vector) AS cosine_sim
-- FROM ocr.receipts
-- ORDER BY embedding <-> $1::vector
-- LIMIT 10;

-- Similarité sur lignes
-- SELECT id, receipt_id, line_number, 1 - (embedding <=> $1::vector) AS cosine_sim
-- FROM ocr.receipt_lines
-- ORDER BY embedding <-> $1::vector
-- LIMIT 10;

-- === (Optionnel) Utilisateur pour l'exporter Prometheus si besoin ===
-- CREATE USER postgres_exporter WITH PASSWORD 'postgres_exporter';
-- GRANT CONNECT ON DATABASE your_db TO postgres_exporter;
-- GRANT USAGE ON SCHEMA ocr TO postgres_exporter;
-- GRANT SELECT ON ALL TABLES IN SCHEMA ocr TO postgres_exporter;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA ocr GRANT SELECT ON TABLES TO postgres_exporter;
