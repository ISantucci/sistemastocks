"""Responsable por defecto cuando la ubicación tiene varios (2026-10-01).

Bug reportado: en Carga múltiple, con una camioneta de DOS responsables, el
selector "Pendientes a nombre de" arrancaba vacío, estaba arriba de todo (lejos
de las filas) y nada frenaba el envío. El backend rechazaba la carga y, como el
rechazo recarga la pantalla, el usuario perdía todo lo que venía cargando.

Pedido de Ignacio: si nadie elige, que quede el PRIMERO por orden alfabético,
siempre, en todos lados donde pasaba lo mismo. Y que se pueda elegir.

Lo que se prueba, sobre todo lo que NO tiene que cambiar:
- sin elegir -> el primero alfabético (Movimientos, Carga múltiple, cierre de
  solicitud de repuestos y remito);
- eligiendo -> el elegido;
- un responsable ajeno a la ubicación se sigue rechazando;
- "alfabético" es como lo lee una persona: sin mayúsculas ni acentos;
- el pendiente SIN entrega no se completa solo (ahí el técnico es el dato);
- las pantallas no ofrecen la opción vacía y el selector de Carga múltiple
  quedó al pie, junto al botón.
"""
import os

import pytest
from conftest import make_user, make_item, make_location, login

_APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture()
def esc(A):
    A.app.config["WTF_CSRF_ENABLED"] = False
    ana = make_user(A, "ana", "TECNICO", full_name="Ana Alvarez")
    beto = make_user(A, "beto", "TECNICO", full_name="Beto Benitez")
    jaula = make_location(A, A.LOCATION_JAULA_TNG)
    truck = make_location(A, "Camioneta Doble", is_truck=True)
    # Se cargan AL REVÉS del orden alfabético: el default no puede depender
    # del orden en que se asignaron.
    for u in (beto, ana):
        A.db.session.add(A.LocationResponsible(location_id=truck.id, user_id=u.id))
    it1 = make_item(A, code="CAB-101", name="Cable uno")
    it2 = make_item(A, code="CAB-102", name="Cable dos")
    A.upsert_stock(it1.id, jaula.id, 20)
    A.upsert_stock(it2.id, jaula.id, 20)
    A.db.session.commit()
    return {"ana": ana, "beto": beto, "jaula": jaula, "truck": truck,
            "it1": it1, "it2": it2}


def _admin(A):
    c = A.app.test_client()
    login(c, "admin", "admin123")
    return c


def _bulk(c, esc, extra=None):
    data = {
        "from_location_id": str(esc["jaula"].id),
        "to_location_id": str(esc["truck"].id),
        "item_id[]": [str(esc["it1"].id), str(esc["it2"].id)],
        "qty[]": ["2", "1"],
        "generate_pending[]": ["1", "1"],
        "pending_comment[]": ["", ""],
        "pending_return_item_id[]": ["", ""],
        "pending_return_qty[]": ["", ""],
        "scrap_reason[]": ["", ""],
        "unit_ids[]": ["", ""],
    }
    data.update(extra or {})
    return c.post("/movements/bulk", data=data, follow_redirects=True)


# ------------------------------------------------------------ Carga múltiple

def test_carga_multiple_sin_elegir_queda_el_primero_alfabetico(A, esc):
    r = _bulk(_admin(A), esc)
    assert "Movimientos creados: 2" in r.get_data(as_text=True)
    pend = A.PendingDelivery.query.all()
    assert len(pend) == 3                      # 2 + 1, uno por unidad
    assert {p.responsible_to_id for p in pend} == {esc["ana"].id}


def test_carga_multiple_eligiendo_queda_el_elegido(A, esc):
    _bulk(_admin(A), esc, {"pending_responsible_id": str(esc["beto"].id)})
    pend = A.PendingDelivery.query.all()
    assert pend and {p.responsible_to_id for p in pend} == {esc["beto"].id}


def test_carga_multiple_responsable_ajeno_se_sigue_rechazando(A, esc):
    ajeno = make_user(A, "ajeno", "TECNICO", full_name="Aaron Ajeno")
    r = _bulk(_admin(A), esc, {"pending_responsible_id": str(ajeno.id)})
    assert "no es responsable" in r.get_data(as_text=True)
    assert A.PendingDelivery.query.count() == 0
    assert A.Movement.query.count() == 0       # todo o nada


def test_sin_responsables_sigue_siendo_error(A, esc):
    vacia = make_location(A, "Camioneta Sin Nadie", is_truck=True)
    r = _admin(A).post("/movements/bulk", data={
        "from_location_id": str(esc["jaula"].id),
        "to_location_id": str(vacia.id),
        "item_id[]": [str(esc["it1"].id)], "qty[]": ["1"],
        "generate_pending[]": ["1"], "pending_comment[]": [""],
        "pending_return_item_id[]": [""], "pending_return_qty[]": [""],
        "scrap_reason[]": [""], "unit_ids[]": [""],
    }, follow_redirects=True)
    assert "no tiene responsable" in r.get_data(as_text=True)
    assert A.Movement.query.count() == 0


# ------------------------------------------------------------ Movimientos

def test_movimiento_simple_sin_elegir_queda_el_primero(A, esc):
    _admin(A).post("/movements", data={
        "item_id": str(esc["it1"].id), "qty": "1",
        "from_location_id": str(esc["jaula"].id),
        "to_location_id": str(esc["truck"].id),
        "generate_pending": "1",
    }, follow_redirects=True)
    assert A.PendingDelivery.query.one().responsible_to_id == esc["ana"].id


# ------------------------------------------------------------ Solicitud de repuestos

def test_cierre_de_solicitud_sin_elegir_queda_el_primero(A, esc):
    c_tec = A.app.test_client()
    login(c_tec, "ana", "pass1234")
    c_tec.post("/solicitudes-repuestos/new", data={
        "dest_location_id": str(esc["truck"].id),
        "item_id[]": [str(esc["it1"].id)], "qty[]": ["1"],
    }, follow_redirects=True)
    rr = A.RepairRequest.query.one()
    ln = rr.lines[0]
    _admin(A).post(f"/solicitudes-repuestos/{rr.id}/cerrar", data={
        f"qty_entregada_{ln.id}": "1",
        f"pending_{ln.id}": "1",
    }, follow_redirects=True)
    assert A.RepairRequest.query.get(rr.id).status == "CERRADA"
    assert A.PendingDelivery.query.one().responsible_to_id == esc["ana"].id


def test_cierre_de_solicitud_ofrece_al_primero_ya_elegido(A, esc):
    c_tec = A.app.test_client()
    login(c_tec, "ana", "pass1234")
    c_tec.post("/solicitudes-repuestos/new", data={
        "dest_location_id": str(esc["truck"].id),
        "item_id[]": [str(esc["it1"].id)], "qty[]": ["1"],
    }, follow_redirects=True)
    rr = A.RepairRequest.query.one()
    html = _admin(A).get(f"/solicitudes-repuestos/{rr.id}").get_data(as_text=True)
    assert "Elegí el responsable" not in html
    assert f'value="{esc["ana"].id}" selected' in html


# ------------------------------------------------------------ Remito

def test_remito_sin_elegir_responsable_queda_el_primero(A, esc):
    admin = A.User.query.filter_by(role="ADMIN").first()
    y, seq, number = A.next_movement_number()
    mov = A.Movement(item_id=esc["it1"].id, qty=1, from_location_id=esc["jaula"].id,
                     to_location_id=esc["truck"].id, user_id=admin.id,
                     year=y, seq=seq, number=number)
    A.db.session.add(mov)
    # La Jaula también necesita responsable: el remito pide los dos lados.
    A.db.session.add(A.LocationResponsible(location_id=esc["jaula"].id, user_id=admin.id))
    A.db.session.commit()
    _admin(A).post("/remitos/new", data={
        "from_location_id": str(esc["jaula"].id),
        "to_location_id": str(esc["truck"].id),
        "movement_id": str(mov.id),
    }, follow_redirects=True)
    rem = A.Remito.query.one()
    assert rem.responsible_to_id == esc["ana"].id
    assert rem.responsible_from_id == admin.id


def test_remito_endpoint_manda_en_orden_alfabetico(A, esc):
    data = _admin(A).get(f"/remitos/responsables?location_id={esc['truck'].id}").get_json()
    assert [p["id"] for p in data["responsibles"]] == [esc["ana"].id, esc["beto"].id]


# ------------------------------------------------------------ Orden alfabético

def test_orden_alfabetico_sin_mayusculas_ni_acentos(A, esc):
    loc = make_location(A, "Camioneta Orden", is_truck=True)
    zoe = make_user(A, "zoe", "TECNICO", full_name="Zoe Zapata")
    angel = make_user(A, "angel", "TECNICO", full_name="ángel Acosta")
    bruno = make_user(A, "bruno", "TECNICO", full_name="Bruno Bravo")
    for u in (zoe, bruno, angel):
        A.db.session.add(A.LocationResponsible(location_id=loc.id, user_id=u.id))
    A.db.session.commit()
    with A.app.test_request_context():
        orden = [u.id for u in A.location_responsible_users(loc.id)]
    # SQLite solo ("ORDER BY full_name") ponía "ángel" al final.
    assert orden == [angel.id, bruno.id, zoe.id]


def test_sin_nombre_completo_ordena_por_usuario(A, esc):
    loc = make_location(A, "Camioneta Sin Nombre", is_truck=True)
    sin = make_user(A, "carla", "TECNICO", full_name="x")
    sin.full_name = ""
    con = make_user(A, "bea", "TECNICO", full_name="Bea Blanco")
    A.db.session.commit()
    for u in (sin, con):
        A.db.session.add(A.LocationResponsible(location_id=loc.id, user_id=u.id))
    A.db.session.commit()
    with A.app.test_request_context():
        orden = [u.id for u in A.location_responsible_users(loc.id)]
    # Antes el nombre vacío ("") quedaba primero y se llevaba el default.
    assert orden == [con.id, sin.id]


# ------------------------------------------------------------ Lo que NO cambia

def test_helper_sin_default_sigue_pidiendo_elegir(A, esc):
    """El pendiente SIN entrega usa el helper sin default: no se adivina."""
    with A.app.test_request_context():
        rid, err = A.resolve_pending_responsible(esc["truck"].id, "")
        assert rid is None and "más de un responsable" in err
        rid, err = A.resolve_pending_responsible(esc["truck"].id, "", default_first=True)
        assert rid == esc["ana"].id and err is None


# ------------------------------------------------------------ Pantallas

def _leer(*partes):
    with open(os.path.join(_APP_DIR, *partes), encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("ruta", [
    ("static", "js", "movements_bulk.js"),
    ("static", "js", "movements_pendiente.js"),
    ("static", "js", "remitos.js"),
    ("templates", "repair_request_detail.html"),
])
def test_los_selectores_no_ofrecen_la_opcion_vacia(ruta):
    txt = _leer(*ruta)
    # Las tres formas en que se armaba la opción vacía (los comentarios que
    # cuentan la historia pueden seguir nombrándola).
    assert 'textContent = "Elegí el responsable' not in txt
    assert '<option value="">Elegí el responsable' not in txt
    assert "disabled selected>Elegí responsable" not in txt


def test_carga_multiple_el_selector_esta_al_pie(A, esc):
    html = _admin(A).get("/movements/bulk").get_data(as_text=True)
    pos_lineas = html.index('id="bulk-lines"')
    pos_selector = html.index('id="bulk_pending_resp_box"')
    pos_boton = html.index("Guardar carga múltiple")
    assert pos_lineas < pos_selector < pos_boton
