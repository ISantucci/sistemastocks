"""Un solo selector de seriales, el de Egresos (2026-10-01).

Pedido tras la capacitación: "que todo lo que sea selección de serial sea igual
que en egresos". Ahí se elige con un botón «Elegir S/N» que abre un popup con
los seriales, un contador y Confirmar. El resto de las pantallas dibujaba una
lista de casillas dentro del formulario (o armada por el template).

Ahora todas usan el mismo popup (static/js/serial_picker.js):
- Movimientos, Utilizados, Descartes y Carga múltiple, por initSerialPicker
  (el marcado de esas pantallas no cambió: cambió cómo se dibuja);
- Pendientes, Reparaciones y Entrega de repuestos, por los selectores
  `.js-sn-picker` que arma el template.

Lo que viaja al servidor NO cambió (unit_id / unit_ids[] / unit_ids_<línea>):
por eso el backend no se tocó y las suites de seriales de siempre siguen
pasando sin cambios. Estas pruebas son guardas: si alguien borra un enganche,
el selector desaparece sin avisar.

El comportamiento en el navegador (abrir, tildar, confirmar, frenar el envío)
se verificó en Chromium: ver 03_Estado_Actual.md.
"""
import json
import re

import pytest
from conftest import make_user, make_item, make_category, make_location, login


def _leer(ruta):
    with open(ruta, encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture()
def esc(A):
    A.app.config["WTF_CSRF_ENABLED"] = False
    cat = make_category(A, "Camaras", "CAM")
    tec = make_user(A, "tec", "TECNICO")
    jaula = make_location(A, A.LOCATION_JAULA_TNG)
    truck = make_location(A, "Camioneta Tec", is_truck=True)
    make_location(A, A.LOCATION_RECUPERADO, is_external=True)
    rep = make_location(A, A.LOCATION_EN_REPARACION)
    A.db.session.add(A.LocationResponsible(location_id=truck.id, user_id=tec.id))
    cam = make_item(A, code="CAM-001", name="Camara domo", category=cat)
    cam.serialized = True
    A.db.session.commit()
    return {"jaula": jaula, "truck": truck, "rep": rep, "cam": cam, "tec": tec}


def _admin(A):
    c = A.app.test_client()
    login(c, "admin", "admin123")
    return c


def _unidad(A, item, serial, loc):
    u = A.ItemUnit(item_id=item.id, serial=serial, status=A.UNIT_EN_STOCK,
                   location_id=loc.id)
    A.db.session.add(u)
    A.db.session.commit()
    return u


def _pickers(html):
    """[{data-*: valor}] de cada div .js-sn-picker de la página."""
    out = []
    for m in re.finditer(r'<div class="[^"]*js-sn-picker[^"]*"(.*?)>', html, re.S):
        attrs = {}
        for k, a, b in re.findall(r'(data-[a-z-]+)=(?:"([^"]*)"|\'([^\']*)\')', m.group(1)):
            attrs[k] = a or b
        out.append(attrs)
    return out


# ==========================================================================
# El componente
# ==========================================================================

def test_el_componente_es_el_popup_de_egresos():
    js = _leer("static/js/serial_picker.js")
    assert "function openSerialModal" in js
    assert "Elegir S/N" in js
    assert "Seleccionados " in js and " · disponibles en " in js     # el contador
    assert "function initStaticSerialPickers" in js
    # Las pantallas multifila siguen publicando en el mismo campo de siempre.
    assert "idsInput" in js and "summaryInput" in js
    # Ya no hay casillas dentro del formulario: viven en el popup.
    assert 'cb.name = "unit_id"' not in js


def test_frena_el_envio_si_falta_elegir_como_en_egresos():
    js = _leer("static/js/serial_picker.js")
    assert "Hay un ítem serializado sin seriales elegidos. Usá «Elegir S/N»." in js
    assert "preventDefault" in js


def test_el_popup_compartido_tiene_el_estilo_de_egresos():
    css = _leer("static/css/app.css")
    assert ".sn-row" in css


def test_egresos_suma_el_automatico():
    js = _leer("static/js/ingresos_egresos.js")
    assert "function autoSn" in js
    assert "(automático)" in js


# ==========================================================================
# Pantallas armadas por el template
# ==========================================================================

def test_pendientes_usa_el_popup(A, esc):
    y, seq, number = A.next_movement_number()
    m = A.Movement(item_id=esc["cam"].id, qty=1,
                   from_location_id=esc["jaula"].id, to_location_id=esc["truck"].id,
                   user_id=1, observation="entrega", year=y, seq=seq, number=number)
    A.db.session.add(m)
    A.db.session.flush()
    A.db.session.add(A.PendingDelivery(movement_id=m.id, responsible_from_id=1,
                                       responsible_to_id=esc["tec"].id,
                                       item_id=esc["cam"].id, return_qty=1))
    A.db.session.commit()
    _unidad(A, esc["cam"], "SN-A", esc["truck"])
    _unidad(A, esc["cam"], "SN-B", esc["truck"])

    html = _admin(A).get("/pending-deliveries").get_data(as_text=True)
    (p,) = _pickers(html)
    assert p["data-name"] == "unit_id" and p["data-mode"] == "exact"
    assert p["data-qty"] == "1"
    assert [s for _, s in json.loads(p["data-units"])] == ["SN-A", "SN-B"]
    assert p["data-origin"] == "Camioneta Tec"
    assert 'type="checkbox" name="unit_id"' not in html


def test_la_mesa_usa_el_popup_cuando_hay_que_elegir(A, esc):
    _unidad(A, esc["cam"], "SN-M1", esc["rep"])
    _unidad(A, esc["cam"], "SN-M2", esc["rep"])
    A.upsert_stock(esc["cam"].id, esc["rep"].id, 2)
    A.db.session.add(A.Repair(item_id=esc["cam"].id, quantity=1, status="EN_REPARACION",
                              source_location_id=esc["truck"].id, created_by_user_id=1))
    A.db.session.commit()

    html = _admin(A).get("/reparaciones").get_data(as_text=True)
    (p,) = _pickers(html)
    assert p["data-name"] == "unit_id" and p["data-mode"] == "exact"
    assert sorted(s for _, s in json.loads(p["data-units"])) == ["SN-M1", "SN-M2"]
    assert 'type="checkbox" name="unit_id"' not in html
    assert "reparaciones.js" in html


def test_entrega_de_repuestos_usa_el_popup_hasta_n(A, esc):
    _unidad(A, esc["cam"], "SN-J1", esc["jaula"])
    _unidad(A, esc["cam"], "SN-J2", esc["jaula"])
    A.upsert_stock(esc["cam"].id, esc["jaula"].id, 2)
    A.db.session.commit()

    tc = A.app.test_client()
    login(tc, "tec")
    tc.post("/solicitudes-repuestos/new", data={
        "dest_location_id": str(esc["truck"].id),
        "item_id[]": [str(esc["cam"].id)], "qty[]": ["1"],
    })
    rr = A.RepairRequest.query.first()
    assert rr is not None

    html = _admin(A).get(f"/solicitudes-repuestos/{rr.id}").get_data(as_text=True)
    (p,) = _pickers(html)
    ln = rr.lines[0]
    # "hasta N" con N = seriales disponibles en la Jaula (2), no lo pedido (1):
    # desde 2026-10-02 se puede entregar de más (test_repuestos_entregar_de_mas).
    assert p["data-mode"] == "max" and p["data-qty"] == "2"
    assert p["data-name"] == f"unit_ids_{ln.id}" and p["data-line"] == str(ln.id)
    assert sorted(s for _, s in json.loads(p["data-units"])) == ["SN-J1", "SN-J2"]
    assert f'type="checkbox" name="unit_ids_{ln.id}"' not in html


def test_lo_que_manda_el_popup_lo_sigue_entendiendo_el_cierre(A, esc):
    """El popup publica "unit_ids_<línea>" como inputs ocultos, igual que las
    casillas de antes: el cierre de la solicitud no cambió."""
    u1 = _unidad(A, esc["cam"], "SN-J1", esc["jaula"])
    _unidad(A, esc["cam"], "SN-J2", esc["jaula"])
    A.upsert_stock(esc["cam"].id, esc["jaula"].id, 2)
    A.db.session.commit()
    tc = A.app.test_client()
    login(tc, "tec")
    tc.post("/solicitudes-repuestos/new", data={
        "dest_location_id": str(esc["truck"].id),
        "item_id[]": [str(esc["cam"].id)], "qty[]": ["1"],
    })
    rr = A.RepairRequest.query.first()
    ln = rr.lines[0]
    _admin(A).post(f"/solicitudes-repuestos/{rr.id}/cerrar", data={
        f"unit_ids_{ln.id}": str(u1.id),
    })
    u = A.ItemUnit.query.get(u1.id)
    assert u.location_id == esc["truck"].id


def test_la_mesa_no_ofrece_la_unidad_de_otra_reparacion(A, esc):
    """Una reparación vieja elige entre las de la mesa, salvo las que ya son de
    otra reparación (ver test_reparaciones_seriales)."""
    jaula = esc["jaula"]
    u = _unidad(A, esc["cam"], "SN-AJENA", esc["rep"])
    A.upsert_stock(esc["cam"].id, esc["rep"].id, 1)
    A.create_repairs(item=esc["cam"], qty=1, units=[u], status="EN_REPARACION",
                     source_location_id=jaula.id, created_by_user_id=1)
    _unidad(A, esc["cam"], "SN-LIBRE-1", esc["rep"])
    _unidad(A, esc["cam"], "SN-LIBRE-2", esc["rep"])
    A.upsert_stock(esc["cam"].id, esc["rep"].id, 2)
    A.db.session.add(A.Repair(item_id=esc["cam"].id, quantity=1, status="EN_REPARACION",
                              source_location_id=jaula.id, created_by_user_id=1))
    A.db.session.commit()

    html = _admin(A).get("/reparaciones").get_data(as_text=True)
    (p,) = _pickers(html)
    assert sorted(s for _, s in json.loads(p["data-units"])) == ["SN-LIBRE-1", "SN-LIBRE-2"]
