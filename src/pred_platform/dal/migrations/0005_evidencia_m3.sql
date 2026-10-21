-- Evidencia de M3: corridas, unidades, pronosticos, metricas, seleccion y veredictos
-- (ADR-03-009 y ADR-03-010 de pred-engine).
--
-- Fuente unica: pred-engine/src/pred_engine/forecasting/persistencia_evidencia/esquema_m3.sql.
-- pred-platform aplica este mismo texto como su migracion 0002 (ADR-05-002): un cambio aqui
-- exige una migracion nueva alla. Son tablas nuevas porque las de 0001 (reportes_validacion,
-- metricas, pronosticos) usan otro vocabulario de veredictos y no tienen claves naturales.

CREATE TABLE m3_corridas (
    run_id           TEXT    PRIMARY KEY,
    ingesta_sha256   TEXT    NOT NULL REFERENCES ingestas (sha256),
    run_id_m2        TEXT,
    identidad        TEXT    NOT NULL,
    datos_sinteticos INTEGER NOT NULL CHECK (datos_sinteticos IN (0, 1)),
    estado           TEXT    NOT NULL CHECK (estado IN ('EN_CURSO', 'EVALUADA', 'SELECCIONADA',
                                                        'PUBLICADA', 'RECHAZADA')),
    causa_rechazo    TEXT,
    creado_en        TEXT    NOT NULL DEFAULT (datetime('now')),
    CHECK ((estado = 'RECHAZADA') = (causa_rechazo IS NOT NULL))
);

-- SKUs del manifiesto validado (candidatos y fallos de 3.2). Toda la evidencia por SKU los
-- referencia y cada uno necesita veredicto para publicar (ADR-03-010).
CREATE TABLE m3_skus (
    run_id TEXT NOT NULL REFERENCES m3_corridas (run_id),
    sku    TEXT NOT NULL,
    PRIMARY KEY (run_id, sku)
);

-- Candidatos que 3.2 no pudo validar (ADR-03-005): nunca un descarte mudo.
CREATE TABLE m3_fallos (
    run_id       TEXT NOT NULL,
    candidato_id TEXT NOT NULL,
    sku          TEXT NOT NULL,
    modelo       TEXT,
    campo        TEXT NOT NULL,
    codigo_error TEXT NOT NULL,
    mensaje      TEXT NOT NULL,
    PRIMARY KEY (run_id, candidato_id),
    FOREIGN KEY (run_id, sku) REFERENCES m3_skus (run_id, sku)
);

-- Unidad durable: SKU x candidato x ventana (origen), incluida la linea base. La fila es el
-- marcador de unidad confirmada y se escribe en la misma transaccion que sus pronosticos.
CREATE TABLE m3_unidades (
    run_id       TEXT NOT NULL,
    sku          TEXT NOT NULL,
    candidato_id TEXT NOT NULL,
    origen       TEXT NOT NULL,
    familia      TEXT NOT NULL,
    modelo       TEXT NOT NULL,
    PRIMARY KEY (run_id, sku, candidato_id, origen),
    FOREIGN KEY (run_id, sku) REFERENCES m3_skus (run_id, sku)
);

-- valor NULL: el modelo no dio un pronostico finito para ese paso.
CREATE TABLE m3_pronosticos (
    run_id       TEXT    NOT NULL,
    sku          TEXT    NOT NULL,
    candidato_id TEXT    NOT NULL,
    origen       TEXT    NOT NULL,
    h            INTEGER NOT NULL CHECK (h >= 1),
    fecha        TEXT    NOT NULL,
    valor        REAL,
    PRIMARY KEY (run_id, sku, candidato_id, origen, h),
    FOREIGN KEY (run_id, sku, candidato_id, origen)
        REFERENCES m3_unidades (run_id, sku, candidato_id, origen)
);

-- ventana: el origen de la ventana o 'agregada'. valor NULL: no calculable, con su causa.
CREATE TABLE m3_metricas (
    run_id       TEXT NOT NULL,
    sku          TEXT NOT NULL,
    candidato_id TEXT NOT NULL,
    ventana      TEXT NOT NULL,
    metrica      TEXT NOT NULL,
    valor        REAL,
    causa        TEXT,
    PRIMARY KEY (run_id, sku, candidato_id, ventana, metrica),
    FOREIGN KEY (run_id, sku) REFERENCES m3_skus (run_id, sku)
);

-- Seleccion por categoria (ADR-03-007) y su resumen de veredictos. Los campos JSON
-- guardan mapas: familia -> mediana de r, sku -> causa, familia -> causa de exclusion
-- (no_entregada o no_elegible, ADR-03-003), veredicto -> conteo.
CREATE TABLE m3_categorias (
    run_id           TEXT    NOT NULL REFERENCES m3_corridas (run_id),
    sku_class        TEXT    NOT NULL,
    familia_campeona TEXT    NOT NULL,
    motivo           TEXT    NOT NULL,
    adverso          INTEGER NOT NULL CHECK (adverso IN (0, 1)),
    medianas_r       TEXT    NOT NULL,
    skus_comparables TEXT    NOT NULL,
    excluidos        TEXT    NOT NULL,
    familias_excluidas TEXT  NOT NULL,
    conteos          TEXT    NOT NULL,
    porcentajes      TEXT    NOT NULL,
    mediana_r        REAL,
    n_adversos       INTEGER NOT NULL,
    PRIMARY KEY (run_id, sku_class)
);

-- Veredicto por SKU (ADR-03-008). Las metricas del campeon y de la linea base estan en
-- m3_metricas (ventana 'agregada'); modelos_evaluados es JSON descriptivo con r por modelo.
-- dm_*: prueba HLN-DM del campeon contra Seasonal Naive, p-valor bilateral y ajuste BH entre
-- los SKUs de la corrida con nivel dm_alfa; sin prueba, dm_causa dice por que.
CREATE TABLE m3_veredictos (
    run_id                     TEXT    NOT NULL,
    sku                        TEXT    NOT NULL,
    sku_class                  TEXT    NOT NULL,
    veredicto                  TEXT    NOT NULL CHECK (veredicto IN (
                                   'FALLO_TECNICO', 'NO_EVALUABLE', 'EVIDENCIA_INSUFICIENTE',
                                   'VALIDADO', 'EXPLORATORIO')),
    familia_campeona           TEXT    NOT NULL,
    candidato_campeon          TEXT,
    n_ventanas                 INTEGER NOT NULL,
    cobertura                  REAL    NOT NULL,
    razon_sn                   REAL,
    pierde_frente_a_linea_base INTEGER CHECK (pierde_frente_a_linea_base IN (0, 1)),
    comparacion_incompleta     INTEGER NOT NULL CHECK (comparacion_incompleta IN (0, 1)),
    iqr_diferencia_mae         REAL,
    dm_n_ventanas              INTEGER NOT NULL,
    dm_horizonte               INTEGER NOT NULL,
    dm_estadistico             REAL,
    dm_p_valor                 REAL,
    dm_p_ajustado              REAL,
    dm_alfa                    REAL,
    dm_significativa           INTEGER CHECK (dm_significativa IN (0, 1)),
    dm_causa                   TEXT,
    justificacion              TEXT    NOT NULL,
    modelos_evaluados          TEXT    NOT NULL,
    PRIMARY KEY (run_id, sku),
    FOREIGN KEY (run_id, sku) REFERENCES m3_skus (run_id, sku),
    FOREIGN KEY (run_id, sku_class) REFERENCES m3_categorias (run_id, sku_class),
    CHECK ((dm_causa IS NULL) = (dm_p_valor IS NOT NULL))
);

-- Lo que consume M4 (ADR-03-010): solo veredictos de corridas publicadas, con su prueba
-- Diebold-Mariano. El resto de tablas se filtra por los run_id que aparecen aqui.
CREATE VIEW evidencia_publicada AS
SELECT v.*, c.datos_sinteticos, c.identidad
FROM m3_veredictos AS v
JOIN m3_corridas AS c ON c.run_id = v.run_id
WHERE c.estado = 'PUBLICADA';
