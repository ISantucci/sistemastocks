"""Que el dedupe y la exclusión Desde/Hacia lleguen a TODOS los roles.

El fix vive en un .js que carga base.html y se engancha en el <script> inline de
cada pantalla. Dos formas silenciosas de que no llegue a un rol:

  1. el rol ve una variante distinta del template (ej. el TECNICO con UNA sola
     ubicación recibe un <input hidden> en vez del <select> "Desde"),
  2. el bloque con el enganche está dentro de un {% if %} por rol y ese rol cae
     por la rama que no lo tiene.

Por eso se verifica por rol y por pantalla que el HTML servido traiga el script
y el enganche, no solo que responda 200.
"""
import pytest
from conftest import make_user, make_item, make_location, login

# OJO: "entrar a la pantalla" y "ver el formulario de carga" NO son lo mismo.
# /movements e /ingresos-egresos son 200 para el LECTOR (modo lectura del
# historial), pero el template le esconde el form. Modelar eso mal fue lo que
# hizo fallar la primera versión de este test, así que queda explícito:
#   ve    = roles a los que la ruta responde 200
#   carga = roles que además reciben el formulario (y por lo tanto el fix)
# (ruta, ve, carga, marca del enganche en el HTML)
PANTALLAS = [
    ("/movements/bulk", {"ADMIN", "SUPERVISOR"}, {"ADMIN", "SUPERVISOR"}, 'itemSel: ".bulk-item"'),
    ("/item-usage", {"ADMIN", "SUPERVISOR", "TECNICO"}, {"ADMIN", "SUPERVISOR", "TECNICO"}, 'itemSel: ".usage-item"'),
    ("/scrap", {"ADMIN", "SUPERVISOR"}, {"ADMIN", "SUPERVISOR"}, 'itemSel: ".scrap-item"'),
    ("/ingresos-egresos", {"ADMIN", "SUPERVISOR", "LECTOR"}, {"ADMIN", "SUPERVISOR"}, "itemSel: '.io-item'"),
]
# Pantallas con selects Desde/Hacia
CON_DESDE_HACIA = [
    ("/movements", {"ADMIN", "SUPERVISOR", "TECNICO", "LECTOR"}, {"ADMIN", "SUPERVISOR", "TECNICO"}),
    ("/movements/bulk", {"ADMIN", "SUPERVISOR"}, {"ADMIN", "SUPERVISOR"}),
]
ROLES = ["ADMIN", "SUPERVISOR", "TECNICO", "LECTOR"]
# El admin de bootstrap ya existe con ese username, así que los de prueba usan
# nombres propios. {rol: (username, password)}
CRED = {
    "ADMIN": ("admin", "admin123"),          # el que crea seed_defaults()
    "SUPERVISOR": ("u_sup", "pass1234"),
    "TECNICO": ("u_tec", "pass1234"),
    "LECTOR": ("u_lec", "pass1234"),
}


@pytest.fixture()
def esc(A):
    A.app.config["WTF_CSRF_ENABLED"] = False
    jaula = make_location(A, A.LOCATION_JAULA_TNG)
    truck = make_location(A, "Berlingo YA", is_truck=True)
    truck2 = make_location(A, "Kangoo", is_truck=True)
    for r in ROLES:
        if r == "ADMIN":
            continue                          # ya lo creó seed_defaults()
        u = make_user(A, CRED[r][0], r)
        if r == "TECNICO":
            A.db.session.add(A.LocationResponsible(location_id=truck.id, user_id=u.id))
    cable = make_item(A, code="CAB-001", name="Rollo cable RG58U")
    A.upsert_stock(cable.id, jaula.id, 10)
    A.upsert_stock(cable.id, truck.id, 10)
    A.db.session.commit()
    return {"jaula": jaula, "truck": truck, "truck2": truck2, "cable": cable}


def _cli(A, role):
    user, pw = CRED[role]
    c = A.app.test_client()
    login(c, user, pw)
    return c


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("url,ve,carga,marca", PANTALLAS)
def test_dedupe_presente_para_cada_rol(A, esc, role, url, ve, carga, marca):
    r = _cli(A, role).get(url)
    if role not in ve:
        assert r.status_code in (302, 403), f"{role} no debería entrar a {url}"
        return
    assert r.status_code == 200, f"{role} en {url} -> {r.status_code}"
    html = r.get_data(as_text=True)
    if role not in carga:
        # Entra en modo lectura: no debe recibir el formulario de carga.
        assert marca not in html, f"{role} recibe el form de carga en {url}"
        return
    assert "line_dedupe.js" in html, f"{role} en {url}: no carga line_dedupe.js"
    assert marca in html, f"{role} en {url}: no engancha el dedupe ({marca})"


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("url,ve,carga", CON_DESDE_HACIA)
def test_exclusion_desde_hacia_presente_para_cada_rol(A, esc, role, url, ve, carga):
    r = _cli(A, role).get(url)
    if role not in ve:
        assert r.status_code in (302, 403)
        return
    html = r.get_data(as_text=True)
    if role not in carga:
        assert 'name="to_location_id"' not in html, f"{role} recibe el form de carga en {url}"
        return
    assert "initFromToExclusion" in html, f"{role} en {url}: falta la exclusión Desde/Hacia"


def test_lector_entra_de_solo_lectura_pero_no_puede_cargar(A, esc):
    """Seguridad real, no visual: que el LECTOR no vea el form no alcanza.

    /movements e /ingresos-egresos le responden 200 (historial), así que se
    verifica también que un POST suyo no cree nada.
    """
    c = _cli(A, "LECTOR")
    assert c.get("/movements").status_code == 200
    antes = A.Movement.query.count()
    c.post("/movements", data={
        "item_id": str(esc["cable"].id), "qty": "1",
        "from_location_id": str(esc["jaula"].id),
        "to_location_id": str(esc["truck"].id),
    }, follow_redirects=True)
    assert A.Movement.query.count() == antes, "el LECTOR pudo registrar un movimiento"


def test_tecnico_con_una_sola_ubicacion_recibe_input_hidden(A, esc):
    """La variante de template que casi se escapa: 'Desde' no es un <select>.

    initFromToExclusion tiene que tolerarlo (un <input> no tiene .options).
    """
    html = _cli(A, "TECNICO").get("/movements").get_data(as_text=True)
    assert 'type="hidden" name="from_location_id"' in html
    assert "initFromToExclusion" in html


def test_tecnico_con_dos_ubicaciones_recibe_select(A, esc):
    tec = A.User.query.filter_by(username=CRED["TECNICO"][0]).first()
    A.db.session.add(A.LocationResponsible(location_id=esc["truck2"].id, user_id=tec.id))
    A.db.session.commit()
    html = _cli(A, "TECNICO").get("/movements").get_data(as_text=True)
    assert '<select name="from_location_id"' in html
    assert "initFromToExclusion" in html


@pytest.mark.parametrize("role", ["ADMIN", "SUPERVISOR", "TECNICO"])
def test_from_igual_to_rechazado_para_cada_rol(A, esc, role):
    """Refuerzo backend: ningún rol puede mover a la misma ubicación."""
    c = _cli(A, role)
    antes = A.Movement.query.count()
    c.post("/movements", data={
        "item_id": str(esc["cable"].id), "qty": "1",
        "from_location_id": str(esc["truck"].id),
        "to_location_id": str(esc["truck"].id),
    }, follow_redirects=True)
    assert A.Movement.query.count() == antes, f"{role} logró mover a la misma ubicación"


@pytest.mark.parametrize("role", ["ADMIN", "SUPERVISOR"])
def test_item_duplicado_rechazado_para_cada_rol(A, esc, role):
    c = _cli(A, role)
    antes = A.Movement.query.count()
    c.post("/movements/bulk", data={
        "from_location_id": str(esc["jaula"].id),
        "to_location_id": str(esc["truck"].id),
        "item_id[]": [str(esc["cable"].id), str(esc["cable"].id)],
        "qty[]": ["1", "1"],
        "generate_pending[]": ["0", "0"], "scrap_reason[]": ["", ""],
    }, follow_redirects=True)
    assert A.Movement.query.count() == antes, f"{role} logró cargar el ítem dos veces"


def test_tecnico_puede_usar_dedupe_en_utilizados(A, esc):
    """El TECNICO en Utilizados: 'Desde' es hidden y el dedupe igual se engancha."""
    html = _cli(A, "TECNICO").get("/item-usage").get_data(as_text=True)
    assert 'itemSel: ".usage-item"' in html
    assert "line_dedupe.js" in html
    c = _cli(A, "TECNICO")
    antes = A.Movement.query.count()
    c.post("/item-usage", data={
        "from_location_id": str(esc["truck"].id),
        "item_id[]": [str(esc["cable"].id), str(esc["cable"].id)],
        "qty[]": ["1", "1"],
    }, follow_redirects=True)
    assert A.Movement.query.count() == antes


def test_tecnico_dedupe_en_solicitud_de_repuestos(A, esc):
    html = _cli(A, "TECNICO").get("/solicitudes-repuestos").get_data(as_text=True)
    assert "initLineDedupe" in html, "el TECNICO no recibe el dedupe en repuestos"
