"""Fase 0.1 · Tarea 3: reference_link de extremo a extremo."""
import sqlite3
import pytest
from conftest import make_category, make_item, login


def test_modelo_tiene_columna(A):
    assert hasattr(A.Item, "reference_link")


def test_base_nueva_crea_la_columna(A):
    # El fixture A ya hizo create_all; la tabla items debe tener reference_link.
    cols = {c["name"] for c in A.db.session.execute(
        A.db.text("PRAGMA table_info(items)")
    ).mappings()}
    assert "reference_link" in cols


def test_ensure_sqlite_schema_agrega_a_base_vieja(A, tmp_path, monkeypatch):
    # Prueba la función productiva REAL (no un ALTER TABLE manual): simulamos una
    # base "vieja" sin reference_link y verificamos que ensure_sqlite_schema() la
    # agregue, y que sea idempotente en una segunda pasada.
    from pathlib import Path
    dbfile = tmp_path / "old.db"
    con = sqlite3.connect(dbfile)
    con.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, code TEXT, name TEXT, "
                "category_id INTEGER, trackable INTEGER, is_active INTEGER, stock_min INTEGER)")
    con.commit()
    cols_before = {r[1] for r in con.execute("PRAGMA table_info(items)")}
    con.close()
    assert "reference_link" not in cols_before

    # Apuntamos la función real a la base temporal y la ejecutamos.
    monkeypatch.setattr(A, "DB_PATH", Path(dbfile))
    A.ensure_sqlite_schema()

    con = sqlite3.connect(dbfile)
    cols_after = [r[1] for r in con.execute("PRAGMA table_info(items)")]
    con.close()
    assert "reference_link" in cols_after
    assert cols_after.count("reference_link") == 1

    # Idempotencia: segunda pasada no lanza excepción ni duplica la columna.
    A.ensure_sqlite_schema()
    con = sqlite3.connect(dbfile)
    cols_2 = [r[1] for r in con.execute("PRAGMA table_info(items)")]
    con.close()
    assert cols_2.count("reference_link") == 1


def test_alta_html_guarda_link(A, client):
    login(client, "admin", "admin123")
    make_category(A, "Cables", "CAB")
    cat = A.Category.query.first()
    client.post("/items/new", data={
        "category_id": cat.id, "name": "Item link", "stock_min": "0",
        "reference_link": "https://tienda/producto",
    })
    it = A.Item.query.filter_by(name="Item link").first()
    assert it is not None and it.reference_link == "https://tienda/producto"


def test_alta_ajax_guarda_link(A, client):
    login(client, "admin", "admin123")
    make_category(A, "Cables", "CAB")
    cat = A.Category.query.first()
    client.post("/items/new",
                data={"category_id": cat.id, "name": "Item ajax", "stock_min": "0",
                      "reference_link": "https://x/ajax"},
                headers={"X-Requested-With": "XMLHttpRequest"})
    it = A.Item.query.filter_by(name="Item ajax").first()
    assert it is not None and it.reference_link == "https://x/ajax"


def test_edicion_actualiza_link(A, client):
    login(client, "admin", "admin123")
    it = make_item(A, code="CAB-001", name="Item")
    client.post(f"/items/{it.id}/edit", data={
        "name": "Item", "category_id": it.category_id, "stock_min": "0",
        "is_active": "on", "reference_link": "https://nuevo/link",
    })
    A.db.session.expire_all()
    assert A.Item.query.get(it.id).reference_link == "https://nuevo/link"


def test_edicion_permite_vaciar_link(A, client):
    login(client, "admin", "admin123")
    it = make_item(A, code="CAB-001", name="Item")
    it.reference_link = "https://viejo/link"
    A.db.session.commit()
    client.post(f"/items/{it.id}/edit", data={
        "name": "Item", "category_id": it.category_id, "stock_min": "0",
        "is_active": "on", "reference_link": "",
    })
    A.db.session.expire_all()
    assert A.Item.query.get(it.id).reference_link is None


def test_listado_muestra_abrir(A, client):
    login(client, "admin", "admin123")
    make_item(A, code="CAB-001", name="Item").reference_link = "https://ver/aca"
    A.db.session.commit()
    body = client.get("/items").get_data(as_text=True)
    assert "Abrir" in body and 'rel="noopener noreferrer"' in body


def test_persiste_tras_reiniciar_sesion(A, client):
    login(client, "admin", "admin123")
    it = make_item(A, code="CAB-001", name="Item")
    it.reference_link = "https://persist/link"
    A.db.session.commit()
    iid = it.id
    A.db.session.remove()  # reinicia la sesión SQLAlchemy
    assert A.Item.query.get(iid).reference_link == "https://persist/link"
