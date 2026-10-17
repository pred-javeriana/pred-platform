-- Migration 0004: the selection of each SKU, its HPO trials and its Walk-Forward evidence
-- (TASK-UI-1.0-A3-G4).
--
-- The engine delivers one `seleccion.jsonl` per run (contract section 20, `pred-engine.seleccion/1`)
-- and the worker imports it when the run closes. Per run and SKU there is one `selecciones` row;
-- per family (always the four of them) one `seleccion_familias` row with its state, the
-- evidence of the chosen configuration and the Walk-Forward summary; the trials of its HPO study
-- go to `ensayos` and the windows to `ventanas_wf`. The metrics of every window are `metricas`
-- rows linked to the family's task (`tareas`, decision D6), so `ventana` is added there.
-- The champion is not here: M3 will write `resultados_comparativos`.
--
-- `evidencia_json`, `configuracion_pronostico_json`, `error_json` and `configuracion_json` hold
-- documents validated by the importer against the contract's models (decision D5: JSON with the
-- version of the contract that wrote it). Dates are ISO 8601 text. This migration only adds
-- tables, one column and one index: existing rows are untouched.

CREATE TABLE selecciones (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ejecucion_id     INTEGER NOT NULL REFERENCES ejecuciones(id),
    sku              TEXT    NOT NULL,
    clase            TEXT    NOT NULL
                         CHECK (clase IN ('smooth', 'intermittent', 'erratic', 'lumpy')),
    perfil           TEXT    NOT NULL
                         CHECK (perfil IN ('dense_stable', 'dense_variable',
                                           'sparse_stable', 'sparse_variable')),
    version_politica TEXT,
    UNIQUE (ejecucion_id, sku)
);

CREATE TABLE seleccion_familias (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    seleccion_id                  INTEGER NOT NULL REFERENCES selecciones(id),
    familia                       TEXT    NOT NULL
                                      CHECK (familia IN ('classical', 'ml', 'dl', 'foundation')),
    estado                        TEXT    NOT NULL
                                      CHECK (estado IN ('pendiente', 'ejecutando', 'exitosa',
                                                        'fallida', 'no_ejecutable', 'excluida')),
    -- The unit of this family (SKU x model, D6) when it ran or was meant to run.
    tarea_id                      INTEGER REFERENCES tareas(id),
    modelo                        TEXT,
    producido_por                 TEXT,
    exclusion_codigo              TEXT,
    exclusion_detalle             TEXT,
    evidencia_json                TEXT,
    configuracion_pronostico_json TEXT,
    semilla_pronostico            INTEGER,
    error_json                    TEXT,
    -- 1 when the study's trials were delivered (even if there are none): 0 reads as "no study".
    ensayos_persistidos           INTEGER NOT NULL DEFAULT 0 CHECK (ensayos_persistidos IN (0, 1)),
    -- Walk-Forward summary; `metrica_objetivo` NULL means the family has none.
    metrica_objetivo              TEXT,
    valor_agregado                REAL,
    completo                      INTEGER CHECK (completo IN (0, 1)),
    n_ventanas_evaluadas          INTEGER,
    n_ventanas_totales            INTEGER,
    alcance_seleccion             TEXT,
    comparacion_estadistica       INTEGER CHECK (comparacion_estadistica IN (0, 1)),
    UNIQUE (seleccion_id, familia)
);

-- Engine vocabulary for the state of a trial (decision D1); the interface words it.
CREATE TABLE ensayos (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    seleccion_familia_id  INTEGER NOT NULL REFERENCES seleccion_familias(id),
    orden                 INTEGER NOT NULL,
    trial_id              TEXT    NOT NULL,
    configuracion_json    TEXT    NOT NULL,
    estado                TEXT    NOT NULL
                              CHECK (estado IN ('pendiente', 'corriendo', 'completado',
                                                'podado', 'fallido')),
    valor                 REAL,
    n_ventanas            INTEGER NOT NULL,
    motivo                TEXT,
    instante              TEXT,
    UNIQUE (seleccion_familia_id, trial_id)
);

CREATE TABLE ventanas_wf (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    seleccion_familia_id  INTEGER NOT NULL REFERENCES seleccion_familias(id),
    indice                INTEGER NOT NULL,
    corte                 TEXT    NOT NULL,
    inicio_train          INTEGER NOT NULL,
    fin_train             INTEGER NOT NULL,
    inicio_val            INTEGER NOT NULL,
    fin_val               INTEGER NOT NULL,
    duracion_s            REAL,
    fallo                 TEXT,
    UNIQUE (seleccion_familia_id, indice)
);

-- NULL: the aggregated metric of the task; a number: the window's index (`ventanas_wf.indice`).
ALTER TABLE metricas ADD COLUMN ventana INTEGER;

CREATE INDEX ix_selecciones_ejecucion ON selecciones (ejecucion_id, clase);
CREATE INDEX ix_metricas_tarea_ventana ON metricas (tarea_id, ventana);
