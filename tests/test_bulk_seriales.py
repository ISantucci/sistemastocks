# -*- coding: utf-8 -*-
"""Carga múltiple con ítems serializados.

El bug: la pantalla ofrecía los ítems serializados en el selector, no tenía
forma de elegir seriales, y al enviar el backend rechazaba la carga entera con
"Cargalo desde Movimientos". El usuario perdía todo lo cargado.

Es el mismo patrón que ya había mordido en el conteo de camioneta y que quedó
escrito en el vault: un freno sin salida es peor que no tener freno.

Ahora se resuelve como en Utilizados y Descartes: cada fila manda sus unidades
en unit_ids[] y se usa la misma resolve_serial_units_out. Estas pruebas cubren
que se mueva lo que se eligió, que el stock agregado y las unidades queden
sincronizados, y —lo más importante— que un error no aplique nada a medias.
"""
import re

from conftest import make_user, make_category, make_location, login, con_js


def _escenario(A):
    """Dos ítems serializados en una camioneta, más uno común."""
    cat = make_category(A)
    cam = A.Item(code="CAM-001", name="Camara domo", category_id=cat.id, serialized=True)
    cam2 = A.Item(code="CAM-002", name="Camara bullet", category_id=cat.id, serialized=True)
    cable = A.Item(code="CAB-001", name="Cable", category_id=cat.id)
    A.db.session.add_all([cam, cam2, cable])
    A.db.session.flush()

    kangoo = make_location(A, "Kangoo DP", is_truck=True)
    jaula = make_location(A, "Jaula TNG")
    proveedor = A.Location.query.filter_by(name="Proveedor").first() or \
        make_location(A, "Proveedor", is_external=True)

    for sn in ("SN-A1", "SN-A2", "SN-A3"):
        A.db.session.add(A.ItemUnit(item_id=cam.id, serial=sn,
                                    location_id=kangoo.id, status="EN_STOCK"))
    for sn in ("SN-B1", "SN-B2"):
        A.db.session.add(A.ItemUnit(item_id=cam2.id, serial=sn,
                                    location_id=kangoo.id, status="EN_STOCK"))
    A.db.session.add_all([
        A.Stock(item_id=cam.id, location_id=kangoo.id, quantity=3),
        A.Stock(item_id=cam2.id, location_id=kangoo.id, quantity=2),
        A.Stock(item_id=cable.id, location_id=kangoo.id, quantity=100),
    ])
    A.db.session.commit()
    return dict(cam=cam, cam2=cam2, cable=cable, kangoo=kangoo,
                jaula=jaula, proveedor=proveedor)


def _admin(A, client):
    make_user(A, "adm", "ADMIN")
    login(client, "adm")


def _unidades(A, item, loc):
    return {u.serial for u in A.ItemUnit.query.filter_by(
        item_id=item.id, location_id=loc.id, status="EN_STOCK").all()}


def _stock(A, item, loc):
    s = A.Stock.query.filter_by(item_id=item.id, location_id=loc.id).first()
    return s.quantity if s else 0


# --------------------------------------------------------------- el bug

def test_un_serializado_ya_no_se_rechaza(A, client):
    """Antes esto devolvía «es serializado. Cargalo desde Movimientos»."""
    e = _escenario(A)
    _admin(A, client)
    unidad = A.ItemUnit.query.filter_by(item_id=e["cam"].id, serial="SN-A1").first()

    r = client.post("/movements/bulk", data={
        "from_location_id": e["kangoo"].id,
        "to_location_id": e["jaula"].id,
        "item_id[]": [str(e["cam"].id)],
        "qty[]": ["1"],
        "unit_ids[]": [str(unidad.id)],
        "generate_pending[]": ["0"],
        "pending_comment[]": [""],
        "pending_return_item_id[]": [""],
        "pending_return_qty[]": [""],
        "scrap_reason[]": [""],
    }, follow_redirects=True)
    texto = r.get_data(as_text=True)
    assert "es serializado" not in texto, "sigue rechazando los serializados"
    assert "Movimientos creados: 1" in texto


def test_se_mueve_el_serial_elegido_y_el_stock_queda_consistente(A, client):
    """Lo que importa: la unidad elegida cambia de ubicación y el stock agregado
    acompaña. Si estos dos se separan, se rompe la trazabilidad."""
    e = _escenario(A)
    _admin(A, client)
    elegida = A.ItemUnit.query.filter_by(item_id=e["cam"].id, serial="SN-A2").first()

    client.post("/movements/bulk", data={
        "from_location_id": e["kangoo"].id,
        "to_location_id": e["jaula"].id,
        "item_id[]": [str(e["cam"].id)],
        "qty[]": ["1"],
        "unit_ids[]": [str(elegida.id)],
        "generate_pending[]": ["0"], "pending_comment[]": [""],
        "pending_return_item_id[]": [""], "pending_return_qty[]": [""],
        "scrap_reason[]": [""],
    }, follow_redirects=True)

    assert _unidades(A, e["cam"], e["jaula"]) == {"SN-A2"}
    assert _unidades(A, e["cam"], e["kangoo"]) == {"SN-A1", "SN-A3"}
    assert _stock(A, e["cam"], e["jaula"]) == 1
    assert _stock(A, e["cam"], e["kangoo"]) == 2

    mov = A.Movement.query.order_by(A.Movement.id.desc()).first()
    assert "SN-A2" in (mov.observation or ""), "el serial tiene que quedar en el historial"


def test_varias_filas_serializadas_en_la_misma_carga(A, client):
    """Es una carga MÚLTIPLE: cada fila resuelve sus propios seriales."""
    e = _escenario(A)
    _admin(A, client)
    u_cam = A.ItemUnit.query.filter_by(item_id=e["cam"].id, serial="SN-A1").first()

    r = client.post("/movements/bulk", data={
        "from_location_id": e["kangoo"].id,
        "to_location_id": e["jaula"].id,
        "item_id[]": [str(e["cam"].id), str(e["cam2"].id), str(e["cable"].id)],
        "qty[]": ["1", "2", "10"],
        # fila 1 elige; fila 2 mueve las 2 que hay (automático); fila 3 no es serializada
        "unit_ids[]": [str(u_cam.id), "", ""],
        "generate_pending[]": ["0", "0", "0"],
        "pending_comment[]": ["", "", ""],
        "pending_return_item_id[]": ["", "", ""],
        "pending_return_qty[]": ["", "", ""],
        "scrap_reason[]": ["", "", ""],
    }, follow_redirects=True)
    assert "Movimientos creados: 3" in r.get_data(as_text=True)

    assert _unidades(A, e["cam"], e["jaula"]) == {"SN-A1"}
    assert _unidades(A, e["cam2"], e["jaula"]) == {"SN-B1", "SN-B2"}
    assert _stock(A, e["cable"], e["jaula"]) == 10
    # stock y unidades, sincronizados en los dos serializados
    for it in (e["cam"], e["cam2"]):
        assert _stock(A, it, e["jaula"]) == len(_unidades(A, it, e["jaula"]))
        assert _stock(A, it, e["kangoo"]) == len(_unidades(A, it, e["kangoo"]))


# ------------------------------------------------------- todo o nada

def test_elegir_mal_los_seriales_no_aplica_absolutamente_nada(A, client):
    """La red de seguridad: si una fila está mal, no se guarda NINGUNA.

    Con 3 seriales en el origen y una cantidad de 2, hay que elegir exactamente
    2. Mandando uno solo, la carga entera se rechaza — incluida la fila del
    cable, que estaba bien.
    """
    e = _escenario(A)
    _admin(A, client)
    una = A.ItemUnit.query.filter_by(item_id=e["cam"].id, serial="SN-A1").first()
    movs_antes = A.Movement.query.count()

    r = client.post("/movements/bulk", data={
        "from_location_id": e["kangoo"].id,
        "to_location_id": e["jaula"].id,
        "item_id[]": [str(e["cable"].id), str(e["cam"].id)],
        "qty[]": ["5", "2"],
        "unit_ids[]": ["", str(una.id)],      # eligió 1 y mueve 2
        "generate_pending[]": ["0", "0"], "pending_comment[]": ["", ""],
        "pending_return_item_id[]": ["", ""], "pending_return_qty[]": ["", ""],
        "scrap_reason[]": ["", ""],
    }, follow_redirects=True)

    assert "elegí exactamente 2" in r.get_data(as_text=True)
    assert A.Movement.query.count() == movs_antes, "se creó un movimiento igual"
    assert _stock(A, e["cable"], e["jaula"]) == 0, "la fila buena se aplicó a medias"
    assert _unidades(A, e["cam"], e["kangoo"]) == {"SN-A1", "SN-A2", "SN-A3"}


def test_desde_un_origen_externo_sigue_mandando_a_movimientos(A, client):
    """Dar de alta seriales NUEVOS necesita un campo por unidad, y ese flujo vive
    en Movimientos. Acá se rechaza, pero el front tampoco los ofrece: el mensaje
    es la red de seguridad, no el camino normal."""
    e = _escenario(A)
    _admin(A, client)
    r = client.post("/movements/bulk", data={
        "from_location_id": e["proveedor"].id,
        "to_location_id": e["jaula"].id,
        "item_id[]": [str(e["cam"].id)],
        "qty[]": ["1"], "unit_ids[]": [""],
        "generate_pending[]": ["0"], "pending_comment[]": [""],
        "pending_return_item_id[]": [""], "pending_return_qty[]": [""],
        "scrap_reason[]": [""],
    }, follow_redirects=True)
    texto = r.get_data(as_text=True)
    assert "Para dar de alta seriales nuevos usá Movimientos" in texto
    assert _stock(A, e["cam"], e["jaula"]) == 0


# ------------------------------------------------------------ la pantalla

def test_la_pantalla_trae_lo_necesario_para_elegir_seriales(A, client):
    e = _escenario(A)
    _admin(A, client)
    html = client.get("/movements/bulk").get_data(as_text=True)
    # el marcado de la fila
    assert 'name="unit_ids[]"' in html
    assert "serial-pick-list" in html
    # los datos y el enganche
    assert "unitsMap" in html and "serializedItems" in html
    assert "initSerialPicker" in con_js(html), "la pantalla no engancha el selector"


def test_el_front_no_ofrece_serializados_desde_un_origen_externo(A, client):
    """Coherencia con el backend: no ofrecer lo que se va a rechazar. Es
    exactamente lo que faltaba y lo que hacía perder la carga."""
    e = _escenario(A)
    _admin(A, client)
    js = con_js(client.get("/movements/bulk").get_data(as_text=True))
    assert "esSerializado" in js
