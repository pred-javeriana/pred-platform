-- Procedencia M1 declarada, compartida por la ingesta y la evaluacion.
CREATE TABLE m3_ingestas (
    ingesta_sha256 TEXT NOT NULL REFERENCES ingestas (sha256),
    ingesta_ref_m1 TEXT NOT NULL,
    datos_sinteticos INTEGER NOT NULL CHECK (datos_sinteticos IN (0, 1)),
    PRIMARY KEY (ingesta_sha256, ingesta_ref_m1)
);
