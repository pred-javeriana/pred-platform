"""Cómo se muestra cada error del motor: causa, qué hacer, desglose y detalle para soporte.

La parte visible (título, acción, resumen y causas) nunca lleva el texto técnico del motor: ni
diccionarios ni representaciones de Python, ni rutas internas, ni el código. Ese texto queda
entero en el detalle para soporte. Los hechos salen de ``ErrorInfo.facts`` (contrato 1.3.0) o,
en corridas anteriores, del texto con el formato fijo que escribía el motor.
"""

from __future__ import annotations

import re

import pytest

from pred_platform.contract import models
from pred_platform.fallas import (
    TITULO_DESCONOCIDO,
    describir_falla,
    describir_motivo,
    soporte_ensayos,
)
from pred_platform.hechos_legados import hechos_del_texto, nombre_restriccion

# Las dos tareas de la captura del dueño (SKU 119187 y 1455482, Clásica / SARIMA), tal como
# las guardó una corrida anterior al contrato 1.3.0.
DETALLE_119187 = (
    "ningun trial de HPO completo para sku_id='119187' (28 fallidos, 6 podados; causas="
    "{'ajuste_fallido': 24, 'escalon': 6, 'configuracion_invalida': 4}; motivos="
    "{'ajuste_fallido': 'ventana=4 ajuste_fallido: AjusteModeloError: SARIMA(1, 0, 0)x(2, 1, 1, 7) "
    "con una raiz AR/MA de modulo 1.0001 (< 1.01): ajuste en el borde de la region estacionaria o "
    "invertible', 'escalon': 'escalon=4 valor=21.5723 umbral_top_1=18.8684', "
    "'configuracion_invalida': 'configuracion_invalida:pred_engine.optimizacion.optimizadores."
    "modelos_clasicos.classical_selection.construir_espacio.<locals>._orden_total_acotado'})"
)
DETALLE_1455482 = (
    "ningun trial de HPO completo para sku_id='1455482' (21 fallidos, 8 podados; causas="
    "{'ajuste_fallido': 17, 'configuracion_invalida': 4, 'poda_semantica': 8}; motivos="
    "{'ajuste_fallido': 'ventana=25 ajuste_fallido: AjusteModeloError: SARIMA(2, 0, 1)x(2, 0, 0, "
    "7) con una raiz AR/MA de modulo 1.0056 (< 1.01): ajuste en el borde de la region estacionaria "
    "o invertible', 'configuracion_invalida': 'configuracion_invalida:pred_engine.optimizacion."
    "optimizadores.modelos_clasicos.classical_selection.construir_espacio.<locals>."
    "_orden_total_acotado', 'poda_semantica': 'ventana=10 valor=101.017 "
    "poda_semantica:prediccion_nula'})"
)

# Cada código que el motor puede emitir (``pred_engine.comun.errores_codigo.codigos_conocidos``)
# y los de la plataforma, con un ``detail`` como el que escribe su origen.
DETALLES_POR_CODIGO: dict[str, str] = {
    "ingest.file_not_found": "[Errno 2] No such file or directory: '/data/raw/x.csv'",
    "ingest.not_csv": "se esperaba un archivo .csv: 'x.xlsx'",
    "ingest.header_rejected": "cabeceras sin alinear: {'sku': None}",
    "ingest.llm_provider": "LlmTimeoutError: el proveedor no respondio en 30 s",
    "ingest.schema_barrier": "fila 3: demand_qty='abc' no es numerico",
    "ingest.temporal_continuity": "sku_id='A' tiene huecos: [Timestamp('2024-01-03')]",
    "ingest.raw_write_blocked": "[Errno 13] Permission denied: '/data/raw'",
    "ingest.raw_conflict": "ya existe /data/raw/x.csv con otro contenido",
    "topology.math": "ADI no finito para sku_id='A'",
    "topology.routing": "clase desconocida 'otra'",
    "topology.contract": "faltan columnas {'sku_class'}",
    "handoff.contract": "columnas inesperadas ['x']",
    "handoff.preservation": "se perdieron 3 filas del panel",
    "handoff.precondition": "SKU sin demanda positiva: ['A']",
    "handoff.missing_metadata": "el parquet no trae pred.reserve",
    "provenance.origin_conflict": "origen declarado 'real' != registrado 'sintetico'",
    "selection.contract": "seleccion clasica imposible para sku_id='A': se requieren al menos "
    "40 observaciones, hay 12 (deficit=28)",
    "selection.no_viable_trial": DETALLE_119187,
    "selection.unregistered_family": "no hay estrategia registrada para 'otra'",
    "selection.unknown_class": "sku_class no soportada: 'otra'",
    "selection.router_config": "politica sin familias para {'smooth'}",
    "model.series_too_short": "se requieren al menos 60 observaciones, hay 45 (deficit=15)",
    "model.fit_failed": "SARIMA(1, 0, 0)x(2, 1, 1, 7) con una raiz AR/MA de modulo 1.0001 "
    "(< 1.01): ajuste en el borde de la region estacionaria o invertible",
    "model.degenerate_forecast": "pronostico degenerado para A/classical desde t*: "
    "prediccion_nula (regla #3); ",
    "model.foundation_unavailable": "el modelo fundacional requiere el extra 'foundation' "
    "(uv sync --frozen --extra dev --extra foundation)",
    "hpo.invalid_space": "p: bajo > alto",
    "hpo.study_failed": "el espacio de busqueda rechaza casi todas las configuraciones "
    "muestreadas (21 intentos invalidos); revise las restricciones de EspacioBusqueda; 21 "
    "fallidos, 0 podados; causas={'configuracion_invalida': 21}; motivos="
    "{'configuracion_invalida': 'configuracion_invalida:tests.limite_nivel'}",
    "run.incompatible_config": "seed; n_trials_objetivo",
    "run.failed_terminal": "la corrida 'r1' esta en estado fallida",
    "run.invalid_transition": "transicion invalida completada -> corriendo",
    "run.manifest_missing": "no existe /data/runs/r1/manifiesto.json",
    "run.manifest_corrupt": "Expecting value: line 1 column 1 (char 0)",
    "run.manifest_version": "version 3 no soportada",
    "evaluation.failed": "No complete finite walk-forward evidence for FIL-0002",
    "pipeline.stage_failed": "RuntimeError: HPO interrumpido para este SKU",
    "pipeline.stage_unavailable": "L4 no disponible en este motor",
    "synthetic.worm_overwrite": "ya existe synthetic_7.csv",
    "synthetic.conformance": "columna lead_time_days ausente",
    "synthetic.divergence_exhausted": "10 intentos sin divergencia aceptable",
    "synthetic.physical_constraint": "demanda negativa en 3 filas",
    "eligibility.no_admissible_history": "sin historia admisible: la serie comienza despues de t*",
    "eligibility.calendar_gaps": "calendario diario con huecos (daily grid requerido)",
    "eligibility.no_reserved_observations": "la serie termina antes de t*: no tiene observaciones "
    "reservadas",
    "eligibility.non_finite_demand": "demanda no finita en la historia admisible",
    "eligibility.insufficient_history": "historia admisible insuficiente: 60 dias, se requieren "
    "68 (min_train + horizonte + 3 pasos)",
    "eligibility.no_configured_family": "la politica '2.2.0-initial' no asigna ninguna familia "
    "configurada a la clase 'lumpy'",
    "eligibility.fewer_than_two_families": "menos de dos familias evaluables tras L2: evaluable "
    "classical; fallidas ['ml']",
    "platform.contract_invalid": "corrida.json inválido: Expecting ',' delimiter",
    "platform.schema_version_unsupported": "progreso de versión mayor 2 no soportada",
    "platform.artifact_missing": "No existe /data/runs/r1/corrida.json",
    "platform.not_found": "No existe la ejecución «9»",
    "platform.input_missing": "No existe /data/processed/abc.parquet.",
    "platform.engine_unavailable": "[Errno 2] No such file or directory: 'pred-engine'",
    "platform.database_busy": "database is locked",
    "platform.result_import_failed": "KeyError: 'summary'",
    "platform.run_stopped": "La ejecución se detuvo a petición del operador.",
}

# Lo que nunca debe verse fuera del detalle para soporte.
_TECNICO = re.compile(
    r"[{}\[\]]|<locals>|pred_engine|\w+=|\w+Error\b|Errno|'|"
    r"\b(?:fallidos|podados|causas|motivos)\b|_[a-z]+_|"
    r"\b(?:ingest|topology|handoff|selection|model|hpo|run|evaluation|pipeline|synthetic|"
    r"eligibility|platform|provenance)\.[a-z_]+"
)


def _visible(falla) -> str:
    partes = [falla.titulo, falla.accion or "", falla.ubicacion or "", falla.resumen or ""]
    for causa in falla.causas:
        partes += [causa.etiqueta, causa.ejemplo or ""]
    return "\n".join(partes)


@pytest.mark.parametrize("codigo", sorted(DETALLES_POR_CODIGO))
def test_cada_codigo_se_muestra_en_espanol_sin_texto_tecnico(codigo: str) -> None:
    detalle = DETALLES_POR_CODIGO[codigo]
    falla = describir_falla({"code": codigo, "stage": "L2", "detail": detalle})

    assert falla.titulo != TITULO_DESCONOCIDO
    assert falla.titulo.endswith(".") and falla.accion
    assert not _TECNICO.search(_visible(falla)), _visible(falla)
    # El detalle del motor queda entero, y copiable, para soporte; uno que repite el título no
    # dice nada nuevo y se omite.
    assert f"Código: {codigo}" in falla.soporte
    assert (detalle in falla.soporte) != (detalle == falla.titulo)


def _causas(falla) -> list[tuple[int, str, str | None]]:
    return [(c.cantidad, c.etiqueta, c.ejemplo) for c in falla.causas]


def test_la_falla_del_sku_119187_se_explica_por_causa_con_un_ejemplo() -> None:
    falla = describir_falla(
        {"code": "selection.no_viable_trial", "stage": "L2", "detail": DETALLE_119187}
    )

    assert falla.titulo == "Ninguna configuración terminó la búsqueda para este SKU."
    assert falla.accion == "Aumente los ensayos o revise la serie del SKU."
    assert falla.resumen == (
        "De 34 configuraciones probadas, 28 fallaron y 6 se descartaron antes de terminar."
    )
    assert _causas(falla) == [
        (
            24,
            "El modelo no pudo ajustarse",
            "Ejemplo: en la ventana 4, SARIMA(1, 0, 0)x(2, 1, 1, 7) dejó una raíz AR/MA con "
            "módulo 1,0001, por debajo del mínimo de 1,01 (ajuste al borde de la región estable).",
        ),
        (
            6,
            "Se descartó por rendir peor que las demás",
            "Ejemplo: tras 4 ventanas de validación, su error (21,57) superó el umbral del mejor "
            "ensayo (18,87).",
        ),
        (
            4,
            "La configuración no cumplía una regla de la búsqueda",
            "Ejemplo: la suma de los órdenes AR y MA superaba el máximo permitido.",
        ),
    ]


def test_la_falla_del_sku_1455482_nombra_la_poda_del_pronostico_nulo() -> None:
    falla = describir_falla(
        {"code": "selection.no_viable_trial", "stage": "L2", "detail": DETALLE_1455482}
    )

    assert falla.resumen == (
        "De 29 configuraciones probadas, 21 fallaron y 8 se descartaron antes de terminar."
    )
    assert [(cantidad, etiqueta) for cantidad, etiqueta, _ in _causas(falla)] == [
        (17, "El modelo no pudo ajustarse"),
        (8, "El pronóstico de prueba no era válido"),
        (4, "La configuración no cumplía una regla de la búsqueda"),
    ]
    assert falla.causas[1].ejemplo == (
        "Ejemplo: en la ventana 10, el pronóstico fue cero en todo el horizonte aunque la serie "
        "tenía demanda (error 101,02)."
    )


def test_los_hechos_del_motor_y_el_texto_anterior_se_muestran_igual() -> None:
    hechos = {
        "search": {
            "failed": 28,
            "pruned": 6,
            "causes": [
                {
                    "cause": "ajuste_fallido",
                    "count": 24,
                    "example": {
                        "window": 4,
                        "fit": {
                            "model": "SARIMA(1, 0, 0)x(2, 1, 1, 7)",
                            "reason": "raiz_en_borde",
                            "value": 1.0001,
                            "limit": 1.01,
                        },
                    },
                },
                {
                    "cause": "escalon",
                    "count": 6,
                    "example": {"rung": 4, "value": 21.5723, "threshold": 18.8684, "keep": 1},
                },
                {
                    "cause": "configuracion_invalida",
                    "count": 4,
                    "example": {"constraint": "orden_total_acotado"},
                },
            ],
        }
    }
    del_motor = describir_falla(
        {"code": "selection.no_viable_trial", "stage": "L2", "detail": "x", "facts": hechos}
    )
    del_texto = describir_falla(
        {"code": "selection.no_viable_trial", "stage": "L2", "detail": DETALLE_119187}
    )

    assert hechos_del_texto(DETALLE_119187) == hechos
    assert (del_motor.resumen, del_motor.causas) == (del_texto.resumen, del_texto.causas)


def test_los_hechos_del_motor_mandan_sobre_el_texto() -> None:
    falla = describir_falla(
        models.ErrorInfo(
            code="model.series_too_short",
            stage="L3",
            detail="se requieren al menos 60 observaciones, hay 45",
            facts=models.ErrorFacts(history=models.HistoryFacts(required=90, available=30)),
        )
    )

    assert falla.resumen == "La serie tiene 30 observaciones y se necesitan 90."


def test_unos_hechos_que_no_cumplen_el_contrato_ceden_al_texto() -> None:
    falla = describir_falla(
        {
            "code": "model.series_too_short",
            "stage": "L3",
            "detail": "se requieren al menos 60 observaciones, hay 45 (deficit=15)",
            "facts": {"history": {"required": "muchas"}},
        }
    )

    assert falla.resumen == "La serie tiene 45 observaciones y se necesitan 60."


@pytest.mark.parametrize(
    ("detalle", "resumen"),
    [
        (
            "SARIMA(1, 0, 0)x(2, 1, 1, 7) con una raiz AR/MA de modulo 1.0001 (< 1.01): ajuste en "
            "el borde de la region estacionaria o invertible",
            "SARIMA(1, 0, 0)x(2, 1, 1, 7) dejó una raíz AR/MA con módulo 1,0001, por debajo del "
            "mínimo de 1,01 (ajuste al borde de la región estable).",
        ),
        (
            "SARIMA(0, 1, 1)x(0, 0, 0, 7) no convergio: LinAlgError: Singular matrix",
            "El ajuste de SARIMA(0, 1, 1)x(0, 0, 0, 7) no convergió.",
        ),
        (
            "LightGBM no ajusto: LightGBMError: Check failed",
            "La biblioteca rechazó el ajuste de LightGBM.",
        ),
        ("Chronos-2 no pronostico: CUDA out of memory", "Chronos-2 no produjo el pronóstico."),
        (
            "forma de cuantiles (9, 3), se esperaba (9, 2)",
            "Chronos-2 no entregó los cuantiles esperados.",
        ),
        (
            "el modelo no emite el cuantil 0.5 (emite (0.1, 0.9))",
            "Chronos-2 no entregó el cuantil que se usa como pronóstico.",
        ),
    ],
)
def test_un_ajuste_fallido_dice_que_modelo_y_por_que(detalle: str, resumen: str) -> None:
    falla = describir_falla({"code": "model.fit_failed", "stage": "L3", "detail": detalle})

    assert falla.resumen == resumen


@pytest.mark.parametrize(
    ("codigo", "detalle", "resumen"),
    [
        (
            "model.series_too_short",
            "serie de longitud 10 insuficiente: con lags=7 se requieren al menos 15 observaciones",
            "La serie tiene 10 observaciones y se necesitan 15.",
        ),
        (
            "selection.contract",
            "seleccion clasica imposible para sku_id='A': se requieren al menos 40 "
            "observaciones, hay 1 (deficit=39)",
            "La serie tiene 1 observación y se necesitan 40.",
        ),
        (
            "eligibility.insufficient_history",
            "historia admisible insuficiente: 60 dias, se requieren 68 (min_train + horizonte + "
            "3 pasos)",
            "La serie tiene 60 observaciones y se necesitan 68.",
        ),
    ],
)
def test_una_historia_corta_dice_cuanto_hay_y_cuanto_falta(
    codigo: str, detalle: str, resumen: str
) -> None:
    assert describir_falla({"code": codigo, "detail": detalle}).resumen == resumen


def test_una_busqueda_detenida_por_sus_reglas_lo_dice() -> None:
    falla = describir_falla(
        {"code": "hpo.study_failed", "detail": DETALLES_POR_CODIGO["hpo.study_failed"]}
    )

    assert falla.resumen == (
        "De 21 configuraciones probadas, 21 fallaron. Las reglas de la búsqueda rechazaron 21 "
        "configuraciones seguidas, así que la búsqueda se detuvo."
    )
    [causa] = falla.causas
    assert (causa.cantidad, causa.etiqueta) == (
        21,
        "La configuración no cumplía una regla de la búsqueda",
    )
    # Una restricción sin texto propio se nombra de forma genérica.
    assert causa.ejemplo == "Ejemplo: no cumplía una regla de la búsqueda."


def test_un_codigo_desconocido_tiene_titulo_generico_y_su_codigo_en_soporte() -> None:
    falla = describir_falla({"code": "model.algo_nuevo", "stage": "L2", "detail": "x=1"})

    assert falla.titulo == TITULO_DESCONOCIDO
    assert falla.accion
    assert "model.algo_nuevo" not in _visible(falla)
    assert "Código: model.algo_nuevo" in falla.soporte


def test_un_error_sin_hechos_no_inventa_desglose() -> None:
    falla = describir_falla({"code": "evaluation.failed", "stage": "L3", "detail": "sin datos"})

    assert falla.resumen is None and falla.causas == ()
    assert not falla.tiene_desglose


def test_un_resumen_antiguo_sin_causas_solo_da_los_totales() -> None:
    falla = describir_falla(
        {
            "code": "selection.no_viable_trial",
            "detail": "ningun trial de HPO completo para sku_id='FIL-0001' (1 fallidos, 2 podados)",
        }
    )

    assert falla.resumen == (
        "De 3 configuraciones probadas, 1 falló y 2 se descartaron antes de terminar."
    )
    assert falla.causas == ()


@pytest.mark.parametrize(
    ("etiqueta", "nombre"),
    [
        (
            "pred_engine.optimizacion.optimizadores.modelos_clasicos.classical_selection."
            "construir_espacio.<locals>._orden_total_acotado",
            "orden_total_acotado",
        ),
        ("_CostoAcotado(maximo=100000)", "costo_acotado"),
        ("lambda:24f248195e851562", None),
        ("anon:24f248195e851562", None),
    ],
)
def test_el_nombre_de_una_restriccion_es_el_que_da_el_motor(
    etiqueta: str, nombre: str | None
) -> None:
    assert nombre_restriccion(etiqueta) == nombre


@pytest.mark.parametrize(
    ("motivo", "frase"),
    [
        (
            "ventana=4 ajuste_fallido: AjusteModeloError: SARIMA(1, 0, 0)x(2, 1, 1, 7) con una "
            "raiz AR/MA de modulo 1.0001 (< 1.01): ajuste en el borde de la region estacionaria o "
            "invertible",
            "El modelo no pudo ajustarse: en la ventana 4, SARIMA(1, 0, 0)x(2, 1, 1, 7) dejó una "
            "raíz AR/MA con módulo 1,0001, por debajo del mínimo de 1,01 (ajuste al borde de la "
            "región estable).",
        ),
        (
            "ventana=2 ajuste_fallido: RuntimeError: no converge",
            "El modelo no pudo ajustarse en la ventana 2.",
        ),
        (
            "escalon=4 valor=21.5723 umbral_top_1=18.8684",
            "Se descartó por rendir peor que las demás: tras 4 ventanas de validación, su error "
            "(21,57) superó el umbral del mejor ensayo (18,87).",
        ),
        (
            "ventana=3 valor=9.5 asha",
            "Se descartó por rendir peor que las demás: en la ventana 3, su error (9,5) quedó "
            "entre los peores.",
        ),
        (
            "ventana=10 valor=101.017 poda_semantica:prediccion_nula",
            "El pronóstico de prueba no era válido: en la ventana 10, el pronóstico fue cero en "
            "todo el horizonte aunque la serie tenía demanda (error 101,02).",
        ),
        (
            "configuracion_invalida:pred_engine.optimizacion.optimizadores.modelos_clasicos."
            "classical_selection.construir_espacio.<locals>._orden_total_acotado",
            "La configuración no cumplía una regla de la búsqueda: la suma de los órdenes AR y MA "
            "superaba el máximo permitido.",
        ),
        (
            "configuracion_invalida:lambda:24f248195e851562",
            "La configuración no cumplía una regla de la búsqueda.",
        ),
        (
            "ninguna_ventana_convergio",
            "Ninguna ventana de validación dio un error medible.",
        ),
        ("otro motivo {'x': 1}", "Otra causa del motor."),
    ],
)
def test_el_motivo_de_un_ensayo_se_lee_como_una_frase(motivo: str, frase: str) -> None:
    assert describir_motivo(motivo) == frase
    assert not _TECNICO.search(frase)


def test_un_ensayo_sin_motivo_no_tiene_frase_ni_soporte() -> None:
    ensayo = models.TrialRow(id="t-0", configuracion={}, estado="completado", n_ventanas=4)

    assert describir_motivo(None) is None
    assert soporte_ensayos([ensayo]) is None
