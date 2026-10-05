"""El mapa de presentación no mezcla vocabularios y no trata la poda como fallo."""

import pytest

from pred_platform.app.presentation import (
    UnknownStatus,
    describe_status,
    format_count,
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
