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
    # El LISTADO lo ven los cuatro roles.
    for role in ("ADMIN", "SUPERVISOR", "TECNICO", "LECTOR"):
        assert as_role(role).get("/remitos").status_code == 200
    # El DETALLE tambien, salvo el TECNICO, que solo abre los remitos donde es
    # parte (proteccion anti-IDOR deliberada). Ver test_remitos.py.
    for role in ("ADMIN", "SUPERVISOR", "LECTOR"):
        assert as_role(role).get(f"/remitos/{r.id}").status_code == 200
    assert as_role("TECNICO").get(f"/remitos/{r.id}").status_code == 302


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


def test_dashboard_tecnico_enlaza_movements_y_no_catalogo(env):
    """El TECNICO tiene vista acotada de movimientos, pero NO la seccion Items.

    Confirmado con Ignacio (2026-08-13): ve los items de su camioneta desde
    /stock, no el catalogo.
    """
    A, c, as_role = env
    body = as_role("TECNICO").get("/").get_data(as_text=True)
    assert 'href="/movements"' in body
    assert 'href="/items"' not in body, "el menu no debe ofrecerle una seccion que el backend rebota"


def test_item_usage_permitido_tecnico(env):
    A, c, as_role = env
    assert as_role("TECNICO").get("/item-usage").status_code == 200


def test_movements_permitido_tecnico(env):
    """El TECNICO entra a /movements con alcance acotado a sus ubicaciones.

    Lo que NO puede hacer (mover desde ubicacion ajena, generar pendientes) se
    verifica en test_tecnico_scope.py, que es donde importa.
    """
    A, c, as_role = env
    assert as_role("TECNICO").get("/movements").status_code == 200


# ---- Tarea 8: solicitudes de compra solo ADMIN/SUPERVISOR ----

# El LECTOR consulta y reporta: VE las solicitudes de compra pero no las genera
# (el POST /solicitudes-compra/new sigue siendo ADMIN/SUPERVISOR, y eso lo
# verifica test_post_manual_solicitud_lector_no_crea). Confirmado 2026-08-13.
@pytest.mark.parametrize("role,allowed", [
    ("ADMIN", True), ("SUPERVISOR", True), ("TECNICO", False), ("LECTOR", True)])
def test_solicitudes_acceso_por_rol(env, role, allowed):
    A, c, as_role = env
    r = as_role(role).get("/solicitudes-compra")
    if allowed:
        assert r.status_code == 200
    else:
        assert r.status_code in (302, 403)


def test_nav_solicitudes_oculto_solo_al_tecnico(env):
    """El menu tiene que coincidir con el backend, en los dos sentidos."""
    A, c, as_role = env
    body_tec = as_role("TECNICO").get("/").get_data(as_text=True)
    assert "Solicitudes de Compra" not in body_tec

    body_lec = as_role("LECTOR").get("/").get_data(as_text=True)
    assert "Solicitudes de Compra" in body_lec, (
        "el LECTOR puede verlas: ocultarle el link seria un menu mentiroso"
    )


def test_post_manual_solicitud_lector_no_crea(env):
    A, c, as_role = env
    n0 = A.PurchaseRequest.query.count()
    as_role("LECTOR").post("/solicitudes-compra/new", data={})
    assert A.PurchaseRequest.query.count() == n0


# ---- Tarea 10: run_local.bat con debug false ----

def test_no_hay_scripts_con_debug_activado():
    """run_local.bat ya no existe. Lo que importa es que ningun script del repo
    deje APP_DEBUG=true: con debug activo, Flask expone una consola ejecutable.
    """
    raiz = os.path.join(os.path.dirname(__file__), "..")
    ofensores = []
    for carpeta, _dirs, archivos in os.walk(raiz):
        if any(x in carpeta for x in (".git", ".venv", "node_modules", "backups", "data")):
            continue
        for nombre in archivos:
            if not nombre.endswith((".bat", ".cmd", ".ps1", ".sh", ".yml", ".yaml")):
                continue
            ruta = os.path.join(carpeta, nombre)
            txt = open(ruta, encoding="utf-8", errors="ignore").read()
            if "APP_DEBUG=true" in txt.replace(" ", ""):
                ofensores.append(nombre)
    assert not ofensores, f"scripts con debug activado: {ofensores}"


# ---- Tarea 9: mensaje de eliminación de item ----

def test_mensaje_eliminacion_item(env):
    A, c, as_role = env
    from conftest import make_item
    it = make_item(A, code="CAB-777", name="Borrable")
    r = as_role("ADMIN").post(f"/items/{it.id}/delete", data={}, follow_redirects=True)
    body = r.get_data(as_text=True)
    assert "eliminado correctamente" in body
    assert "no se reutiliza" not in body
