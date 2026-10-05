-- Migration 0001: initial schema (SRS 3.6), the eleven tables the DAL had before versioning.
--
-- The statements keep `IF NOT EXISTS` on purpose: a database created before migrations existed
-- (user_version 0, tables already there) adopts version 1 unchanged, and an empty one gets the
-- tables. Released migrations are never edited; later changes are new files (ADR-05-002).

CREATE TABLE IF NOT EXISTS usuarios (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT    NOT NULL UNIQUE,
    password_hash TEXT  NOT NULL,
    rol         TEXT    NOT NULL CHECK (rol IN ('Operador', 'Administrador')),
    activo      INTEGER NOT NULL DEFAULT 1,
    creado_en   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS configuraciones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    version     INTEGER NOT NULL,
    descripcion TEXT,
    parametros  TEXT    NOT NULL,
    creado_en   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ingestas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256      TEXT    NOT NULL UNIQUE,
    nombre_archivo TEXT NOT NULL,
    filas       INTEGER NOT NULL,
    skus        INTEGER NOT NULL,
    fecha_inicio TEXT,
    fecha_fin   TEXT,
    creado_en   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bitacora_calidad (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ingesta_id  INTEGER NOT NULL REFERENCES ingestas(id),
    severidad   TEXT    NOT NULL CHECK (severidad IN ('info', 'advertencia', 'error')),
    codigo      TEXT    NOT NULL,
    mensaje     TEXT    NOT NULL,
    fila        INTEGER,
    columna     TEXT,
    creado_en   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS series (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ingesta_id  INTEGER NOT NULL REFERENCES ingestas(id),
    sku         TEXT    NOT NULL,
    familia     TEXT,
    perfil_demanda TEXT,
    n_obs       INTEGER NOT NULL,
    UNIQUE (ingesta_id, sku)
);

CREATE TABLE IF NOT EXISTS ejecuciones (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ingesta_id      INTEGER NOT NULL REFERENCES ingestas(id),
    configuracion_id INTEGER NOT NULL REFERENCES configuraciones(id),
    seed            INTEGER NOT NULL,
    estado          TEXT    NOT NULL DEFAULT 'pendiente'
                        CHECK (estado IN ('pendiente', 'ejecutando', 'completada',
                                          'completada_con_fallos', 'detenida')),
    iniciada_en     TEXT,
    finalizada_en   TEXT,
    creado_en       TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tareas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ejecucion_id    INTEGER NOT NULL REFERENCES ejecuciones(id),
    sku             TEXT,
    modelo          TEXT    NOT NULL,
    corte           TEXT    NOT NULL,
    estado          TEXT    NOT NULL DEFAULT 'pendiente'
                        CHECK (estado IN ('pendiente', 'ejecutando', 'exitosa',
                                          'fallida', 'no_ejecutable')),
    seed            INTEGER NOT NULL,
    tiempo_pared_s  REAL,
    detalle_error   TEXT,
    iniciada_en     TEXT,
    finalizada_en   TEXT,
    UNIQUE (ejecucion_id, sku, modelo, corte)
);

CREATE TABLE IF NOT EXISTS pronosticos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tarea_id    INTEGER NOT NULL REFERENCES tareas(id),
    sku         TEXT    NOT NULL,
    modelo      TEXT    NOT NULL,
    fecha       TEXT    NOT NULL,
    valor       REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_pronosticos_sku_run
    ON pronosticos (sku, tarea_id);

CREATE TABLE IF NOT EXISTS metricas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tarea_id    INTEGER NOT NULL REFERENCES tareas(id),
    sku         TEXT    NOT NULL,
    modelo      TEXT    NOT NULL,
    metrica     TEXT    NOT NULL,
    valor       REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_metricas_sku_run
    ON metricas (sku, tarea_id);

CREATE TABLE IF NOT EXISTS resultados_comparativos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ejecucion_id    INTEGER NOT NULL REFERENCES ejecuciones(id),
    sku             TEXT    NOT NULL,
    modelo_campeon  TEXT    NOT NULL,
    metrica_seleccion TEXT  NOT NULL,
    valor_seleccion REAL    NOT NULL,
    UNIQUE (ejecucion_id, sku)
);

CREATE TABLE IF NOT EXISTS reportes_validacion (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ejecucion_id    INTEGER NOT NULL REFERENCES ejecuciones(id),
    sku             TEXT    NOT NULL,
    modelo_campeon  TEXT    NOT NULL,
    veredicto       TEXT    NOT NULL CHECK (veredicto IN ('mantiene', 'parcial', 'falla')),
    detalle         TEXT,
    creado_en       TEXT    NOT NULL DEFAULT (datetime('now'))
);
