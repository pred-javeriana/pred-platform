"""El mapa de presentación no mezcla vocabularios y no trata la poda como fallo."""

import pytest

from pred_platform.app.presentation import (
    UnknownStatus,
    abbreviate_id,
    describe_status,
    format_count,
    format_decimal,
    format_when,
    sku_class_label,
)


def test_task_states_are_five_and_distinguishable_without_sharing_icons() -> None:
    codes = ("pendiente", "ejecutando", "exitosa", "fallida", "no_ejecutable")
    shown = [describe_status("task", code) for code in codes]
    assert len({item.icon for item in shown}) == 5
    assert len({item.label for item in shown}) == 5


def test_pruned_trial_is_not_rendered_as_failure() -> None:
    pruned = describe_status("trial", "podado")
    failed = describe_status("trial", "fallido")
    assert pruned.label == "podada"
    assert pruned.modifier == "pruned"
    assert pruned.icon != failed.icon
    assert pruned.modifier != failed.modifier
    assert failed.label == "fallida"
    assert failed.modifier == "err"


def test_trial_contract_codes_map_to_interface_labels() -> None:
    assert describe_status("trial", "corriendo").label == "ejecutando"
    assert describe_status("trial", "completado").label == "completada"


def test_verdicts_keep_their_own_vocabulary() -> None:
    assert describe_status("verdict", "mantiene").label == "se sostiene"
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
    assert error.label == "error"
    assert warn.label == "advertencia"


def test_sku_class_labels_and_decimal_helpers() -> None:
    assert sku_class_label("smooth") == "Smooth (Suave)"
    assert sku_class_label("erratic") == "Erratic (Errática)"
    assert sku_class_label("intermittent") == "Intermittent (Intermitente)"
    assert sku_class_label("lumpy") == "Lumpy (Grumosa)"
    assert format_decimal(1.32) == "1,32"
    assert format_decimal(0.00827, 3) == "0,008"
    assert abbreviate_id("b1831d4585b72d7c") == "b1831d4585b7"
    assert abbreviate_id("short") == "short"
    assert format_when(None) == "—"


# ------------------------------------------------------------ la selección por SKU (C2)
def test_a_family_can_be_excluded_without_being_a_failure() -> None:
    excluded = describe_status("family", "excluida")
    failed = describe_status("family", "fallida")
    assert excluded.label == "excluida" and excluded.modifier == "neutral"
    assert excluded.icon != failed.icon and excluded.modifier != failed.modifier
    for code in ("pendiente", "ejecutando", "exitosa", "fallida", "no_ejecutable"):
        assert describe_status("family", code) == describe_status("task", code)
    with pytest.raises(UnknownStatus, match="family"):
        describe_status("family", "podado")
