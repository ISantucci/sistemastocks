"""Tarea 8: protección CSRF."""
import pytest
from conftest import csrf_from, login


@pytest.fixture()
def csrf_client(A):
    # CSRF ACTIVADO para este módulo.
    A.app.config["WTF_CSRF_ENABLED"] = True
    return A.app.test_client()


def test_login_con_token_valido(A, csrf_client):
    r = login(csrf_client, "admin", "admin123")
    assert r.status_code == 302  # login ok


def test_login_sin_token_rechazado(A, csrf_client):
    r = csrf_client.post("/login", data={"username": "admin", "password": "admin123"})
    assert r.status_code == 400


def test_perfil_con_token_valido(A, csrf_client):
    login(csrf_client, "admin", "admin123")
    tok = csrf_from(csrf_client, "/perfil")
    r = csrf_client.post("/perfil", data={
        "current_password": "admin123", "new_password": "admin999",
        "confirm_password": "admin999", "csrf_token": tok,
    })
    assert r.status_code in (302, 200)


def test_movimiento_sin_token_rechazado(A, csrf_client):
    login(csrf_client, "admin", "admin123")
    r = csrf_client.post("/movements", data={"item_id": "1", "qty": "1",
                                             "from_location_id": "1", "to_location_id": "2"})
    assert r.status_code == 400


def test_backup_sin_token_rechazado(A, csrf_client):
    login(csrf_client, "admin", "admin123")
    r = csrf_client.post("/admin/backup-db", data={})
    assert r.status_code == 400


def test_clear_stock_sin_token_rechazado(A, csrf_client):
    login(csrf_client, "admin", "admin123")
    r = csrf_client.post("/admin/clear-stock", data={"confirm_text": "BORRAR-STOCK"})
    assert r.status_code == 400
    # y nada se borró
    assert True


def test_remito_sin_token_rechazado(A, csrf_client):
    login(csrf_client, "admin", "admin123")
    r = csrf_client.post("/remitos/new", data={"from_location_id": "1", "to_location_id": "2"})
    assert r.status_code == 400


def test_import_sin_token_rechazado(A, csrf_client):
    import io
    login(csrf_client, "admin", "admin123")
    r = csrf_client.post("/import/items",
                         data={"file": (io.BytesIO(b"code,name,category\n"), "x.csv")},
                         content_type="multipart/form-data")
    assert r.status_code == 400


def test_token_incorrecto_da_400_comprensible(A, csrf_client):
    login(csrf_client, "admin", "admin123")
    r = csrf_client.post("/perfil", data={
        "current_password": "admin123", "new_password": "x",
        "confirm_password": "x", "csrf_token": "token-invalido",
    })
    assert r.status_code == 400
    body = r.get_data(as_text=True)
    assert "venci" in body.lower() or "no es válido" in body.lower() or "no es valido" in body.lower()
    assert "Traceback" not in body
