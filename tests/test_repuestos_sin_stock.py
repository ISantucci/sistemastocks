"""Solicitud de repuestos de ítems SIN stock en la Jaula (2026-09-30).

Pedido de los técnicos tras la capacitación: poder pedir un repuesto aunque la
Jaula no lo tenga, para que quede pendiente y le recuerde a Ignacio comprarlo.

Antes el backend rechazaba el pedido ("no tiene stock en la Jaula, no se puede
solicitar") y el selector solo ofrecía lo que había.

Lo que se prueba, sobre todo lo que NO tiene que cambiar:
- el técnico puede pedir cualquier ítem ACTIVO (los inactivos siguen afuera);
- se le avisa "sin stock en Jaula", sin mostrarle cuánto hay;
- pedir no toca stock ni movimientos;
- el recordatorio de compra (Alertas de Stock, "A comprar", "faltan") se deriva
  de las solicitudes PENDIENTE y desaparece solo;
- ENTREGAR sigue exigiendo stock real en la Jaula.
"""
import pytest
from conftest import make_user, make_item, make_location, login


@pytest.fixture()
def esc(A):
    A.app.config["WTF_CSRF_ENABLED"] = False
    jaula = make_location(A, A.LOCATION_JAULA_TNG)
    truck = make_location(A, "Camioneta Uno", is_truck=True)
    tec = make_user(A, "tec", "TECNICO", full_name="Tecnico Uno")
    make_user(A, "sup", "SUPERVISOR")
    A.db.session.add(A.LocationResponsible(location_id=truck.id, user_id=tec.id))
    hay = make_item(A, code="CAB-001", name="Cable con stock")
    nohay = make_item(A, code="CAB-002", name="Fuente sin stock")
    poco = make_item(A, code="CAB-003", name="Conector con poco")
    baja = make_item(A, code="CAB-004", name="Item dado de baja", is_active=False)
    A.upsert_stock(hay.id, jaula.id, 10)
    A.upsert_stock(poco.id, jaula.id, 2)
    A.db.session.commit()
    return {"jaula": jaula, "truck": truck, "tec": tec, "hay": hay,
            "nohay": nohay, "poco": poco, "baja": baja}


def _cli(A, user):
    c = A.app.test_client()
    login(c, user, "admin123" if user == "admin" else "pass1234")
    return c


def _pedir(c, esc, lineas):
    return c.post("/solicitudes-repuestos/new", data={
        "dest_location_id": str(esc["truck"].id),
        "item_id[]": [str(it.id) for it, _ in lineas],
        "qty[]": [str(q) for _, q in lineas],
    }, follow_redirects=True)


def _stock(A, item_id, loc_id):
    row = A.Stock.query.filter_by(item_id=item_id, location_id=loc_id).first()
    return row.quantity if row else 0


# ==========================================================================
# El técnico pide lo que no hay
# ==========================================================================

def test_tecnico_pide_un_item_sin_stock_en_jaula(A, esc):
    movs = A.Movement.query.count()
    r = _pedir(_cli(A, "tec"), esc, [(esc["nohay"], 3)])
    html = r.get_data(as_text=True)

    rr = A.RepairRequest.query.one()
    assert rr.status == "PENDIENTE"
    assert [(l.item_id, l.qty) for l in rr.lines] == [(esc["nohay"].id, 3)]
    assert "Solicitud de repuestos creada" in html
    assert "Sin stock en la Jaula" in html and "CAB-002" in html
    # Pedir no toca stock ni movimientos.
    assert A.Movement.query.count() == movs
    assert _stock(A, esc["nohay"].id, esc["truck"].id) == 0


def test_con_stock_no_avisa_nada_nuevo(A, esc):
    html = _pedir(_cli(A, "tec"), esc, [(esc["hay"], 1)]).get_data(as_text=True)
    assert "Solicitud de repuestos creada" in html
    assert "Sin stock en la Jaula" not in html


def test_mezcla_con_y_sin_stock_en_una_solicitud(A, esc):
    _pedir(_cli(A, "tec"), esc, [(esc["hay"], 1), (esc["nohay"], 2)])
    rr = A.RepairRequest.query.one()
    assert sorted(l.item_id for l in rr.lines) == sorted([esc["hay"].id, esc["nohay"].id])


def test_item_inactivo_sigue_rechazado(A, esc):
    html = _pedir(_cli(A, "tec"), esc, [(esc["baja"], 1)]).get_data(as_text=True)
    assert "no existe o está dado de baja" in html
    assert A.RepairRequest.query.count() == 0


def test_selector_del_tecnico_ofrece_todo_el_catalogo_activo(A, esc):
    html = _cli(A, "tec").get("/solicitudes-repuestos").get_data(as_text=True)
    assert "CAB-001 - Cable con stock" in html
    assert "CAB-002 - Fuente sin stock (sin stock en Jaula)" in html
    # Con poco stock no se marca: al técnico no se le dice cuánto hay.
    assert "CAB-003 - Conector con poco (sin stock" not in html
    assert "CAB-003 - Conector con poco" in html
    # Los dados de baja no se ofrecen.
    assert "CAB-004" not in html


def test_otro_tecnico_no_ve_la_solicitud(A, esc):
    make_user(A, "tec2", "TECNICO")
    _pedir(_cli(A, "tec"), esc, [(esc["nohay"], 1)])
    rr = A.RepairRequest.query.one()
    r = _cli(A, "tec2").get(f"/solicitudes-repuestos/{rr.id}", follow_redirects=True)
    assert "No tenés acceso a esa solicitud" in r.get_data(as_text=True)


# ==========================================================================
# Recordatorio de compra
# ==========================================================================

def test_alertas_muestra_lo_que_falta_comprar(A, esc):
    _pedir(_cli(A, "tec"), esc, [(esc["nohay"], 3), (esc["hay"], 1)])
    rr = A.RepairRequest.query.one()
    html = _cli(A, "admin").get("/stock-alerts").get_data(as_text=True)
    assert "Repuestos pedidos sin stock en la Jaula" in html
    assert "Fuente sin stock" in html
    assert rr.number in html
    # Lo que la Jaula sí cubre no figura.
    bloque = html.split("Repuestos pedidos sin stock en la Jaula")[1].split("</table>")[0]
    assert "Cable con stock" not in bloque


def test_los_pedidos_se_suman_entre_solicitudes(A, esc):
    # 2 en la Jaula, dos pedidos de 2: cada uno entra, juntos faltan 2.
    c = _cli(A, "tec")
    _pedir(c, esc, [(esc["poco"], 2)])
    _pedir(c, esc, [(esc["poco"], 2)])
    with A.app.test_request_context():
        faltas = A.repair_request_shortages()
    assert len(faltas) == 1
    e = faltas[0]
    assert (e["item"].id, e["pedido"], e["en_jaula"], e["faltante"]) == (esc["poco"].id, 4, 2, 2)
    assert len(e["requests"]) == 2


def test_sin_faltantes_la_pantalla_de_alertas_queda_como_antes(A, esc):
    _pedir(_cli(A, "tec"), esc, [(esc["hay"], 1)])
    html = _cli(A, "admin").get("/stock-alerts").get_data(as_text=True)
    assert "Repuestos pedidos sin stock" not in html


def test_rechazar_la_solicitud_saca_el_recordatorio(A, esc):
    _pedir(_cli(A, "tec"), esc, [(esc["nohay"], 1)])
    rr = A.RepairRequest.query.one()
    adm = _cli(A, "admin")
    adm.post(f"/solicitudes-repuestos/{rr.id}/rechazar", data={"close_reason": "No se compra"})
    html = adm.get("/stock-alerts").get_data(as_text=True)
    assert "Repuestos pedidos sin stock" not in html


def test_entra_la_compra_y_el_recordatorio_desaparece(A, esc):
    _pedir(_cli(A, "tec"), esc, [(esc["nohay"], 2)])
    A.upsert_stock(esc["nohay"].id, esc["jaula"].id, 2)
    A.db.session.commit()
    html = _cli(A, "admin").get("/stock-alerts").get_data(as_text=True)
    assert "Repuestos pedidos sin stock" not in html


def test_lista_marca_a_comprar_solo_para_admin(A, esc):
    _pedir(_cli(A, "tec"), esc, [(esc["nohay"], 1)])
    assert "A comprar" in _cli(A, "admin").get("/solicitudes-repuestos").get_data(as_text=True)
    assert "A comprar" in _cli(A, "sup").get("/solicitudes-repuestos").get_data(as_text=True)
    assert "A comprar" not in _cli(A, "tec").get("/solicitudes-repuestos").get_data(as_text=True)


def test_detalle_dice_cuanto_falta_por_linea(A, esc):
    _pedir(_cli(A, "tec"), esc, [(esc["poco"], 5)])
    rr = A.RepairRequest.query.one()
    html = _cli(A, "admin").get(f"/solicitudes-repuestos/{rr.id}").get_data(as_text=True)
    assert "En Jaula" in html
    assert "faltan 3" in html
    # El técnico no ve cuánto hay en la Jaula.
    html_tec = _cli(A, "tec").get(f"/solicitudes-repuestos/{rr.id}").get_data(as_text=True)
    assert "En Jaula" not in html_tec and "faltan" not in html_tec


# ==========================================================================
# Entregar sigue exigiendo stock real
# ==========================================================================

def test_no_se_puede_entregar_lo_que_no_hay(A, esc):
    _pedir(_cli(A, "tec"), esc, [(esc["nohay"], 1)])
    rr = A.RepairRequest.query.one()
    ln = rr.lines[0]
    movs = A.Movement.query.count()
    r = _cli(A, "admin").post(f"/solicitudes-repuestos/{rr.id}/cerrar", data={
        f"qty_entregada_{ln.id}": "1",
    }, follow_redirects=True)
    assert "no hay tanto en Jaula" in r.get_data(as_text=True)
    assert A.RepairRequest.query.get(rr.id).status == "PENDIENTE"
    assert A.Movement.query.count() == movs
    assert _stock(A, esc["nohay"].id, esc["truck"].id) == 0


def test_cuando_llega_la_compra_se_entrega_como_siempre(A, esc):
    _pedir(_cli(A, "tec"), esc, [(esc["nohay"], 2)])
    rr = A.RepairRequest.query.one()
    ln = rr.lines[0]
    A.upsert_stock(esc["nohay"].id, esc["jaula"].id, 2)
    A.db.session.commit()
    _cli(A, "admin").post(f"/solicitudes-repuestos/{rr.id}/cerrar", data={
        f"qty_entregada_{ln.id}": "2",
    }, follow_redirects=True)
    assert A.RepairRequest.query.get(rr.id).status == "CERRADA"
    assert _stock(A, esc["nohay"].id, esc["truck"].id) == 2
    assert _stock(A, esc["nohay"].id, esc["jaula"].id) == 0
