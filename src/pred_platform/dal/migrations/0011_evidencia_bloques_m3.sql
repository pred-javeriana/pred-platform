-- Evidencia del bloque de prueba (ADR-023): significancia por categoria, confirmacion del
-- campeon frente a cada competidora, seleccion de robustez previa a t*, intervalos de r
-- y productos que M2 excluyo de la competencia.
-- El motor es dueno del texto; pred-platform aplica este mismo texto como su
-- migracion 0011 (version 11 de PRAGMA user_version).
--
-- La familia campeona de m3_categorias se elige en el bloque de seleccion de la reserva.
-- m3_veredictos, m3_cartera y las filas 'agregada' de m3_metricas leen el bloque de
-- prueba; dm_p_ajustado es BH entre los SKU de la categoria. Las filas por ventana de
-- m3_metricas cubren toda la reserva. Los limites de los bloques estan en
-- m3_protocolos.protocolo, clave 'bloques'.

-- HLN-DM del campeon contra Seasonal Naive resumida por categoria: SKU con p-valor,
-- significativos por BH a nivel dm_alfa segun el signo del estadistico, los mismos conteos
-- con Benjamini-Yekutieli y el nivel FCR de los intervalos de los SKU significativos.
CREATE TABLE m3_significancia (
    run_id       TEXT    NOT NULL,
    sku_class    TEXT    NOT NULL,
    n_probados   INTEGER NOT NULL CHECK (n_probados >= 0),
    n_mejores    INTEGER NOT NULL CHECK (n_mejores >= 0),
    n_peores     INTEGER NOT NULL CHECK (n_peores >= 0),
    n_mejores_by INTEGER NOT NULL CHECK (n_mejores_by >= 0),
    n_peores_by  INTEGER NOT NULL CHECK (n_peores_by >= 0),
    nivel_fcr    REAL    CHECK (nivel_fcr > 0 AND nivel_fcr < 1),
    PRIMARY KEY (run_id, sku_class),
    FOREIGN KEY (run_id, sku_class) REFERENCES m3_categorias (run_id, sku_class),
    CHECK (n_mejores + n_peores <= n_probados),
    CHECK ((nivel_fcr IS NULL) = (n_mejores + n_peores = 0))
);

-- Confirmacion del campeon congelado. estado: confirmado, no_confirmado, sin_comparables
-- (pocos SKU para alguna competidora) o no_aplica (el campeon es la linea base o los
-- datos no son reales declarados). p_iut es el mayor p de las competidoras y p_holm su
-- ajuste Holm entre categorias. efecto_*: mediana de r del campeon frente a Seasonal
-- Naive con su intervalo bootstrap; efecto_causa 'perdida_cero:<campos>' nombra los
-- valores que las perdidas cero dejan sin definir. contendiente: la familia que la
-- misma regla elige sin la fundacional; su fila de m3_comparaciones es Chronos-2
-- frente a ella.
CREATE TABLE m3_confirmaciones (
    run_id             TEXT    NOT NULL,
    sku_class          TEXT    NOT NULL,
    familia_campeona   TEXT    NOT NULL,
    estado             TEXT    NOT NULL CHECK (estado IN (
                           'confirmado', 'no_confirmado', 'sin_comparables', 'no_aplica')),
    causa              TEXT,
    p_iut              REAL,
    p_holm             REAL,
    alfa               REAL    NOT NULL,
    efecto_n_skus      INTEGER CHECK (efecto_n_skus >= 0),
    efecto_mediana_r   REAL,
    efecto_ic_inferior REAL,
    efecto_ic_superior REAL,
    efecto_causa       TEXT,
    contendiente       TEXT,
    PRIMARY KEY (run_id, sku_class),
    FOREIGN KEY (run_id, sku_class) REFERENCES m3_categorias (run_id, sku_class),
    CHECK ((estado = 'confirmado') = (causa IS NULL)),
    CHECK ((p_holm IS NULL) = (estado IN ('sin_comparables', 'no_aplica')))
);

-- Campeon frente a una competidora en los SKU con ambas instancias y al menos n_min_dm
-- ventanas comunes de prueba (politica del protocolo). La razon de un SKU es
-- RMSE_campeon / RMSE_competidora; < 1 favorece al campeon. Vale 0 si solo el campeon
-- no tiene error, infinito (REAL Inf) si solo la competidora no lo tiene y 1 si ninguno
-- lo tiene. ic_mediana es el intervalo bootstrap por bloques de dias de la mediana de
-- razones; p_iut es p_wilcoxon si esa mediana es < 1 y 1 si no. n_mejores y n_peores
-- cuentan los SKU con HLN-DM significativa por BH dentro de la comparacion. Con menos SKU
-- que el minimo, causa lo dice y solo quedan n_skus, la mediana y las victorias. Una
-- causa 'perdida_cero:<campos>' nombra los valores que las perdidas cero dejan sin
-- definir, como Hodges-Lehmann con una razon 0 y una infinita.
CREATE TABLE m3_comparaciones (
    run_id                     TEXT    NOT NULL,
    sku_class                  TEXT    NOT NULL,
    competidora                TEXT    NOT NULL,
    n_skus                     INTEGER NOT NULL CHECK (n_skus >= 0),
    mediana_razon              REAL,
    ic_mediana_inferior        REAL,
    ic_mediana_superior        REAL,
    hodges_lehmann             REAL,
    ic_hodges_lehmann_inferior REAL,
    ic_hodges_lehmann_superior REAL,
    n_victorias                INTEGER NOT NULL CHECK (n_victorias BETWEEN 0 AND n_skus),
    ic_victorias_inferior      REAL,
    ic_victorias_superior      REAL,
    estadistico_wilcoxon       REAL,
    p_wilcoxon                 REAL,
    p_iut                      REAL,
    n_mejores                  INTEGER,
    n_peores                   INTEGER,
    causa                      TEXT,
    PRIMARY KEY (run_id, sku_class, competidora),
    FOREIGN KEY (run_id, sku_class) REFERENCES m3_confirmaciones (run_id, sku_class),
    CHECK (p_wilcoxon IS NOT NULL OR causa IS NOT NULL)
);

-- Seleccion de robustez: la regla de m3_categorias sobre los pliegues L3, que solo usan
-- historia hasta t*. Se informa y nunca elige al campeon: las familias optimizadas se
-- puntuan en los pliegues que su HPO uso. Los campos JSON son los de m3_categorias.
CREATE TABLE m3_seleccion_previa (
    run_id             TEXT    NOT NULL,
    sku_class          TEXT    NOT NULL,
    familia_campeona   TEXT    NOT NULL,
    motivo             TEXT    NOT NULL,
    adverso            INTEGER NOT NULL CHECK (adverso IN (0, 1)),
    medianas_r         TEXT    NOT NULL,
    skus_comparables   TEXT    NOT NULL,
    excluidos          TEXT    NOT NULL,
    familias_excluidas TEXT    NOT NULL,
    PRIMARY KEY (run_id, sku_class),
    FOREIGN KEY (run_id, sku_class) REFERENCES m3_categorias (run_id, sku_class)
);

-- Valores de r del campeon que HLN-DM contra Seasonal Naive no rechaza (Fieller): contiene
-- r y excluye 1 justo cuando la prueba rechaza a nivel 1 - nivel. El conjunto es
-- [inferior, superior]; superior NULL: sin cota superior. Con inferior_cola tiene dos
-- tramos, [inferior, superior] y [inferior_cola, infinito), y la prueba rechaza los
-- valores entre ambos. *_fcr: el mismo conjunto al nivel FCR de su categoria, solo para
-- los SKU con prueba significativa por BH.
CREATE TABLE m3_intervalos_r (
    run_id            TEXT NOT NULL,
    sku               TEXT NOT NULL,
    nivel             REAL NOT NULL CHECK (nivel > 0 AND nivel < 1),
    inferior          REAL NOT NULL CHECK (inferior >= 0),
    superior          REAL CHECK (superior >= inferior),
    inferior_cola     REAL CHECK (inferior_cola > superior),
    nivel_fcr         REAL CHECK (nivel_fcr > 0 AND nivel_fcr < 1),
    inferior_fcr      REAL CHECK (inferior_fcr >= 0),
    superior_fcr      REAL CHECK (superior_fcr >= inferior_fcr),
    inferior_cola_fcr REAL CHECK (inferior_cola_fcr > superior_fcr),
    PRIMARY KEY (run_id, sku),
    FOREIGN KEY (run_id, sku) REFERENCES m3_veredictos (run_id, sku),
    CHECK (inferior_cola IS NULL OR superior IS NOT NULL),
    CHECK (inferior_cola_fcr IS NULL OR superior_fcr IS NOT NULL),
    CHECK ((nivel_fcr IS NULL) = (inferior_fcr IS NULL)),
    CHECK (nivel_fcr IS NOT NULL OR superior_fcr IS NULL)
);

-- Productos que M2 excluyo antes de L2 o al cerrar L2 (regla de dos familias), con su
-- codigo eligibility.* y su causa. Nunca entran en m3_skus ni reciben veredicto. Los
-- lanzamientos tardios de M1 estan en m3_lanzamientos_tardios.
CREATE TABLE m3_exclusiones (
    run_id    TEXT NOT NULL REFERENCES m3_cortes (run_id),
    sku       TEXT NOT NULL,
    sku_class TEXT NOT NULL,
    codigo    TEXT NOT NULL CHECK (codigo LIKE 'eligibility.%'),
    causa     TEXT NOT NULL,
    PRIMARY KEY (run_id, sku)
);

CREATE TRIGGER m3_exclusion_sin_sku
BEFORE INSERT ON m3_exclusiones
WHEN EXISTS (SELECT 1 FROM m3_skus WHERE run_id = NEW.run_id AND sku = NEW.sku)
BEGIN
    SELECT RAISE(ABORT, 'un producto excluido no puede ser un SKU evaluado');
END;

CREATE TRIGGER m3_sku_sin_exclusion
BEFORE INSERT ON m3_skus
WHEN EXISTS (SELECT 1 FROM m3_exclusiones WHERE run_id = NEW.run_id AND sku = NEW.sku)
BEGIN
    SELECT RAISE(ABORT, 'un producto excluido no puede ser un SKU evaluado');
END;
