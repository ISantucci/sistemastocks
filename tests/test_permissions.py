"""Tarea 5: permisos backend por rol (no alcanza con ocultar botones).

Se usa UN solo test_client y se re-loguea el rol antes de cada request
(login_user reemplaza la sesión). Evita el cruce de cookies entre clients.
"""
import pytest
from conftest import make_user, make_category, login

ALL = ["ADMIN", "SUPERVISOR", "TECNICO", "LECTOR"]
CREDS = {
    "ADMIN": ("admin", "admin123"),
    "SUPERVISOR": ("sup", "pass1234"),
    "TECNICO": ("tec", "pass1234"),
    "LECTOR": ("lec", "pass1234"),
}


@pytest.fixture()
def env(A):
    A.app.config["WTF_CSRF_ENABLED"] = False
    make_user(A, "sup", "SUPERVISOR")
    make_user(A, "tec", "TECNICO")
    make_user(A, "lec", "LECTOR")
    c = A.app.test_client()

    def as_role(role):
        login(c, *CREDS[role])
        return c

    return A, c, as_role


GET_MATRIX = [
    ("/", {"ADMIN", "SUPERVISOR", "TECNICO", "LECTOR"}),
    ("/stock", {"ADMIN", "SUPERVISOR", "TECNICO", "LECTOR"}),
    ("/stock/export.csv", {"ADMIN", "SUPERVISOR", "TECNICO", "LECTOR"}),
    ("/items", {"ADMIN", "SUPERVISOR", "TECNICO", "LECTOR"}),
    ("/perfil", {"ADMIN", "SUPERVISOR", "TECNICO", "LECTOR"}),
    ("/remitos", {"ADMIN", "SUPERVISOR", "TECNICO", "LECTOR"}),
    # El SUPERVISOR gestiona el catálogo de ítems igual que el ADMIN.
    ("/items/new", {"ADMIN", "SUPERVISOR"}),
    ("/items/export.xlsx", {"ADMIN", "SUPERVISOR", "LECTOR"}),
    ("/movements", {"ADMIN", "SUPERVISOR"}),
    ("/movements/bulk", {"ADMIN", "SUPERVISOR"}),
    ("/movements/export.csv", {"ADMIN", "SUPERVISOR"}),
    ("/pending-deliveries", {"ADMIN", "SUPERVISOR"}),
    ("/stock-alerts", {"ADMIN", "SUPERVISOR"}),
    ("/categories", {"ADMIN", "SUPERVISOR"}),
    ("/locations", {"ADMIN", "SUPERVISOR"}),
    ("/users", {"ADMIN"}),
    ("/admin", {"ADMIN"}),
    ("/admin/adjust-stock", {"ADMIN"}),
    ("/import/items", {"ADMIN"}),
]


@pytest.mark.parametrize("path,allowed", GET_MATRIX)
def test_get_matrix(env, path, allowed):
    A, c, as_role = env
    for role in ALL:
        as_role(role)
        r = c.get(path)
        if role in allowed:
            assert r.status_code == 200, f"{role} debería ver {path} (fue {r.status_code})"
        else:
            assert r.status_code in (302, 403), f"{role} NO debería ver {path} (fue {r.status_code})"


def test_supervisor_no_crea_categoria(env):
    A, c, as_role = env
    n0 = A.Category.query.count()
    as_role("SUPERVISOR").post("/categories", data={"name": "X", "prefix": "XYZ"})
    assert A.Category.query.count() == n0


def test_admin_crea_categoria(env):
    A, c, as_role = env
    n0 = A.Category.query.count()
    as_role("ADMIN").post("/categories", data={"name": "NuevaCat", "prefix": "NUE"})
    assert A.Category.query.count() == n0 + 1


def test_supervisor_no_crea_ubicacion(env):
    A, c, as_role = env
    n0 = A.Location.query.count()
    as_role("SUPERVISOR").post("/locations", data={"name": "UbicX"})
    assert A.Location.query.count() == n0


def test_admin_crea_ubicacion(env):
    A, c, as_role = env
    n0 = A.Location.query.count()
    as_role("ADMIN").post("/locations", data={"name": "UbicNueva"})
    assert A.Location.query.count() == n0 + 1


def test_supervisor_crea_item(env):
    """El SUPERVISOR trabaja el catálogo de ítems igual que el ADMIN.

    Cambio deliberado: antes esta ruta era solo ADMIN. Lo que sigue reservado
    a ADMIN es el panel /admin, la gestión de usuarios y la importación masiva
    (ver test_solo_admin_crea_usuarios y la matriz GET_MATRIX).
    """
    A, c, as_role = env
    make_category(A, "Cables", "CAB")
    n0 = A.Item.query.count()
    as_role("SUPERVISOR").post("/items/new", data={
        "category_id": A.Category.query.first().id, "name": "Item", "stock_min": "0",
    })
    assert A.Item.query.count() == n0 + 1


def test_tecnico_y_lector_no_crean_item(env):
    A, c, as_role = env
    make_category(A, "Cables", "CAB")
    n0 = A.Item.query.count()
    for role in ("TECNICO", "LECTOR"):
        as_role(role).post("/items/new", data={
            "category_id": A.Category.query.first().id, "name": f"Item{role}", "stock_min": "0",
        })
    assert A.Item.query.count() == n0


def test_solo_admin_crea_usuarios(env):
    A, c, as_role = env
    n0 = A.User.query.count()
    for role in ("SUPERVISOR", "TECNICO", "LECTOR"):
        as_role(role).post("/users", data={
            "username": f"x_{role}", "full_name": "X", "role": "LECTOR", "password": "pass1234",
        })
    assert A.User.query.count() == n0
