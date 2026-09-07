"""Tarea 8: protección CSRF."""
import time

import pytest
from itsdangerous import URLSafeTimedSerializer

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


# ------------------ vigencia del token (WTF_CSRF_TIME_LIMIT) ------------------
# El default de Flask-WTF es 1 hora y el reloj arranca al RENDERIZAR la pantalla.
# En /conteo el formulario queda abierto horas: con el default, al guardar el
# token ya habia vencido y se perdia el conteo entero. Estas pruebas fijan las
# dos mitades de la regla: una ventana amplia SI, ventana infinita NO.


def _token_envejecido(A, client, segundos, monkeypatch):
    """Token CSRF válido pero firmado con el reloj corrido N segundos hacia atrás.

    Es la única forma de probar el vencimiento sin esperar horas: se re-firma el
    mismo token crudo que ya tiene la sesión, con timestamp viejo.
    """
    with client.session_transaction() as sess:
        crudo = sess.get("csrf_token")
    assert crudo, "la sesión todavía no tiene token CSRF crudo"
    s = URLSafeTimedSerializer(A.app.secret_key, salt="wtf-csrf-token")
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() - segundos)
    try:
        return s.dumps(crudo)
    finally:
        monkeypatch.undo()


def test_time_limit_configurado_cubre_una_jornada(A):
    # Si alguien saca esta config, el default de Flask-WTF vuelve a 3600s y
    # /conteo vuelve a perder el trabajo de la sesión.
    assert A.app.config["WTF_CSRF_TIME_LIMIT"] >= 8 * 60 * 60


def test_token_de_dos_horas_sigue_siendo_valido(A, csrf_client, monkeypatch):
    """El caso real: pantalla abierta más de una hora y recién ahí se guarda."""
    login(csrf_client, "admin", "admin123")
    csrf_from(csrf_client, "/perfil")  # asegura token crudo en la sesión
    viejo = _token_envejecido(A, csrf_client, 2 * 60 * 60, monkeypatch)
    r = csrf_client.post("/perfil", data={
        "current_password": "admin123", "new_password": "admin999",
        "confirm_password": "admin999", "csrf_token": viejo,
    })
    assert r.status_code != 400, "un token de 2 horas no debería vencer"
    assert r.status_code in (302, 200)


def test_token_de_nueve_horas_sigue_venciendo(A, csrf_client, monkeypatch):
    """La ventana se amplió, no se eliminó: pasado el límite sigue rechazando."""
    login(csrf_client, "admin", "admin123")
    csrf_from(csrf_client, "/perfil")
    vencido = _token_envejecido(A, csrf_client, 9 * 60 * 60, monkeypatch)
    r = csrf_client.post("/perfil", data={
        "current_password": "admin123", "new_password": "admin999",
        "confirm_password": "admin999", "csrf_token": vencido,
    })
    assert r.status_code == 400
