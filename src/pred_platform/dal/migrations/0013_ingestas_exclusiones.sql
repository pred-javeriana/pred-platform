-- Migration 0013: exclusiones de productos en ingesta (contrato 1.4.0).
--
-- Los productos excluidos antes de L2 (p. ej. lanzados despues de la fecha de corte)
-- y su conteo se persisten en `ingestas` para el informe de diagnostico.
-- Filas anteriores mantienen NULL en ambas columnas.

ALTER TABLE ingestas ADD COLUMN excluded_count INTEGER;
ALTER TABLE ingestas ADD COLUMN exclusiones_json TEXT;
