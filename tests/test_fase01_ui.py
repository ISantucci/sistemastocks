"""Fase 0.1 · Tareas 6, 7, 8, 10: visibilidad y correcciones de UI/permisos."""
import os
import pytest
from conftest import make_user, login

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


# ---- Tarea 6: botón "Nuevo remito" por rol ----

def test_nuevo_remito_visible_admin_sup(env):
    A, c, as_role = env
    for role in ("ADMIN", "SUPERVISOR"):
        body = as_role(role).get("/remitos").get_data(as_text=True)
        assert "Nuevo remito" in body
        assert "modal-remito-new" in body


def test_nuevo_remito_oculto_tec_lec(env):
    A, c, as_role = env
    for role in ("TECNICO", "LECTOR"):
        body = as_role(role).get("/remitos").get_data(as_text=True)
        assert "Nuevo remito" not in body
        assert "modal-remito-new" not in body
        assert "REMITO_MOVS_URL" not in body  # scripts de creación no cargan


def test_remito_listado_detalle_accesible_los_cuatro(env):
    A, c, as_role = env
    # crear un remito como admin para tener detalle
    from conftest import make_item, make_location
    prov = A.Location.query.filter_by(name="Proveedor").first()
    dep = make_location(A, "Dep1")
    resp = make_user(A, "resp", "SUPERVISOR")
    A.db.session.add(A.LocationResponsible(location_id=dep.id, user_id=resp.id))
    it = make_item(A, code="CAB-001", name="Cable")
    A.upsert_stock(it.id, dep.id, 1)
    y, seq, num = A.next_movement_number()
    admin = A.User.query.filter_by(role="ADMIN").first()
    mov = A.Movement(item_id=it.id, qty=1, from_location_id=prov.id, to_location_id=dep.id,
                     user_id=admin.id, year=y, seq=seq, number=num)
    A.db.session.add(mov); A.db.session.commit()
    as_role("ADMIN").post("/remitos/new", data={
        "from_location_id": str(prov.id), "to_location_id": str(dep.id),
        "movement_id": str(mov.id), "responsible_to_id": str(resp.id)})
    r = A.Remito.query.first()
    for role in ("ADMIN", "SUPERVISOR", "TECNICO", "LECTOR"):
        assert as_role(role).get("/remitos").status_code == 200
        assert as_role(role).get(f"/remitos/{r.id}").status_code == 200


def test_post_manual_remito_bloqueado_tec_lec(env):
    A, c, as_role = env
    for role in ("TECNICO", "LECTOR"):
        as_role(role).post("/remitos/new", data={"from_location_id": "1", "to_location_id": "2"})
    assert A.Remito.query.count() == 0


# ---- Tarea 7: dashboard del TECNICO ----

def test_dashboard_tecnico_enlaza_item_usage(env):
    A, c, as_role = env
    body = as_role("TECNICO").get("/").get_data(as_text=True)
    assert 'href="/item-usage"' in body


def test_dashboard_tecnico_no_enlaza_movements(env):
    A, c, as_role = env
    body = as_role("TECNICO").get("/").get_data(as_text=True)
    assert 'href="/movements"' not in body


def test_item_usage_permitido_tecnico(env):
    A, c, as_role = env
    assert as_role("TECNICO").get("/item-usage").status_code == 200


def test_movements_bloqueado_tecnico(env):
    A, c, as_role = env
    assert as_role("TECNICO").get("/movements").status_code in (302, 403)


# ---- Tarea 8: solicitudes de compra solo ADMIN/SUPERVISOR ----

@pytest.mark.parametrize("role,allowed", [
    ("ADMIN", True), ("SUPERVISOR", True), ("TECNICO", False), ("LECTOR", False)])
def test_solicitudes_acceso_por_rol(env, role, allowed):
    A, c, as_role = env
    r = as_role(role).get("/solicitudes-compra")
    if allowed:
        assert r.status_code == 200
    else:
        assert r.status_code in (302, 403)


def test_nav_solicitudes_oculto_tec_lec(env):
    A, c, as_role = env
    for role in ("TECNICO", "LECTOR"):
        body = as_role(role).get("/").get_data(as_text=True)
        assert "Solicitudes de Compra" not in body


def test_post_manual_solicitud_lector_no_crea(env):
    A, c, as_role = env
    n0 = A.PurchaseRequest.query.count()
    as_role("LECTOR").post("/solicitudes-compra/new", data={})
    assert A.PurchaseRequest.query.count() == n0


# ---- Tarea 10: run_local.bat con debug false ----

def test_run_local_bat_debug_false():
    path = os.path.join(os.path.dirname(__file__), "..", "run_local.bat")
    txt = open(path, encoding="utf-8", errors="ignore").read()
    assert "APP_DEBUG=false" in txt
    assert "APP_DEBUG=true" not in txt


# ---- Tarea 9: mensaje de eliminación de item ----

def test_mensaje_eliminacion_item(env):
    A, c, as_role = env
    from conftest import make_item
    it = make_item(A, code="CAB-777", name="Borrable")
    r = as_role("ADMIN").post(f"/items/{it.id}/delete", data={}, follow_redirects=True)
    body = r.get_data(as_text=True)
    assert "eliminado correctamente" in body
    assert "no se reutiliza" not in body
