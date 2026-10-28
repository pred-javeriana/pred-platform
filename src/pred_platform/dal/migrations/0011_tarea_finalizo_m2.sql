-- Una tarea que terminó M2 (L2 y L3) espera la evaluación de M3 en `finalizo_m2`, y queda
-- `exitosa` solo cuando M3 guarda su candidato (contrato 1.2.0). SQLite no altera un CHECK:
-- se reconstruye la tabla con los mismos id, columnas e índice. El motor conoce esta versión.
CREATE TABLE tareas_nueva (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ejecucion_id    INTEGER NOT NULL REFERENCES ejecuciones(id),
    sku             TEXT,
    modelo          TEXT    NOT NULL,
    corte           TEXT    NOT NULL,
    estado          TEXT    NOT NULL DEFAULT 'pendiente'
                        CHECK (estado IN ('pendiente', 'ejecutando', 'finalizo_m2', 'exitosa',
                                          'fallida', 'no_ejecutable')),
    seed            INTEGER NOT NULL,
    tiempo_pared_s  REAL,
    detalle_error   TEXT,
    iniciada_en     TEXT,
    finalizada_en   TEXT,
    tiempo_l2_s     REAL,
    UNIQUE (ejecucion_id, sku, modelo, corte)
);

INSERT INTO tareas_nueva (id, ejecucion_id, sku, modelo, corte, estado, seed, tiempo_pared_s,
                          detalle_error, iniciada_en, finalizada_en, tiempo_l2_s)
SELECT id, ejecucion_id, sku, modelo, corte, estado, seed, tiempo_pared_s,
       detalle_error, iniciada_en, finalizada_en, tiempo_l2_s
FROM tareas;

-- AUTOINCREMENT no reutiliza ids de tareas borradas: se conserva su contador.
UPDATE sqlite_sequence SET seq = (SELECT seq FROM sqlite_sequence WHERE name = 'tareas')
WHERE name = 'tareas_nueva' AND EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'tareas');

DROP TABLE tareas;
ALTER TABLE tareas_nueva RENAME TO tareas;
CREATE INDEX ix_tareas_ejecucion_estado ON tareas (ejecucion_id, estado);
