"""Tarea 6: filtro q en /users (username, full_name, role)."""
from conftest import make_user, login


def _setup(A, client):
    make_user(A, "jperez", "SUPERVISOR", full_name="Juan Perez")
    make_user(A, "mgomez", "TECNICO", full_name="Maria Gomez")
    make_user(A, "lector1", "LECTOR", full_name="Ana Lopez")
    login(client, "admin", "admin123")


def _body(client, q=None):
    path = "/users" + (f"?q={q}" if q is not None else "")
    return client.get(path).get_data(as_text=True)


def test_busqueda_por_username(A, client):
    _setup(A, client)
    b = _body(client, "jperez")
    assert "jperez" in b and "mgomez" not in b


def test_busqueda_por_nombre_completo(A, client):
    _setup(A, client)
    b = _body(client, "Maria")
    assert "mgomez" in b and "jperez" not in b


def test_busqueda_por_rol(A, client):
    _setup(A, client)
    b = _body(client, "TECNICO")
    assert "mgomez" in b and "jperez" not in b


def test_busqueda_sin_coincidencias(A, client):
    _setup(A, client)
    b = _body(client, "zzzznadazzz")
    assert "jperez" not in b and "mgomez" not in b


def test_q_vacio_muestra_todos(A, client):
    _setup(A, client)
    b = _body(client, "")
    assert "jperez" in b and "mgomez" in b and "lector1" in b


def test_conserva_valor_en_input(A, client):
    _setup(A, client)
    b = _body(client, "Maria")
    assert 'value="Maria"' in b
