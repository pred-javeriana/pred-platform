-- Migration 0002: ingest report and demand topology (TASK-UI-1.0-A3-G1).
--
-- The engine leaves one report per ingestion (data/ingestas/<sha256>.json) and the platform
-- imports it. `ingestas` gains the report's scalar fields (status, deposit, published Parquet,
-- reserve cut, classifier thresholds); `series` gains the per-SKU metrics. Existing rows keep
-- NULL in the new columns. The legacy `series.familia` and `series.perfil_demanda` are left
-- unused (decision D4: Syntetos-Boylan, no ABC-XYZ profile).

ALTER TABLE ingestas ADD COLUMN estado TEXT
    CHECK (estado IN ('accepted', 'rejected', 'failed', 'needs_confirmation'));
ALTER TABLE ingestas ADD COLUMN estado_deposito TEXT
    CHECK (estado_deposito IN ('deposited', 'already_present_identical',
                               'already_present_different'));
ALTER TABLE ingestas ADD COLUMN deposito_colision INTEGER
    CHECK (deposito_colision IN (0, 1));
ALTER TABLE ingestas ADD COLUMN ruta_raw TEXT;
ALTER TABLE ingestas ADD COLUMN ruta_parquet TEXT;
ALTER TABLE ingestas ADD COLUMN filas_validadas INTEGER;
ALTER TABLE ingestas ADD COLUMN filas_panel INTEGER;
ALTER TABLE ingestas ADD COLUMN t_star TEXT;
ALTER TABLE ingestas ADD COLUMN umbral_adi REAL;
ALTER TABLE ingestas ADD COLUMN umbral_cv2 REAL;
ALTER TABLE ingestas ADD COLUMN diagnostico_json TEXT;
ALTER TABLE ingestas ADD COLUMN error_json TEXT;
ALTER TABLE ingestas ADD COLUMN publicada_en TEXT;

ALTER TABLE series ADD COLUMN adi REAL;
ALTER TABLE series ADD COLUMN cv2 REAL;
ALTER TABLE series ADD COLUMN n_positive INTEGER;
ALTER TABLE series ADD COLUMN clase TEXT
    CHECK (clase IN ('smooth', 'intermittent', 'erratic', 'lumpy'));

CREATE INDEX IF NOT EXISTS ix_series_ingesta_clase ON series (ingesta_id, clase);
