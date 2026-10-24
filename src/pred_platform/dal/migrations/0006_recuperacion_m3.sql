-- Recuperacion de M3: protocolo congelado y resultado durable por ventana.
-- Se aplica despues del esquema inicial de evidencia, sin modificarlo.
-- El motor es dueno del texto; la plataforma aplica las mismas migraciones.

CREATE TABLE m3_protocolos (
    run_id               TEXT PRIMARY KEY REFERENCES m3_corridas (run_id),
    ejecucion_id         INTEGER REFERENCES ejecuciones (id),
    protocolo            TEXT NOT NULL,
    manifiesto           TEXT NOT NULL,
    procedencia          TEXT NOT NULL,
    reserva_abierta_en   TEXT NOT NULL,
    n_unidades_previstas INTEGER NOT NULL CHECK (n_unidades_previstas >= 0)
);

CREATE TABLE m3_cartera (
    run_id           TEXT NOT NULL REFERENCES m3_corridas (run_id),
    sku_class        TEXT NOT NULL,
    familia          TEXT NOT NULL,
    metrica          TEXT NOT NULL,
    n_skus           INTEGER NOT NULL,
    n_instancias     INTEGER NOT NULL,
    n_calculables    INTEGER NOT NULL,
    n_no_calculables INTEGER NOT NULL,
    media            REAL,
    mediana          REAL,
    p25              REAL,
    p75              REAL,
    PRIMARY KEY (run_id, sku_class, familia, metrica)
);

ALTER TABLE m3_unidades ADD COLUMN estado TEXT NOT NULL DEFAULT 'confirmada'
    CHECK (estado IN ('confirmada', 'fallida'));
ALTER TABLE m3_unidades ADD COLUMN causa TEXT;
ALTER TABLE m3_unidades ADD COLUMN emitido_en TEXT;
ALTER TABLE m3_unidades ADD COLUMN codigo_version TEXT;
ALTER TABLE m3_unidades ADD COLUMN semilla INTEGER;

CREATE TRIGGER m3_unidad_fallida_con_causa
BEFORE INSERT ON m3_unidades
WHEN (NEW.estado = 'fallida') != (NEW.causa IS NOT NULL)
BEGIN
    SELECT RAISE(ABORT, 'una unidad fallida necesita causa y una confirmada no la admite');
END;
