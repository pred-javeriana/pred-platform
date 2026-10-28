-- Datos que M4 muestra y M3 publica con la corrida: corte t*, lanzamientos tardios,
-- demanda observada de la reserva y conteo de campeones por familia.
-- El motor es dueno del texto; pred-platform aplica este mismo texto como su
-- migracion 0010 (version 10 de PRAGMA user_version).

-- Corte t* con que L2 separo la reserva, leido de los metadatos de M1, nunca recalculado.
CREATE TABLE m3_cortes (
    run_id TEXT PRIMARY KEY REFERENCES m3_corridas (run_id),
    t_star TEXT NOT NULL
);

-- Productos que M1 excluyo por lanzarse despues del corte; nunca entran en m3_skus.
CREATE TABLE m3_lanzamientos_tardios (
    run_id TEXT NOT NULL REFERENCES m3_cortes (run_id),
    sku    TEXT NOT NULL,
    causa  TEXT NOT NULL CHECK (causa = 'lanzado despues del corte'),
    PRIMARY KEY (run_id, sku)
);

CREATE TRIGGER m3_lanzamiento_tardio_sin_sku
BEFORE INSERT ON m3_lanzamientos_tardios
WHEN EXISTS (SELECT 1 FROM m3_skus WHERE run_id = NEW.run_id AND sku = NEW.sku)
BEGIN
    SELECT RAISE(ABORT, 'un lanzamiento tardio no puede ser un SKU evaluado');
END;

CREATE TRIGGER m3_sku_sin_lanzamiento_tardio
BEFORE INSERT ON m3_skus
WHEN EXISTS (
    SELECT 1 FROM m3_lanzamientos_tardios WHERE run_id = NEW.run_id AND sku = NEW.sku
)
BEGIN
    SELECT RAISE(ABORT, 'un lanzamiento tardio no puede ser un SKU evaluado');
END;

-- Demanda observada de cada dia de la reserva (fecha > t*). valor NULL: sin observacion.
CREATE TABLE m3_observaciones (
    run_id TEXT NOT NULL,
    sku    TEXT NOT NULL,
    fecha  TEXT NOT NULL,
    valor  REAL,
    PRIMARY KEY (run_id, sku, fecha),
    FOREIGN KEY (run_id, sku) REFERENCES m3_skus (run_id, sku)
);

CREATE TRIGGER m3_observacion_en_reserva
BEFORE INSERT ON m3_observaciones
WHEN NOT EXISTS (
    SELECT 1 FROM m3_cortes WHERE run_id = NEW.run_id AND t_star < NEW.fecha
)
BEGIN
    SELECT RAISE(ABORT, 'una observacion necesita el corte de su corrida y una fecha de la reserva');
END;

-- SKU por familia campeona publicada, en los dos grupos de veredicto de M4. Las filas
-- con sku_class NULL son la cartera completa de la corrida.
CREATE VIEW m3_campeones AS
SELECT run_id,
       sku_class,
       familia_campeona AS familia,
       COUNT(*) AS n_skus,
       SUM(veredicto IN ('VALIDADO', 'EXPLORATORIO')) AS n_evaluados,
       SUM(veredicto NOT IN ('VALIDADO', 'EXPLORATORIO')) AS n_no_evaluados
FROM m3_veredictos
GROUP BY run_id, sku_class, familia_campeona
UNION ALL
SELECT run_id,
       NULL,
       familia_campeona,
       COUNT(*),
       SUM(veredicto IN ('VALIDADO', 'EXPLORATORIO')),
       SUM(veredicto NOT IN ('VALIDADO', 'EXPLORATORIO'))
FROM m3_veredictos
GROUP BY run_id, familia_campeona;
