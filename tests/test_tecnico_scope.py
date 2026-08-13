"""Alcance del rol TECNICO — autorización por OBJETO, no por pantalla.

Por qué existe este archivo: la matriz de `test_permissions.py` verifica quién
entra a cada URL, pero el TÉCNICO accede a varias pantallas con alcance
**acotado**. Ahí el riesgo no es el 403 que falta: es ver o tocar datos de otro
técnico estando legítimamente adentro de la pantalla.

Reglas confirmadas con Ignacio (2026-08-13):
  1. El técnico ve SUS pendientes.
  2. El técnico NO entra a la sección Ítems, pero SÍ ve los ítems que tiene en
     su camioneta (ubicación asignada).
  3. El lector ve las solicitudes de compra pero no las genera.
"""
import re

import pytest
from conftest import make_user, make_item, make_location, login


@pytest.fixture()
def esc(A):
    """Dos técnicos con camionetas distintas y un ítem exclusivo de cada uno."""
    A.app.config["WTF_CSRF_ENABLED"] = False

    tec_a = make_user(A, "tec_a", "TECNICO")
    tec_b = make_user(A, "tec_b", "TECNICO")

    dep = make_location(A, "Deposito")
    truck_a = make_location(A, "Camioneta A", is_truck=True)
    truck_b = make_location(A, "Camioneta B", is_truck=True)

    A.db.session.add(A.LocationResponsible(location_id=truck_a.id, user_id=tec_a.id))
    A.db.session.add(A.LocationResponsible(location_id=truck_b.id, user_id=tec_b.id))
    A.db.session.commit()

    it_a = make_item(A, code="AAA-001", name="Item de A")
    it_b = make_item(A, code="BBB-001", name="Item de B")
    it_dep = make_item(A, code="DEP-001", name="Item solo en deposito")

    A.upsert_stock(it_a.id, truck_a.id, 5)
    A.upsert_stock(it_b.id, truck_b.id, 5)
    A.upsert_stock(it_dep.id, dep.id, 50)
    A.db.session.commit()

    return {
        "tec_a": tec_a, "tec_b": tec_b, "dep": dep,
        "truck_a": truck_a, "truck_b": truck_b,
        "it_a": it_a, "it_b": it_b, "it_dep": it_dep,
    }


def _como(A, username):
    c = A.app.test_client()
    login(c, username)
    return c


def _cuerpo_tabla(html):
    m = re.search(r"<tbody>(.*?)</tbody>", html, re.S)
    return m.group(1) if m else ""


# ------------------------------------------------------- regla 2: catálogo


def test_tecnico_no_entra_al_catalogo_de_items(A, esc):
    assert _como(A, "tec_a").get("/items").status_code in (302, 403)


def test_selector_de_stock_solo_muestra_items_de_su_camioneta(A, esc):
    """Antes el selector le volcaba el catálogo COMPLETO, incluido stock ajeno."""
    html = _como(A, "tec_a").get("/stock").get_data(as_text=True)
    assert "AAA-001" in html, "debe ver los ítems de su propia camioneta"
    assert "BBB-001" not in html, "no debe ver ítems de la camioneta de otro técnico"
    assert "DEP-001" not in html, "no debe ver ítems que no tiene asignados"


def test_api_de_busqueda_respeta_el_mismo_alcance(A, esc):
    """Acotar el <select> y dejar la API abierta sería seguridad visual."""
    data = _como(A, "tec_a").get("/api/items/search").get_json()
    codigos = {d["code"] for d in data}
    assert "AAA-001" in codigos
    assert "BBB-001" not in codigos
    assert "DEP-001" not in codigos


def test_api_de_busqueda_no_filtra_por_termino_ajeno(A, esc):
    """Buscar explícitamente el código de otro técnico tampoco lo revela."""
    data = _como(A, "tec_a").get("/api/items/search?q=BBB-001").get_json()
    assert data == []


def test_tecnico_sin_ubicacion_no_ve_ningun_item(A, esc):
    make_user(A, "tec_huerfano", "TECNICO")
    data = _como(A, "tec_huerfano").get("/api/items/search").get_json()
    assert data == []


def test_otros_roles_siguen_viendo_el_catalogo_completo(A, esc):
    """El acotamiento es SOLO para el técnico: no debe afectar a nadie más."""
    make_user(A, "sup", "SUPERVISOR")
    codigos = {d["code"] for d in _como(A, "sup").get("/api/items/search").get_json()}
    assert {"AAA-001", "BBB-001", "DEP-001"} <= codigos


# ------------------------------------------------------- stock por ubicación


def test_tecnico_no_ve_stock_de_otra_camioneta(A, esc):
    cuerpo = _cuerpo_tabla(_como(A, "tec_a").get("/stock").get_data(as_text=True))
    assert "AAA-001" in cuerpo
    assert "BBB-001" not in cuerpo


def test_tecnico_no_puede_forzar_otra_ubicacion_por_querystring(A, esc):
    """Pasar el id de la camioneta ajena a mano no debe cambiar el alcance."""
    url = f"/stock?location_id={esc['truck_b'].id}"
    cuerpo = _cuerpo_tabla(_como(A, "tec_a").get(url).get_data(as_text=True))
    assert "BBB-001" not in cuerpo, "el filtro por ubicación no puede ser manipulable"


def test_export_csv_respeta_el_alcance_del_tecnico(A, esc):
    """El export es otra puerta a los mismos datos: tiene que filtrar igual."""
    url = f"/stock/export.csv?location_id={esc['truck_b'].id}"
    csv_text = _como(A, "tec_a").get(url).get_data(as_text=True)
    assert "BBB-001" not in csv_text


# ------------------------------------------------------- regla 1: pendientes


def test_tecnico_entra_a_pendientes(A, esc):
    assert _como(A, "tec_a").get("/pending-deliveries").status_code == 200


def test_tecnico_no_puede_generar_pendientes_desde_movimientos(A, esc):
    """Generar un pendiente es de ADMIN/SUPERVISOR, aunque el técnico vea la pantalla."""
    n0 = A.PendingDelivery.query.count()
    _como(A, "tec_a").post("/movements", data={
        "item_id": str(esc["it_a"].id), "qty": "1",
        "from_location_id": str(esc["truck_a"].id),
        "to_location_id": str(esc["dep"].id),
        "generate_pending": "1", "pending_comment": "intento",
    })
    assert A.PendingDelivery.query.count() == n0


# ------------------------------------------------------- movimientos


def test_tecnico_no_puede_mover_desde_ubicacion_ajena(A, esc):
    """El caso que importa: entra a /movements, pero no opera stock de otro."""
    qty_antes = A.Stock.query.filter_by(
        item_id=esc["it_b"].id, location_id=esc["truck_b"].id
    ).first().quantity

    _como(A, "tec_a").post("/movements", data={
        "item_id": str(esc["it_b"].id), "qty": "1",
        "from_location_id": str(esc["truck_b"].id),
        "to_location_id": str(esc["dep"].id),
    })

    qty_despues = A.Stock.query.filter_by(
        item_id=esc["it_b"].id, location_id=esc["truck_b"].id
    ).first().quantity
    assert qty_despues == qty_antes, "el stock ajeno no se debe poder mover"


def test_tecnico_si_puede_mover_desde_su_ubicacion(A, esc):
    """Contrapeso: el filtro no debe romper la operación legítima."""
    qty_antes = A.Stock.query.filter_by(
        item_id=esc["it_a"].id, location_id=esc["truck_a"].id
    ).first().quantity

    _como(A, "tec_a").post("/movements", data={
        "item_id": str(esc["it_a"].id), "qty": "1",
        "from_location_id": str(esc["truck_a"].id),
        "to_location_id": str(esc["dep"].id),
    })

    qty_despues = A.Stock.query.filter_by(
        item_id=esc["it_a"].id, location_id=esc["truck_a"].id
    ).first().quantity
    assert qty_despues == qty_antes - 1


# ------------------------------------------------------- regla 3: lector


def test_lector_ve_solicitudes_de_compra(A, esc):
    make_user(A, "lec", "LECTOR")
    assert _como(A, "lec").get("/solicitudes-compra").status_code == 200


def test_lector_no_puede_generar_solicitudes(A, esc):
    make_user(A, "lec", "LECTOR")
    n0 = A.PurchaseRequest.query.count()
    r = _como(A, "lec").post("/solicitudes-compra/new", data={})
    assert r.status_code in (302, 403)
    assert A.PurchaseRequest.query.count() == n0
