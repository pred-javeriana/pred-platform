-- Ausencia de declaracion: el bit datos_sinteticos no declara datos reales.
-- Las filas historicas conservan su declaracion y su evidencia sin cambios.
ALTER TABLE m3_ingestas ADD COLUMN origen_declarado INTEGER NOT NULL DEFAULT 1
    CHECK (origen_declarado IN (0, 1) AND (origen_declarado = 1 OR datos_sinteticos = 0));
ALTER TABLE m3_corridas ADD COLUMN origen_declarado INTEGER NOT NULL DEFAULT 1
    CHECK (origen_declarado IN (0, 1) AND (origen_declarado = 1 OR datos_sinteticos = 0));
