"""Cerrar una solicitud de repuestos entregando MÁS de lo pedido (2026-10-02).

Pedido de Ignacio: "así como se pueden cerrar las solicitudes de repuesto
poniendo menos de lo que pidieron, que se puedan poner más".

Antes el cierre rechazaba entregar más que lo pedido ("cantidad a entregar
inválida (0 a N)" / "elegiste más seriales que lo pedido"). Ahora el único tope
es lo que hay de verdad en la Jaula.

Lo que se prueba, sobre todo lo que NO tiene que cambiar:
- se puede entregar de más, con y sin serial, y la solicitud queda CERRADA;
- si alguna línea queda corta es CERRADA_PARCIAL, aunque otra reciba de más;
- entregar menos sigue siendo parcial, como siempre;
- no se puede entregar más de lo que hay en la Jaula (ni un serial que no esté
  ahí): se rechaza todo y no se mueve nada;
- los pendientes de una entrega de más siguen topeados por lo entregado;
- la pantalla precarga lo de siempre y deja subir hasta lo que hay en la Jaula.
"""
import re

import pytest
from conftest import make_user, make_item, make_category, make_location, login


@pytest.fixture()
def esc(A):
    A.app.config["WTF_CSRF_ENABLED"] = False
    jaula = make_location(A, A.LOCATION_JAULA_TNG)
    truck = make_location(A, "Camioneta Uno", is_truck=True)
    otra = make_location(A, "Deposito Otro")
    tec = make_user(A, "tec", "TECNICO", full_name="Tecnico Uno")
    A.db.session.add(A.LocationResponsible(location_id=truck.id, user_id=tec.id))
    hay = make_item(A, code="CAB-001", name="Cable con stock")
    poco = make_item(A, code="CAB-003", name="Conector con poco")
    cam = make_item(A, code="CAM-001", name="Camara domo",
                    category=make_category(A, "Camaras", "CAM"))
    cam.serialized = True
    A.upsert_stock(hay.id, jaula.id, 10)
    A.upsert_stock(poco.id, jaula.id, 2)
    A.db.session.commit()
    return {"jaula": jaula, "truck": truck, "otra": otra, "tec": tec,
            "hay": hay, "poco": poco, "cam": cam}


def _cli(A, user):
    c = A.app.test_client()
    login(c, user, "admin123" if user == "admin" else "pass1234")
    return c


def _pedir(A, esc, lineas):
    _cli(A, "tec").post("/solicitudes-repuestos/new", data={
        "dest_location_id": str(esc["truck"].id),
        "item_id[]": [str(it.id) for it, _ in lineas],
        "qty[]": [str(q) for _, q in lineas],
    }, follow_redirects=True)
    return A.RepairRequest.query.order_by(A.RepairRequest.id.desc()).first()


def _cerrar(A, rr, data):
    return _cli(A, "admin").post(
        f"/solicitudes-repuestos/{rr.id}/cerrar", data=data, follow_redirects=True
    ).get_data(as_text=True)


def _stock(A, item_id, loc_id):
    row = A.Stock.query.filter_by(item_id=item_id, location_id=loc_id).first()
    return row.quantity if row else 0


def _unidad(A, item, serial, loc):
    u = A.ItemUnit(item_id=item.id, serial=serial, status=A.UNIT_EN_STOCK,
                   location_id=loc.id)
    A.db.session.add(u)
    A.db.session.commit()
    return u


# ==========================================================================
# Entregar de más
# ==========================================================================

def test_sin_serial_se_puede_entregar_mas_de_lo_pedido(A, esc):
    rr = _pedir(A, esc, [(esc["hay"], 2)])
    ln = rr.lines[0]
    movs = A.Movement.query.count()
    html = _cerrar(A, rr, {f"qty_entregada_{ln.id}": "3"})

    rr = A.RepairRequest.query.get(rr.id)
    assert rr.status == "CERRADA"
    assert "cerrada (completa)" in html
    assert rr.lines[0].qty == 2 and rr.lines[0].qty_entregada == 3
    assert _stock(A, esc["hay"].id, esc["jaula"].id) == 7
    assert _stock(A, esc["hay"].id, esc["truck"].id) == 3
    assert A.Movement.query.count() == movs + 1
    m = A.Movement.query.order_by(A.Movement.id.desc()).first()
    assert (m.qty, m.from_location_id, m.to_location_id) == (
        3, esc["jaula"].id, esc["truck"].id)


def test_con_serial_se_pueden_entregar_mas_seriales_que_los_pedidos(A, esc):
    u1 = _unidad(A, esc["cam"], "SN-1", esc["jaula"])
    u2 = _unidad(A, esc["cam"], "SN-2", esc["jaula"])
    _unidad(A, esc["cam"], "SN-3", esc["jaula"])
    A.upsert_stock(esc["cam"].id, esc["jaula"].id, 3)
    A.db.session.commit()
    rr = _pedir(A, esc, [(esc["cam"], 1)])
    ln = rr.lines[0]
    _cerrar(A, rr, {f"unit_ids_{ln.id}": [str(u1.id), str(u2.id)]})

    rr = A.RepairRequest.query.get(rr.id)
    assert rr.status == "CERRADA"
    assert rr.lines[0].qty_entregada == 2
    for uid in (u1.id, u2.id):
        assert A.ItemUnit.query.get(uid).location_id == esc["truck"].id
    assert _stock(A, esc["cam"].id, esc["jaula"].id) == 1
    assert _stock(A, esc["cam"].id, esc["truck"].id) == 2


def test_una_linea_de_mas_y_otra_corta_es_parcial(A, esc):
    rr = _pedir(A, esc, [(esc["hay"], 2), (esc["poco"], 2)])
    l_hay, l_poco = rr.lines
    _cerrar(A, rr, {f"qty_entregada_{l_hay.id}": "4",
                    f"qty_entregada_{l_poco.id}": "1"})
    rr = A.RepairRequest.query.get(rr.id)
    assert rr.status == "CERRADA_PARCIAL"
    assert [l.qty_entregada for l in rr.lines] == [4, 1]


def test_entregar_menos_sigue_siendo_parcial(A, esc):
    rr = _pedir(A, esc, [(esc["hay"], 3)])
    ln = rr.lines[0]
    _cerrar(A, rr, {f"qty_entregada_{ln.id}": "1"})
    assert A.RepairRequest.query.get(rr.id).status == "CERRADA_PARCIAL"


def test_entregar_justo_lo_pedido_sigue_siendo_completa(A, esc):
    rr = _pedir(A, esc, [(esc["hay"], 2)])
    ln = rr.lines[0]
    _cerrar(A, rr, {f"qty_entregada_{ln.id}": "2"})
    assert A.RepairRequest.query.get(rr.id).status == "CERRADA"


# ==========================================================================
# El tope sigue siendo la Jaula
# ==========================================================================

def test_no_se_entrega_de_mas_lo_que_no_hay_en_jaula(A, esc):
    rr = _pedir(A, esc, [(esc["hay"], 1), (esc["poco"], 1)])
    l_hay, l_poco = rr.lines
    movs = A.Movement.query.count()
    html = _cerrar(A, rr, {f"qty_entregada_{l_hay.id}": "2",
                           f"qty_entregada_{l_poco.id}": "3"})
    assert "no hay tanto en Jaula" in html
    # Todo o nada: tampoco se movió la línea que sí era válida.
    assert A.RepairRequest.query.get(rr.id).status == "PENDIENTE"
    assert A.Movement.query.count() == movs
    assert _stock(A, esc["hay"].id, esc["jaula"].id) == 10
    assert _stock(A, esc["poco"].id, esc["jaula"].id) == 2


def test_un_serial_de_mas_que_no_esta_en_jaula_se_rechaza(A, esc):
    u1 = _unidad(A, esc["cam"], "SN-1", esc["jaula"])
    fuera = _unidad(A, esc["cam"], "SN-9", esc["otra"])
    A.upsert_stock(esc["cam"].id, esc["jaula"].id, 1)
    A.db.session.commit()
    rr = _pedir(A, esc, [(esc["cam"], 1)])
    ln = rr.lines[0]
    movs = A.Movement.query.count()
    html = _cerrar(A, rr, {f"unit_ids_{ln.id}": [str(u1.id), str(fuera.id)]})
    assert "ya no está disponible en la Jaula" in html
    assert A.RepairRequest.query.get(rr.id).status == "PENDIENTE"
    assert A.Movement.query.count() == movs
    assert A.ItemUnit.query.get(u1.id).location_id == esc["jaula"].id


# ==========================================================================
# Pendientes de una entrega de más
# ==========================================================================

def test_pendiente_de_una_entrega_de_mas_va_por_lo_entregado(A, esc):
    rr = _pedir(A, esc, [(esc["hay"], 2)])
    ln = rr.lines[0]
    _cerrar(A, rr, {f"qty_entregada_{ln.id}": "3", f"pending_{ln.id}": "1"})
    assert A.RepairRequest.query.get(rr.id).status == "CERRADA"
    # Un pendiente por unidad entregada, como siempre.
    assert A.PendingDelivery.query.count() == 3


def test_no_se_puede_pedir_devolver_mas_que_lo_entregado(A, esc):
    rr = _pedir(A, esc, [(esc["hay"], 2)])
    ln = rr.lines[0]
    movs = A.Movement.query.count()
    html = _cerrar(A, rr, {f"qty_entregada_{ln.id}": "3", f"pending_{ln.id}": "1",
                           f"pending_return_qty_{ln.id}": "4"})
    assert "no puede superar la entregada (3)" in html
    assert A.RepairRequest.query.get(rr.id).status == "PENDIENTE"
    assert A.Movement.query.count() == movs
    assert A.PendingDelivery.query.count() == 0


# ==========================================================================
# Pantalla
# ==========================================================================

def test_la_pantalla_precarga_lo_pedido_y_deja_subir_hasta_la_jaula(A, esc):
    rr = _pedir(A, esc, [(esc["hay"], 2), (esc["poco"], 5)])
    l_hay, l_poco = rr.lines
    html = _cli(A, "admin").get(f"/solicitudes-repuestos/{rr.id}").get_data(as_text=True)

    def campo(line_id):
        m = re.search(r'<input type="number" name="qty_entregada_%d"[^>]*>' % line_id, html)
        assert m, line_id
        tag = m.group(0)
        return (re.search(r'max="(\d+)"', tag).group(1),
                re.search(r'value="(\d+)"', tag).group(1))

    # Pidió 2, hay 10: precarga 2 (como siempre), deja llegar a 10.
    assert campo(l_hay.id) == ("10", "2")
    # Pidió 5, hay 2: precarga y tope son lo que hay (como siempre).
    assert campo(l_poco.id) == ("2", "2")
    assert "o más, hasta lo que haya en la Jaula" in html
