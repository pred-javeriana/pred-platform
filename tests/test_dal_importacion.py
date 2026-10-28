"""Import of the engine's ingest reports into the DAL (G1).

The fixtures in ``tests/fixtures/motor/`` are real outputs of ``pred_engine`` (the ones its
``ingest_csv_with_report`` writes): an accepted ingestion with three SKUs of different class, a
name conflict awaiting confirmation (and the same file once confirmed), a failed ingestion and a
rejected one.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from pred_platform.dal.importacion import (
    PANEL_COLUMNS,
    ReportImportError,
    import_reports,
    main,
    parse_report,
)
from pred_platform.dal.migrate import apply_migrations, load_migrations
from pred_platform.dal.schema import create_db

_FIXTURES = Path(__file__).parent / "fixtures" / "motor"


def _fixture(label: str) -> dict[str, Any]:
    return json.loads((_FIXTURES / f"ingesta.{label}.json").read_text(encoding="utf-8"))


def _place(root: Path, label: str, **changes: Any) -> Path:
    """Write fixture ``label`` (optionally edited) as ``<root>/ingestas/<ingest_id>.json``."""
    data = _fixture(label)
    data.update(changes)
    folder = root / "ingestas"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{data['ingest_id']}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "pred.db"
    create_db(path).close()
    return path


def _rows(db: Path, sql: str, *params: object) -> list[tuple[Any, ...]]:
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute(sql, params).fetchall()


# ---------------------------------------------------------------------- accepted
def test_an_accepted_ingestion_fills_the_ingestion_and_its_series(tmp_path: Path, db: Path) -> None:
    _place(tmp_path, "accepted")

    result = import_reports(db, tmp_path)

    assert (result.imported, result.failed) == (1, ())
    (row,) = _rows(
        db,
        "SELECT nombre_archivo, filas, skus, estado, estado_deposito, deposito_colision, ruta_raw,"
        " ruta_parquet, filas_validadas, filas_panel, t_star, umbral_adi, umbral_cv2,"
        " diagnostico_json, error_json FROM ingestas",
    )
    assert row == (
        "demo_canonico.csv", 270, 3, "accepted", "deposited", 0, "raw/demo_canonico.csv",
        "processed/demo_canonico.parquet", 270, 270, "2026-03-13", 1.32, 0.49, None, None,
    )  # fmt: skip
    series = _rows(db, "SELECT sku, n_obs, n_positive, adi, cv2, clase FROM series ORDER BY sku")
    assert [(s[0], s[5]) for s in series] == [
        ("FIL-0001", "smooth"),
        ("FIL-0002", "intermittent"),
        ("FIL-0003", "lumpy"),
    ]
    assert series[1][1:4] == (72, 24, 3.0)
    assert _rows(db, "SELECT COUNT(*) FROM bitacora_calidad") == [(0,)]


def test_importing_twice_leaves_the_same_rows(tmp_path: Path, db: Path) -> None:
    _place(tmp_path, "accepted")
    import_reports(db, tmp_path)
    content = "SELECT sku, n_obs, n_positive, adi, cv2, clase FROM series ORDER BY sku"
    before = _rows(db, content)

    again = import_reports(db, tmp_path)

    assert again.imported == 1
    assert _rows(db, "SELECT COUNT(*) FROM ingestas") == [(1,)]
    assert _rows(db, content) == before
    assert _rows(db, "SELECT COUNT(*) FROM series") == [(3,)]


def test_the_published_columns_are_the_contract_ones(tmp_path: Path) -> None:
    assert _fixture("accepted")["published"]["columns"] == PANEL_COLUMNS
    path = _place(tmp_path, "accepted")
    assert parse_report(path).published is not None


# ----------------------------------------------------------------- other statuses
def test_a_rejected_ingestion_keeps_its_header_diagnostic_and_error(
    tmp_path: Path, db: Path
) -> None:
    _place(tmp_path, "rejected")

    import_reports(db, tmp_path)

    estado, diagnostico, error = _rows(
        db, "SELECT estado, diagnostico_json, error_json FROM ingestas"
    )[0]
    assert estado == "rejected"
    assert json.loads(diagnostico)["entries"][0]["action"] == "Renombrar 'Date' a 'timestamp'"
    assert json.loads(error)["code"] == "ingest.header_rejected"
    assert _rows(db, "SELECT severidad, codigo, columna FROM bitacora_calidad") == [
        ("error", "ingest.header_rejected", "timestamp")
    ]
    assert _rows(db, "SELECT COUNT(*) FROM series") == [(0,)]


def test_a_failed_ingestion_keeps_the_offending_row(tmp_path: Path, db: Path) -> None:
    _place(tmp_path, "failed")

    import_reports(db, tmp_path)

    assert _rows(db, "SELECT estado, estado_deposito FROM ingestas") == [("failed", "deposited")]
    (log,) = _rows(db, "SELECT severidad, codigo, fila, columna FROM bitacora_calidad")
    assert log[:2] == ("error", "ingest.schema_barrier")
    assert log[3] == "lead_time_days" and isinstance(log[2], int)
    assert json.loads(_rows(db, "SELECT error_json FROM ingestas")[0][0])["row_index"] == log[2]


def test_a_name_conflict_waits_for_confirmation_and_the_engine_severity_is_translated(
    tmp_path: Path, db: Path
) -> None:
    _place(tmp_path, "needs_confirmation")

    import_reports(db, tmp_path)

    assert _rows(
        db, "SELECT estado, estado_deposito, deposito_colision, ruta_parquet FROM ingestas"
    ) == [("needs_confirmation", "already_present_different", 1, None)]
    # Not deposited yet: it keeps the name the user uploaded.
    assert _rows(db, "SELECT nombre_archivo FROM ingestas") == [("demo_canonico.csv",)]
    assert _rows(db, "SELECT severidad, codigo FROM bitacora_calidad") == [
        ("advertencia", "ingest.raw_conflict")
    ]


def test_confirming_the_conflict_replaces_the_waiting_report(tmp_path: Path, db: Path) -> None:
    _place(tmp_path, "needs_confirmation")
    import_reports(db, tmp_path)

    _place(tmp_path, "accepted_keep_both")  # same SHA-256: the engine rewrote the same file
    import_reports(db, tmp_path)

    assert _rows(db, "SELECT COUNT(*) FROM ingestas") == [(1,)]
    assert _rows(db, "SELECT estado, estado_deposito, deposito_colision FROM ingestas") == [
        ("accepted", "deposited", 1)
    ]
    # Kept under its versioned name so it is not confused with the original file.
    (nombre,) = _rows(db, "SELECT nombre_archivo FROM ingestas")[0]
    assert nombre == "demo_canonico-6cffc729.csv"
    assert _rows(db, "SELECT COUNT(*) FROM bitacora_calidad") == [(0,)]
    assert _rows(db, "SELECT COUNT(*) FROM series") == [(3,)]


def test_two_different_ingestions_coexist(tmp_path: Path, db: Path) -> None:
    _place(tmp_path, "accepted")
    _place(tmp_path, "failed")

    result = import_reports(db, tmp_path)

    assert result.imported == 2
    estados = sorted(r[0] for r in _rows(db, "SELECT estado FROM ingestas"))
    assert estados == ["accepted", "failed"]


# ------------------------------------------------------------------ invalid files
def _failure(tmp_path: Path, db: Path, path: Path) -> ReportImportError:
    result = import_reports(db, tmp_path)
    assert result.imported == 0 and len(result.failed) == 1
    assert result.failed[0].path == path
    return result.failed[0]


def test_another_major_version_is_rejected(tmp_path: Path, db: Path) -> None:
    path = _place(tmp_path, "accepted", schema="pred-engine.ingesta/2")
    assert "no soportada" in str(_failure(tmp_path, db, path))


def test_a_newer_minor_with_extra_fields_still_imports(tmp_path: Path, db: Path) -> None:
    _place(tmp_path, "accepted", campo_nuevo={"x": 1})
    assert import_reports(db, tmp_path).imported == 1


def test_a_missing_schema_field_is_rejected(tmp_path: Path, db: Path) -> None:
    data = _fixture("accepted")
    del data["schema"]
    folder = tmp_path / "ingestas"
    folder.mkdir()
    path = folder / f"{data['ingest_id']}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert "falta el campo schema" in str(_failure(tmp_path, db, path))


def test_a_class_outside_the_contract_is_rejected(tmp_path: Path, db: Path) -> None:
    data = _fixture("accepted")
    data["topology"]["metrics"][0]["sku_class"] = "Smooth"
    path = _place(tmp_path, "accepted", topology=data["topology"])
    failure = _failure(tmp_path, db, path)
    assert "sku_class" in str(failure)
    assert _rows(db, "SELECT COUNT(*) FROM series") == [(0,)]


def test_a_repeated_sku_is_rejected(tmp_path: Path, db: Path) -> None:
    data = _fixture("accepted")
    data["topology"]["metrics"].append(dict(data["topology"]["metrics"][0]))
    path = _place(tmp_path, "accepted", topology=data["topology"])
    assert "repite un SKU" in str(_failure(tmp_path, db, path))


def test_an_accepted_report_without_topology_is_rejected(tmp_path: Path, db: Path) -> None:
    path = _place(tmp_path, "accepted", topology=None)
    assert "exige published, topology y reserve" in str(_failure(tmp_path, db, path))


def test_an_accepted_report_declaring_synthetic_origin_without_published_sha_is_rejected(
    tmp_path: Path, db: Path
) -> None:
    source_file = dict(_fixture("accepted")["source_file"], datos_sinteticos=True)
    path = _place(tmp_path, "accepted", source_file=source_file)
    failure = _failure(tmp_path, db, path)
    assert "un origen declarado exige published.sha256" in str(failure)
    assert _rows(db, "SELECT COUNT(*) FROM ingestas") == [(0,)]


def test_an_accepted_report_declaring_real_origin_without_published_sha_is_rejected(
    tmp_path: Path, db: Path
) -> None:
    source_file = dict(_fixture("accepted")["source_file"], datos_sinteticos=False)
    path = _place(tmp_path, "accepted", source_file=source_file)
    failure = _failure(tmp_path, db, path)
    assert "un origen declarado exige published.sha256" in str(failure)
    assert _rows(db, "SELECT COUNT(*) FROM ingestas") == [(0,)]


def test_an_accepted_report_with_null_origin_without_published_sha_is_accepted(
    tmp_path: Path, db: Path
) -> None:
    source_file = dict(_fixture("accepted")["source_file"], datos_sinteticos=None)
    _place(tmp_path, "accepted", source_file=source_file)
    result = import_reports(db, tmp_path)
    assert result.ok and result.imported == 1
    assert _rows(db, "SELECT COUNT(*) FROM ingestas") == [(1,)]


def test_an_accepted_report_with_declared_origin_and_valid_published_sha_is_accepted(
    tmp_path: Path, db: Path
) -> None:
    source_file = dict(_fixture("accepted")["source_file"], datos_sinteticos=True)
    published = dict(_fixture("accepted")["published"], sha256="a" * 64)
    _place(tmp_path, "accepted", source_file=source_file, published=published)
    result = import_reports(db, tmp_path)
    assert result.ok and result.imported == 1
    assert _rows(db, "SELECT COUNT(*) FROM ingestas") == [(1,)]


def test_a_failed_report_with_declared_origin_without_published_sha_is_imported(
    tmp_path: Path, db: Path
) -> None:
    source_file = dict(_fixture("failed")["source_file"], datos_sinteticos=True)
    _place(tmp_path, "failed", source_file=source_file)
    result = import_reports(db, tmp_path)
    assert result.ok and result.imported == 1
    assert _rows(db, "SELECT estado FROM ingestas") == [("failed",)]


def test_a_failed_report_without_error_is_rejected(tmp_path: Path, db: Path) -> None:
    path = _place(tmp_path, "failed", error=None)
    assert "exige error" in str(_failure(tmp_path, db, path))


def test_the_file_name_must_be_the_ingest_id(tmp_path: Path, db: Path) -> None:
    folder = tmp_path / "ingestas"
    folder.mkdir()
    path = folder / ("0" * 64 + ".json")
    path.write_text((_FIXTURES / "ingesta.accepted.json").read_text(encoding="utf-8"))
    assert "no coincide con ingest_id" in str(_failure(tmp_path, db, path))


def test_a_corrupt_file_is_reported_and_the_others_are_imported(tmp_path: Path, db: Path) -> None:
    _place(tmp_path, "accepted")
    bad = tmp_path / "ingestas" / "roto.json"
    bad.write_text("{no es json", encoding="utf-8")

    result = import_reports(db, tmp_path)

    assert result.imported == 1
    assert [f.path for f in result.failed] == [bad]
    assert not result.ok


# ------------------------------------------------------------ environment problems
def test_no_reports_folder_is_not_an_error(tmp_path: Path, db: Path) -> None:
    assert import_reports(db, tmp_path).imported == 0


def test_a_missing_database_asks_to_migrate_first(tmp_path: Path) -> None:
    _place(tmp_path, "accepted")
    with pytest.raises(ReportImportError, match="migre primero"):
        import_reports(tmp_path / "no_existe.db", tmp_path)


def test_a_database_without_the_report_columns_asks_to_migrate_first(tmp_path: Path) -> None:
    _place(tmp_path, "accepted")
    old = tmp_path / "old.db"
    with closing(sqlite3.connect(old)) as conn:
        apply_migrations(conn, load_migrations()[:1])
    with pytest.raises(ReportImportError, match="versión 1"):
        import_reports(old, tmp_path)


# ------------------------------------------------------------------------- CLI
def test_cli_migrates_then_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _place(tmp_path, "accepted")
    db_path = tmp_path / "cli.db"
    monkeypatch.setenv("PRED_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("PRED_DB_PATH", str(db_path))

    assert main([]) == 0

    assert "imported 1 report(s)" in capsys.readouterr().out
    assert _rows(db_path, "SELECT estado FROM ingestas") == [("accepted",)]


def test_cli_status_compares_without_changing_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _place(tmp_path, "accepted")
    monkeypatch.setenv("PRED_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("PRED_DB_PATH", str(tmp_path / "pred.db"))

    assert main(["--status"]) == 0

    assert "1 report(s)" in capsys.readouterr().out
    assert not (tmp_path / "pred.db").exists()


def test_cli_exit_code_is_1_when_a_report_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _place(tmp_path, "accepted")
    (tmp_path / "ingestas" / "roto.json").write_text("{", encoding="utf-8")
    monkeypatch.setenv("PRED_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("PRED_DB_PATH", str(tmp_path / "pred.db"))

    assert main([]) == 1

    assert "skipped: roto.json" in capsys.readouterr().err


def test_cli_rejects_an_invalid_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PRED_DATA_SOURCE", "mock")
    assert main([]) == 2
    assert "PRED_DATA_SOURCE" in capsys.readouterr().err
