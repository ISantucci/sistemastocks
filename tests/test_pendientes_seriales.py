"""Cierre de pendientes y mesa de reparaciones con ítems serializados.

Dos agujeros que se tapan juntos, porque son el mismo circuito:

1. **El pendiente no cerraba.** Se entrega un repuesto bueno, el técnico lo
   instala (consumiéndolo de su camioneta) y trae el viejo SACADO DEL EQUIPO.
   Ese que vuelve nunca estuvo en el stock de la camioneta, y el cierre lo
   descontaba igual: "Stock insuficiente". Ahora se elige de dónde vuelve.

2. **Los serializados no se podían cerrar NUNCA.** La pantalla los rechazaba de
   plano y es el único lugar que cierra un pendiente: quedaban abiertos para
   siempre. Lo mismo en la mesa de reparaciones, que no sabía resolverlos.

Las pruebas cubren sobre todo lo que NO tiene que pasar: que un rechazo no mueva
stock ni deje unidades a medias, que un serial que ya está en stock no entre dos
veces, y que el comportamiento anterior siga siendo el default.
"""
import pytest
from conftest import make_user, make_item, make_category, make_location, login


@pytest.fixture()
def esc(A):
    """Jaula + camioneta de un técnico, con un ítem común y uno serializado."""
    A.app.config["WTF_CSRF_ENABLED"] = False
    cat = make_category(A, "Placas", "PLA")
    tec = make_user(A, "tec", "TECNICO")

    jaula = make_location(A, A.LOCATION_JAULA_TNG)
    truck = make_location(A, "Camioneta Tec", is_truck=True)
    make_location(A, A.LOCATION_RECUPERADO, is_external=True)
    make_location(A, A.LOCATION_EN_REPARACION)
    A.db.session.add(A.LocationResponsible(location_id=truck.id, user_id=tec.id))

    placa = make_item(A, code="PLA-001", name="Placa madre", category=cat)
    cam = make_item(A, code="PLA-900", name="Camara serializada", category=cat)
    cam.serialized = True
    A.db.session.commit()
    return {"jaula": jaula, "truck": truck, "placa": placa, "cam": cam, "tec": tec}


def _admin(A):
    c = A.app.test_client()
    login(c, "admin", "admin123")
    return c


def _unidad(A, item, serial, loc, status=None):
    u = A.ItemUnit(item_id=item.id, serial=serial,
                   status=status or A.UNIT_EN_STOCK,
                   location_id=loc.id if loc is not None else None)
    A.db.session.add(u)
    A.db.session.commit()
    return u


def _pendiente(A, esc, item, qty=1):
    """Entrega Jaula -> camioneta con su pendiente, armada directamente.

    Se arma por ORM y no por HTTP a propósito: lo que se prueba es el CIERRE, y
    así el escenario queda explícito (qué stock hay y qué unidades existen).
    """
    y, seq, number = A.next_movement_number()
    m = A.Movement(item_id=item.id, qty=qty,
                   from_location_id=esc["jaula"].id, to_location_id=esc["truck"].id,
                   user_id=1, observation="entrega", year=y, seq=seq, number=number)
    A.db.session.add(m)
    A.db.session.flush()
    p = A.PendingDelivery(movement_id=m.id, responsible_from_id=1,
                          responsible_to_id=esc["tec"].id, item_id=item.id,
                          return_qty=qty)
    A.db.session.add(p)
    A.db.session.commit()
    return p


def _stock(A, item_id, loc_id):
    row = A.Stock.query.filter_by(item_id=item_id, location_id=loc_id).first()
    return row.quantity if row else 0


def _estado(A, serial):
    u = A.ItemUnit.query.filter_by(serial=serial).first()
    return (u.status, u.location_id) if u else (None, None)


def _loc(A, nombre):
    return A.Location.query.filter_by(name=nombre).first()


# ==========================================================================
# Etapa 1 — de dónde vuelve (ítems comunes)
# ==========================================================================

def test_recuperado_del_campo_cierra_sin_stock_en_la_camioneta(A, esc):
    """EL CASO REAL: la placa buena ya se consumió, vuelve la de falla del equipo."""
    p = _pendiente(A, esc, esc["placa"])
    assert _stock(A, esc["placa"].id, esc["truck"].id) == 0  # ya la consumió

    c = _admin(A)
    c.post("/pending-deliveries", data={
        "pending_id": str(p.id), "return_action": "repair", "return_origin": "campo",
    })

    assert A.PendingDelivery.query.get(p.id).returned is True
    assert _stock(A, esc["placa"].id, esc["truck"].id) == 0        # no descontó
    assert _stock(A, esc["placa"].id, _loc(A, A.LOCATION_EN_REPARACION).id) == 1
    assert A.Repair.query.filter_by(pending_id=p.id).count() == 1
    obs = A.Movement.query.order_by(A.Movement.id.desc()).first().observation
    assert "Recuperado en campo" in obs   # el historial dice que no salió del stock


def test_el_default_sigue_descontando_de_la_camioneta(A, esc):
    """Sin elegir origen, el comportamiento es exactamente el de antes."""
    p = _pendiente(A, esc, esc["placa"])
    c = _admin(A)
    r = c.post("/pending-deliveries", data={
        "pending_id": str(p.id), "return_action": "return",
    }, follow_redirects=True)

    assert A.PendingDelivery.query.get(p.id).returned is False   # sin stock, no cierra
    assert "insuficiente" in r.get_data(as_text=True).lower()


def test_desde_el_stock_del_tecnico_descuenta_como_siempre(A, esc):
    p = _pendiente(A, esc, esc["placa"])
    A.upsert_stock(esc["placa"].id, esc["truck"].id, 1)
    A.db.session.commit()

    c = _admin(A)
    c.post("/pending-deliveries", data={
        "pending_id": str(p.id), "return_action": "return", "return_origin": "stock",
    })
    assert A.PendingDelivery.query.get(p.id).returned is True
    assert _stock(A, esc["placa"].id, esc["truck"].id) == 0
    assert _stock(A, esc["placa"].id, esc["jaula"].id) == 1


def test_origen_invalido_cae_al_default_no_rompe(A, esc):
    p = _pendiente(A, esc, esc["placa"])
    A.upsert_stock(esc["placa"].id, esc["truck"].id, 1)
    A.db.session.commit()
    c = _admin(A)
    c.post("/pending-deliveries", data={
        "pending_id": str(p.id), "return_action": "return", "return_origin": "cualquiera",
    })
    assert A.PendingDelivery.query.get(p.id).returned is True
    assert _stock(A, esc["placa"].id, esc["truck"].id) == 0   # usó el default: stock


# ==========================================================================
# Etapa 1 — seriales en el cierre del pendiente
# ==========================================================================

def test_serializado_desde_stock_elige_cual_vuelve(A, esc):
    p = _pendiente(A, esc, esc["cam"])
    u_a = _unidad(A, esc["cam"], "SN-A", esc["truck"])
    _unidad(A, esc["cam"], "SN-B", esc["truck"])
    A.upsert_stock(esc["cam"].id, esc["truck"].id, 2)
    A.db.session.commit()

    c = _admin(A)
    c.post("/pending-deliveries", data={
        "pending_id": str(p.id), "return_action": "return",
        "return_origin": "stock", "unit_id": str(u_a.id),
    })

    rep = _loc(A, A.LOCATION_JAULA_TNG)
    assert _estado(A, "SN-A") == (A.UNIT_EN_STOCK, rep.id)          # volvió esta
    assert _estado(A, "SN-B") == (A.UNIT_EN_STOCK, esc["truck"].id)  # esta no
    assert "SN-A" in A.Movement.query.order_by(A.Movement.id.desc()).first().observation


def test_serializado_desde_stock_con_uno_solo_sale_solo(A, esc):
    p = _pendiente(A, esc, esc["cam"])
    _unidad(A, esc["cam"], "SN-UNICA", esc["truck"])
    A.upsert_stock(esc["cam"].id, esc["truck"].id, 1)
    A.db.session.commit()

    c = _admin(A)
    c.post("/pending-deliveries", data={
        "pending_id": str(p.id), "return_action": "return", "return_origin": "stock",
    })
    assert A.PendingDelivery.query.get(p.id).returned is True
    assert "SN-UNICA" in A.Movement.query.order_by(A.Movement.id.desc()).first().observation


def test_serializado_desde_stock_sin_elegir_rechaza_y_no_mueve_nada(A, esc):
    p = _pendiente(A, esc, esc["cam"])
    _unidad(A, esc["cam"], "SN-A", esc["truck"])
    _unidad(A, esc["cam"], "SN-B", esc["truck"])
    A.upsert_stock(esc["cam"].id, esc["truck"].id, 2)
    A.db.session.commit()
    movs = A.Movement.query.count()

    c = _admin(A)
    c.post("/pending-deliveries", data={
        "pending_id": str(p.id), "return_action": "return", "return_origin": "stock",
    })
    assert A.PendingDelivery.query.get(p.id).returned is False
    assert A.Movement.query.count() == movs
    assert _stock(A, esc["cam"].id, esc["truck"].id) == 2
    assert _estado(A, "SN-A") == (A.UNIT_EN_STOCK, esc["truck"].id)


def test_serializado_desde_stock_con_serial_ajeno_rechaza(A, esc):
    """Un serial que está en la Jaula no puede salir de la camioneta."""
    p = _pendiente(A, esc, esc["cam"])
    _unidad(A, esc["cam"], "SN-A", esc["truck"])
    _unidad(A, esc["cam"], "SN-B", esc["truck"])
    ajeno = _unidad(A, esc["cam"], "SN-JAULA", esc["jaula"])
    A.upsert_stock(esc["cam"].id, esc["truck"].id, 2)
    A.db.session.commit()

    c = _admin(A)
    c.post("/pending-deliveries", data={
        "pending_id": str(p.id), "return_action": "return",
        "return_origin": "stock", "unit_id": str(ajeno.id),
    })
    assert A.PendingDelivery.query.get(p.id).returned is False
    assert _estado(A, "SN-JAULA") == (A.UNIT_EN_STOCK, esc["jaula"].id)


def test_serializado_del_campo_crea_la_unidad_y_no_descuenta(A, esc):
    p = _pendiente(A, esc, esc["cam"])
    c = _admin(A)
    c.post("/pending-deliveries", data={
        "pending_id": str(p.id), "return_action": "repair",
        "return_origin": "campo", "unit_serial": "SN-DEL-EQUIPO",
    })

    rep = _loc(A, A.LOCATION_EN_REPARACION)
    assert A.PendingDelivery.query.get(p.id).returned is True
    assert _estado(A, "SN-DEL-EQUIPO") == (A.UNIT_EN_STOCK, rep.id)
    assert _stock(A, esc["cam"].id, esc["truck"].id) == 0
    assert _stock(A, esc["cam"].id, rep.id) == 1


def test_serializado_del_campo_reactiva_la_unidad_que_habia_salido(A, esc):
    """Es la misma unidad física que vuelve, no un serial duplicado."""
    p = _pendiente(A, esc, esc["cam"])
    _unidad(A, esc["cam"], "SN-VIEJA", None, status=A.UNIT_ENTREGADO)
    unidades_antes = A.ItemUnit.query.filter_by(item_id=esc["cam"].id).count()

    c = _admin(A)
    c.post("/pending-deliveries", data={
        "pending_id": str(p.id), "return_action": "repair",
        "return_origin": "campo", "unit_serial": "SN-VIEJA",
    })

    rep = _loc(A, A.LOCATION_EN_REPARACION)
    assert A.PendingDelivery.query.get(p.id).returned is True
    assert _estado(A, "SN-VIEJA") == (A.UNIT_EN_STOCK, rep.id)
    assert A.ItemUnit.query.filter_by(item_id=esc["cam"].id).count() == unidades_antes


def test_serializado_del_campo_rechaza_un_serial_que_ya_esta_en_stock(A, esc):
    """Si ya está en stock, entrar de nuevo lo duplicaría físicamente."""
    p = _pendiente(A, esc, esc["cam"])
    _unidad(A, esc["cam"], "SN-EN-JAULA", esc["jaula"])
    A.upsert_stock(esc["cam"].id, esc["jaula"].id, 1)
    A.db.session.commit()
    movs = A.Movement.query.count()

    c = _admin(A)
    r = c.post("/pending-deliveries", data={
        "pending_id": str(p.id), "return_action": "repair",
        "return_origin": "campo", "unit_serial": "SN-EN-JAULA",
    }, follow_redirects=True)

    assert A.PendingDelivery.query.get(p.id).returned is False
    assert A.Movement.query.count() == movs
    assert _estado(A, "SN-EN-JAULA") == (A.UNIT_EN_STOCK, esc["jaula"].id)
    assert "ya esta en stock" in r.get_data(as_text=True).lower()


def test_serializado_del_campo_sin_serial_rechaza(A, esc):
    p = _pendiente(A, esc, esc["cam"])
    movs = A.Movement.query.count()
    c = _admin(A)
    c.post("/pending-deliveries", data={
        "pending_id": str(p.id), "return_action": "repair", "return_origin": "campo",
    })
    assert A.PendingDelivery.query.get(p.id).returned is False
    assert A.Movement.query.count() == movs
    assert A.ItemUnit.query.count() == 0


def test_serializado_a_descartes_deja_la_unidad_descartada_y_genera_scrap(A, esc):
    p = _pendiente(A, esc, esc["cam"])
    c = _admin(A)
    c.post("/pending-deliveries", data={
        "pending_id": str(p.id), "return_action": "scrap", "scrap_reason": "Roto",
        "return_origin": "campo", "unit_serial": "SN-ROTA",
    })
    assert _estado(A, "SN-ROTA") == (A.UNIT_DESCARTADO, None)
    assert A.Scrap.query.filter_by(item_id=esc["cam"].id).count() == 1


def test_el_tecnico_sigue_sin_poder_cerrar_pendientes(A, esc):
    p = _pendiente(A, esc, esc["cam"])
    c = A.app.test_client()
    login(c, "tec")
    c.post("/pending-deliveries", data={
        "pending_id": str(p.id), "return_action": "repair",
        "return_origin": "campo", "unit_serial": "SN-X",
    })
    assert A.PendingDelivery.query.get(p.id).returned is False
    assert A.ItemUnit.query.count() == 0


# ==========================================================================
# Etapa 2 — mesa de reparaciones con seriales
# ==========================================================================

@pytest.fixture()
def mesa(A, esc):
    """Una cámara serializada ya en la mesa de reparación, con su Repair."""
    rep = _loc(A, A.LOCATION_EN_REPARACION)
    _unidad(A, esc["cam"], "SN-MESA", rep)
    A.upsert_stock(esc["cam"].id, rep.id, 1)
    r = A.Repair(item_id=esc["cam"].id, quantity=1, status="EN_REPARACION",
                 source_location_id=esc["truck"].id, created_by_user_id=1)
    A.db.session.add(r)
    A.db.session.commit()
    return {"repair": r, "rep_loc": rep}


def test_mesa_resuelve_serializado_reparado(A, esc, mesa):
    c = _admin(A)
    c.post("/reparaciones", data={
        "repair_id": str(mesa["repair"].id), "repair_action": "reparado",
    })
    jaula = _loc(A, A.LOCATION_JAULA_TNG)
    assert A.Repair.query.get(mesa["repair"].id).status == "REPARADO"
    assert _estado(A, "SN-MESA") == (A.UNIT_EN_STOCK, jaula.id)
    assert _stock(A, esc["cam"].id, jaula.id) == 1
    assert _stock(A, esc["cam"].id, mesa["rep_loc"].id) == 0
    assert "SN-MESA" in A.Movement.query.order_by(A.Movement.id.desc()).first().observation


def test_mesa_resuelve_serializado_descartado(A, esc, mesa):
    c = _admin(A)
    c.post("/reparaciones", data={
        "repair_id": str(mesa["repair"].id), "repair_action": "descartado",
        "scrap_reason": "Irreparable",
    })
    assert A.Repair.query.get(mesa["repair"].id).status == "DESCARTADO"
    assert _estado(A, "SN-MESA") == (A.UNIT_DESCARTADO, None)
    assert A.Scrap.query.filter_by(item_id=esc["cam"].id, source="REPARACION").count() == 1


def test_mesa_con_varias_unidades_del_mismo_item_hay_que_elegir(A, esc, mesa):
    """Dos unidades en la mesa y una reparación de 1: no se puede adivinar."""
    _unidad(A, esc["cam"], "SN-MESA-2", mesa["rep_loc"])
    A.upsert_stock(esc["cam"].id, mesa["rep_loc"].id, 1)
    A.db.session.commit()

    c = _admin(A)
    c.post("/reparaciones", data={
        "repair_id": str(mesa["repair"].id), "repair_action": "reparado",
    })
    assert A.Repair.query.get(mesa["repair"].id).status == "EN_REPARACION"
    assert _estado(A, "SN-MESA") == (A.UNIT_EN_STOCK, mesa["rep_loc"].id)

    elegida = A.ItemUnit.query.filter_by(serial="SN-MESA-2").first()
    c.post("/reparaciones", data={
        "repair_id": str(mesa["repair"].id), "repair_action": "reparado",
        "unit_id": str(elegida.id),
    })
    jaula = _loc(A, A.LOCATION_JAULA_TNG)
    assert A.Repair.query.get(mesa["repair"].id).status == "REPARADO"
    assert _estado(A, "SN-MESA-2") == (A.UNIT_EN_STOCK, jaula.id)
    assert _estado(A, "SN-MESA") == (A.UNIT_EN_STOCK, mesa["rep_loc"].id)


def test_mesa_circuito_proveedor_ida_y_vuelta_con_el_mismo_serial(A, esc, mesa):
    prov = make_location(A, A.LOCATION_PROVEEDOR, is_external=True)
    s = A.Supplier(contact_name="Repuestos SA", is_active=True)
    A.db.session.add(s)
    A.db.session.commit()

    c = _admin(A)
    c.post("/reparaciones", data={
        "repair_id": str(mesa["repair"].id), "repair_action": "enviar_proveedor",
        "supplier_id": str(s.id),
    })
    assert A.Repair.query.get(mesa["repair"].id).status == "EN_PROVEEDOR"
    assert _estado(A, "SN-MESA") == (A.UNIT_ENTREGADO, None)   # salió del sistema
    assert _stock(A, esc["cam"].id, mesa["rep_loc"].id) == 0
    assert A.Remito.query.count() == 1

    c.post("/reparaciones", data={
        "repair_id": str(mesa["repair"].id), "repair_action": "reparado_proveedor",
        "supplier_id": str(s.id), "unit_serial": "SN-MESA",
    })
    jaula = _loc(A, A.LOCATION_JAULA_TNG)
    assert A.Repair.query.get(mesa["repair"].id).status == "REPARADO"
    assert _estado(A, "SN-MESA") == (A.UNIT_EN_STOCK, jaula.id)  # la MISMA unidad
    assert A.ItemUnit.query.filter_by(serial="SN-MESA").count() == 1
    assert A.Remito.query.count() == 2


def test_mesa_sigue_funcionando_para_items_comunes(A, esc):
    """Regresión: lo que ya andaba no cambió."""
    rep = _loc(A, A.LOCATION_EN_REPARACION)
    A.upsert_stock(esc["placa"].id, rep.id, 2)
    r = A.Repair(item_id=esc["placa"].id, quantity=2, status="EN_REPARACION",
                 source_location_id=esc["truck"].id, created_by_user_id=1)
    A.db.session.add(r)
    A.db.session.commit()

    c = _admin(A)
    c.post("/reparaciones", data={"repair_id": str(r.id), "repair_action": "reparado"})
    jaula = _loc(A, A.LOCATION_JAULA_TNG)
    assert A.Repair.query.get(r.id).status == "REPARADO"
    assert _stock(A, esc["placa"].id, jaula.id) == 2


def test_movimiento_manual_a_reparacion_de_serializado_entra_a_la_mesa(A, esc):
    """Antes el Repair no se creaba para serializados: la mesa no los veía."""
    u = _unidad(A, esc["cam"], "SN-MANUAL", esc["jaula"])
    A.upsert_stock(esc["cam"].id, esc["jaula"].id, 1)
    A.db.session.commit()

    c = _admin(A)
    c.post("/movements", data={
        "item_id": str(esc["cam"].id), "qty": "1",
        "from_location_id": str(esc["jaula"].id),
        "to_location_id": str(_loc(A, A.LOCATION_EN_REPARACION).id),
        "unit_id": str(u.id),
    })
    assert A.Repair.query.filter_by(item_id=esc["cam"].id, status="EN_REPARACION").count() == 1


# ==========================================================================
# Guardas de las pantallas: si el selector no se dibuja, no hay forma de elegir
# ==========================================================================

def test_la_pantalla_de_pendientes_dibuja_origen_y_seriales(A, esc):
    p = _pendiente(A, esc, esc["cam"])
    _unidad(A, esc["cam"], "SN-A", esc["truck"])
    _unidad(A, esc["cam"], "SN-B", esc["truck"])
    A.upsert_stock(esc["cam"].id, esc["truck"].id, 2)
    A.db.session.commit()

    html = _admin(A).get("/pending-deliveries").get_data(as_text=True)
    assert 'name="return_origin"' in html
    assert 'value="campo"' in html
    assert 'name="unit_id"' in html and "SN-A" in html and "SN-B" in html
    assert 'name="unit_serial"' in html          # el campo para cargar el que entra


def test_la_mesa_de_reparaciones_dibuja_el_selector_de_serial(A, esc, mesa):
    _unidad(A, esc["cam"], "SN-MESA-2", mesa["rep_loc"])
    A.upsert_stock(esc["cam"].id, mesa["rep_loc"].id, 1)
    A.db.session.commit()

    html = _admin(A).get("/reparaciones").get_data(as_text=True)
    assert 'name="unit_id"' in html
    assert "SN-MESA" in html and "SN-MESA-2" in html


def test_pantallas_sin_serializados_no_dibujan_el_selector(A, esc):
    """Regresión de ruido: un ítem común no muestra campos de serial."""
    _pendiente(A, esc, esc["placa"])
    html = _admin(A).get("/pending-deliveries").get_data(as_text=True)
    assert 'name="return_origin"' in html        # el origen sí, es para todos
    assert 'name="unit_serial"' not in html      # los seriales no
