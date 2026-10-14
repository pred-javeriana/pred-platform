-- Migration 0003: runs launched on the engine and their tasks (TASK-UI-1.0-A3-G3).
--
-- The worker creates an `ejecuciones` row per launch and one `tareas` row per engine unit
-- (SKU x model, decision D6). `ejecuciones` gains the engine's run id and folder, the reserve
-- cut, the process id and the attempt counter; a partial unique index keeps one row per engine
-- run so a resume updates the same rows instead of duplicating them. `tareas` already has the
-- columns the contract needs (`modelo` as familia:algoritmo, `corte` = t*, `detalle_error` as
-- the JSON of an ErrorInfo).

ALTER TABLE ejecuciones ADD COLUMN run_id TEXT;
ALTER TABLE ejecuciones ADD COLUMN ruta_corrida TEXT;
ALTER TABLE ejecuciones ADD COLUMN reserva_json TEXT;
ALTER TABLE ejecuciones ADD COLUMN error_json TEXT;
ALTER TABLE ejecuciones ADD COLUMN pid INTEGER;
ALTER TABLE ejecuciones ADD COLUMN intento INTEGER NOT NULL DEFAULT 0;

-- Seconds of the unit's L2 stage: `tiempo_pared_s` is L2 + L3 and is recomputed, not accumulated,
-- so applying the same progress event twice leaves the same value.
ALTER TABLE tareas ADD COLUMN tiempo_l2_s REAL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_ejecuciones_run_id
    ON ejecuciones (run_id) WHERE run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_ejecuciones_estado ON ejecuciones (estado);
CREATE INDEX IF NOT EXISTS ix_tareas_ejecucion_estado ON tareas (ejecucion_id, estado);
