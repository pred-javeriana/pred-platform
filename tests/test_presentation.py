"""El mapa de presentación: una etiqueta en español y una explicación por código, sin mezclar
vocabularios ni tratar la poda como fallo."""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from pred_platform.app.presentation import (
    FAMILY_ORDER,
    VERDICT_ORDER,
    UnknownStatus,
    abbreviate_id,
    describe_run,
    describe_status,
    exact_when,
    family_label,
    family_options,
    family_term,
    format_count,
    format_date,
    format_decimal,
    format_duration,
    format_when,
    glossary_hint,
    metric_label,
    model_label,
    model_term,
    sku_class_label,
    sku_class_options,
    sku_class_term,
    stage_label,
    status_options,
    verdict_label,
    verdict_options,
)


def test_task_states_are_five_and_distinguishable_without_sharing_icons() -> None:
    codes = ("pendiente", "ejecutando", "exitosa", "fallida", "no_ejecutable")
    shown = [describe_status("task", code) for code in codes]
    assert len({item.icon for item in shown}) == 5
    assert len({item.label for item in shown}) == 5
    assert [item.label for item in shown] == [
        "Pendiente",
        "Ejecutando",
        "Exitosa",
        "Fallida",
        "No ejecutable",
    ]
    assert describe_status("run", "ejecutando").label == "Ejecutando"
    # Tasks that finished fitting and wait for the final evaluation: a short label, not a sentence.
    waiting = describe_status("task", "finalizo_m2")
    assert waiting.label == "Por evaluar" and "evaluación final" in (waiting.hint or "")


def test_a_run_with_failed_tasks_reads_as_completed_not_as_a_failure() -> None:
    with_failures = describe_status("run", "completada_con_fallos")
    completed = describe_status("run", "completada")
    assert with_failures.label == "Completada"
    assert (with_failures.icon, with_failures.modifier) == (completed.icon, completed.modifier)
    assert with_failures.hint and "fallaron" in with_failures.hint
    # The stored code stays available for traceability.
    assert with_failures.code == "completada_con_fallos"


def test_a_stopped_run_is_a_warning_while_it_can_resume_and_an_error_when_it_cannot() -> None:
    resumable = describe_run("detenida", resumable=True)
    final = describe_run("detenida", resumable=False)
    assert (resumable.label, resumable.modifier) == ("Detenida", "warn")
    assert (final.label, final.modifier) == ("Detenida", "err")
    assert final.hint and "ejecución nueva" in final.hint and final.code == "detenida"
    # Only a stop depends on whether it can resume.
    assert describe_run("completada", resumable=False) == describe_status("run", "completada")


def test_pruned_trial_is_not_rendered_as_failure() -> None:
    pruned = describe_status("trial", "podado")
    failed = describe_status("trial", "fallido")
    assert pruned.label == "Podada"
    assert pruned.modifier == "pruned"
    assert pruned.icon != failed.icon
    assert pruned.modifier != failed.modifier
    assert failed.label == "Fallida"
    assert failed.modifier == "err"


def test_trial_contract_codes_map_to_interface_labels() -> None:
    assert describe_status("trial", "corriendo").label == "Ejecutando"
    assert describe_status("trial", "completado").label == "Completada"


def test_each_verdict_has_a_spanish_label_and_a_one_line_explanation() -> None:
    # The five M3 verdicts keep their own vocabulary, icon and color, in plain Spanish.
    verdicts = (
        "VALIDADO",
        "EXPLORATORIO",
        "EVIDENCIA_INSUFICIENTE",
        "NO_EVALUABLE",
        "FALLO_TECNICO",
    )
    assert set(verdicts) == set(VERDICT_ORDER)
    shown = [describe_status("verdict", code) for code in verdicts]
    assert [status.label for status in shown] == [
        "Validado",
        "Exploratorio",
        "Evidencia insuficiente",
        "No evaluable",
        "Error técnico",
    ]
    assert [verdict_label(code) for code in verdicts] == [status.label for status in shown]
    for status in shown:
        assert status.hint and status.hint.endswith(".") and "\n" not in status.hint
        assert "_" not in status.hint and len(status.hint) <= 90
    assert [status.code for status in shown] == list(verdicts)
    assert len({(status.icon, status.modifier) for status in shown}) == len(verdicts)
    # Filters send the contract code and show the label.
    assert verdict_options() == [("", "Todos")] + [(c, verdict_label(c)) for c in VERDICT_ORDER]
    for legacy in ("mantiene", "parcial", "falla"):
        with pytest.raises(UnknownStatus, match="verdict"):
            describe_status("verdict", legacy)
    with pytest.raises(UnknownStatus, match="task"):
        describe_status("task", "podado")
    with pytest.raises(UnknownStatus, match="trial"):
        describe_status("trial", "exitosa")


def test_format_count_uses_spanish_thousands() -> None:
    assert format_count(1284) == "1.284"


def test_ingest_statuses_use_a1_phrases_and_distinct_modifiers() -> None:
    accepted = describe_status("ingest", "accepted")
    rejected = describe_status("ingest", "rejected")
    failed = describe_status("ingest", "failed")
    pending = describe_status("ingest", "needs_confirmation")
    assert accepted.label == "Datos válidos"
    assert rejected.label == "El archivo necesita correcciones"
    assert failed.label == "No fue posible procesar el archivo"
    assert pending.label == "Se requiere confirmación"
    assert accepted.modifier == "ok"
    assert rejected.modifier == "warn"
    assert failed.modifier == "err"
    assert pending.modifier == "info"
    with pytest.raises(UnknownStatus, match="ingest"):
        describe_status("ingest", "exitosa")


def test_severity_is_not_color_only() -> None:
    error = describe_status("severity", "error")
    warn = describe_status("severity", "advertencia")
    assert error.icon != warn.icon
    assert error.label == "Error"
    assert warn.label == "Advertencia"


def test_sku_class_labels_and_decimal_helpers() -> None:
    assert sku_class_label("smooth") == "Suave"
    assert sku_class_label("erratic") == "Errática"
    assert sku_class_label("intermittent") == "Intermitente"
    assert sku_class_label("lumpy") == "Grumosa"
    assert sku_class_term("lumpy").hint == "Muchos días sin venta y cantidades muy variables."
    assert sku_class_options() == [
        ("", "Todas"),
        ("smooth", "Suave"),
        ("erratic", "Errática"),
        ("intermittent", "Intermitente"),
        ("lumpy", "Grumosa"),
    ]
    assert format_decimal(1.32) == "1,32"
    assert format_decimal(0.00827, 3) == "0,008"
    assert abbreviate_id("b1831d4585b72d7c") == "b1831d4585b7"
    assert abbreviate_id("short") == "short"
    assert format_when(None) == "—"


# ------------------------------------------------------------ la selección por SKU (C2)
def test_a_family_can_be_excluded_without_being_a_failure() -> None:
    excluded = describe_status("family", "excluida")
    failed = describe_status("family", "fallida")
    assert excluded.label == "Excluida" and excluded.modifier == "neutral"
    assert excluded.icon != failed.icon and excluded.modifier != failed.modifier
    for code in ("pendiente", "ejecutando", "exitosa", "fallida", "no_ejecutable"):
        assert describe_status("family", code) == describe_status("task", code)
    with pytest.raises(UnknownStatus, match="family"):
        describe_status("family", "podado")


# ---------------------------------------------------------- familias, modelos y etapas
def test_families_and_models_read_in_spanish_whatever_the_engine_spelling() -> None:
    assert [family_label(code) for code in FAMILY_ORDER] == [
        "Clásica",
        "Aprendizaje automático",
        "Aprendizaje profundo",
        "Fundacional",
    ]
    assert family_label("seasonal_naive") == "Línea base estacional"
    assert all(family_term(code).hint for code in (*FAMILY_ORDER, "seasonal_naive"))
    assert family_options() == [("", "Todas")] + [(c, family_label(c)) for c in FAMILY_ORDER]
    assert family_options(baseline=True)[-1] == ("seasonal_naive", "Línea base estacional")
    # A task model, a bare model and a candidate id all read as the model's name.
    for code in ("foundation:chronos2", "chronos2", "1000866/foundation/chronos2"):
        assert model_label(code) == "Chronos-2"
    assert model_label("classical:sarima") == "SARIMA"
    assert model_label("seasonal_naive") == "Ingenuo estacional"
    assert model_term("ml:lightgbm").hint


def test_stages_metrics_and_thesis_terms_have_plain_names() -> None:
    assert [stage_label(code) for code in ("L1", "L2", "L3", "L4")] == [
        "Preparación de los datos",
        "Ajuste de modelos",
        "Validación con ventanas",
        "Evaluación final",
    ]
    assert metric_label("rmse") == "RMSE"
    assert metric_label("razon_sn") == "r frente a la línea base"
    assert glossary_hint("BH") and glossary_hint("rmse")
    assert glossary_hint("no-such-term") is None


def test_an_unknown_code_falls_back_to_itself_instead_of_breaking_the_page() -> None:
    assert sku_class_label("new_class") == "new_class"
    assert family_term("new_family").hint is None
    assert status_options("task", ("pendiente", "fallida")) == [
        ("", "Todos"),
        ("pendiente", "Pendiente"),
        ("fallida", "Fallida"),
    ]


# ---------------------------------------------------------------- fechas y duraciones
def test_instants_read_in_spanish_with_their_zone_and_keep_the_exact_value() -> None:
    instant = datetime(2026, 11, 3, 2, 2, 38, tzinfo=UTC)
    assert format_when(instant) == "3 nov 2026, 02:02 UTC"
    assert exact_when(instant) == "2026-11-03 02:02:38 UTC (hora universal coordinada)"
    # Another offset is shown in UTC, the zone the engine and the platform store.
    bogota = instant.astimezone(timezone(timedelta(hours=-5)))
    assert format_when(bogota) == "3 nov 2026, 02:02 UTC"
    assert format_when(datetime(2016, 9, 11, 7, 5)) == "11 sept 2016, 07:05"
    assert exact_when(None) == ""


def test_days_read_in_spanish_and_unknown_text_passes_through() -> None:
    assert format_date("2016-09-11") == "11 sept 2016"
    assert format_date(date(2017, 1, 5)) == "5 ene 2017"
    assert format_date("") == "—" and format_date(None) == "—"
    assert format_date("sin fecha") == "sin fecha"


def test_durations_read_at_a_glance() -> None:
    assert format_duration(45) == "45 s"
    assert format_duration(12 * 60 + 5) == "12 min"
    assert format_duration(3600 + 5 * 60) == "1 h 05 min"
    assert format_duration(2 * 86400 + 3 * 3600) == "2 d 3 h"
    assert format_duration(-3) == "0 s"
