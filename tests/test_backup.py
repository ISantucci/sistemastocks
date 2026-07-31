"""Tarea 3: backups con sqlite3 .backup() y logs."""
import sqlite3
from pathlib import Path
import pytest
from conftest import login, csrf_from, make_item, make_location


def test_backup_existe_y_no_vacio_en_backup_dir(A):
    with A.app.app_context():
        dest = A._backup_db("test")
    assert dest.exists()
    assert dest.stat().st_size > 0
    # queda dentro de BACKUP_DIR, no en BASE_DIR/backups
    assert Path(dest).parent == A.BACKUP_DIR
    assert A.BASE_DIR not in Path(dest).parents or A.BACKUP_DIR == (A.BASE_DIR / "backups")


def test_backup_abrible_con_sqlite_y_con_tablas(A):
    with A.app.app_context():
        dest = A._backup_db("test2")
    con = sqlite3.connect(str(dest))
    tablas = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    con.close()
    assert "items" in tablas and "users" in tablas


def test_backup_falla_si_no_existe_db(A, monkeypatch):
    monkeypatch.setattr(A, "DB_PATH", Path("/ruta/inexistente/stocks.db"))
    with A.app.app_context():
        with pytest.raises(Exception):
            A._backup_db("nope")


def test_operacion_destructiva_aborta_si_backup_falla(A, client, monkeypatch):
    # Preparamos datos
    it = make_item(A)
    loc = make_location(A, "Dep1")
    with A.app.app_context():
        A.upsert_stock(it.id, loc.id, 5)
        A.db.session.commit()
    login(client, "admin", "admin123")

    # Forzamos que el backup falle
    def boom(label):
        raise RuntimeError("backup falla simulado")

    monkeypatch.setattr(A, "_backup_db", boom)
    client.post("/admin/clear-stock", data={"confirm_text": "BORRAR-STOCK"})
    monkeypatch.undo()

    # El stock NO se borró (la operación abortó antes)
    assert A.Stock.query.count() == 1


def test_backup_manual_reutiliza_helper(A, client, monkeypatch):
    login(client, "admin", "admin123")
    called = {}

    real = A._backup_db

    def spy(label):
        called["label"] = label
        return real(label)

    monkeypatch.setattr(A, "_backup_db", spy)
    client.post("/admin/backup-db", data={})
    monkeypatch.undo()
    assert called.get("label") == "manual"
