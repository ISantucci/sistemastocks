"""Mesa de reparaciones por serial (2026-10-01).

Hasta acá `repairs` no sabía qué unidad era cada reparación: la fila decía
"CAM-001 x 1" y el serial se elegía al resolver entre TODAS las unidades de ese
ítem que estuvieran en la mesa. Con dos cámaras iguales había que adivinar, al
volver del proveedor se tipeaba el serial de memoria, y un egreso serializado
por reparación salía del sistema sin quedar en la mesa.

Ahora cada reparación nueva queda vinculada a su unidad (tabla repair_units):

- serializado = UNA reparación por unidad, en los tres lugares donde nace
  (Movimientos hacia "En reparación", cierre de pendiente "A reparación" y
  egreso con motivo Reparación);
- al resolver sale ESA unidad, sin elegir;
- al volver del proveedor vuelve ESA unidad, sin tipear, salvo que el proveedor
  devuelva otra (reemplazo), que se carga y queda registrada;
- las reparaciones anteriores (sin vínculo) siguen como antes.

Como en el resto de las suites de seriales, lo importante es lo que NO tiene que
pasar: que un rechazo no mueva nada y que lo viejo siga andando.
"""
import pytest
from conftest import make_user, make_item, make_category, make_location, login


@pytest.fixture()
def esc(A):
    A.app.config["WTF_CSRF_ENABLED"] = False
    cat = make_category(A, "Camaras", "CAM")
    tec = make_user(A, "tec", "TECNICO")
    jaula = make_location(A, A.LOCATION_JAULA_TNG)
    truck = make_location(A, "Camioneta Tec", is_truck=True)
    prov = make_location(A, A.LOCATION_PROVEEDOR, is_external=True)
    make_location(A, A.LOCATION_RECUPERADO, is_external=True)
    make_location(A, A.LOCATION_DESCARTES, is_external=True)
    rep = make_location(A, A.LOCATION_EN_REPARACION)
    A.db.session.add(A.LocationResponsible(location_id=truck.id, user_id=tec.id))
    cam = make_item(A, code="CAM-001", name="Camara domo", category=cat)
    cam.serialized = True
    placa = make_item(A, code="CAM-100", name="Fuente comun", category=cat)
    s = A.Supplier(contact_name="Repara SA", is_active=True)
    A.db.session.add(s)
    A.db.session.commit()
    return {"jaula": jaula, "truck": truck, "prov": prov, "rep": rep,
            "cam": cam, "placa": placa, "tec": tec, "sup": s}


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


def _stock(A, item_id, loc_id):
    row = A.Stock.query.filter_by(item_id=item_id, location_id=loc_id).first()
    return row.quantity if row else 0


def _estado(A, serial):
    u = A.ItemUnit.query.filter_by(serial=serial).first()
    return (u.status, u.location_id) if u else (None, None)


def _serials(A, r):
    return sorted(u.serial for u in A.repair_units_of(A.Repair.query.get(r.id)))


def _a_la_mesa(A, esc, serials):
    """Mueve por Movimientos esas cámaras de la Jaula a la mesa."""
    units = [_unidad(A, esc["cam"], s, esc["jaula"]) for s in serials]
    A.upsert_stock(esc["cam"].id, esc["jaula"].id, len(units))
    A.db.session.commit()
    _admin(A).post("/movements", data={
        "item_id": str(esc["cam"].id), "qty": str(len(units)),
        "from_location_id": str(esc["jaula"].id),
        "to_location_id": str(esc["rep"].id),
        "unit_id": [str(u.id) for u in units],
    })
    return (A.Repair.query.filter_by(item_id=esc["cam"].id, status="EN_REPARACION")
            .order_by(A.Repair.id).all())


# ==========================================================================
# Dónde nace: una reparación por unidad, con su serial
# ==========================================================================

def test_movimiento_a_la_mesa_crea_una_reparacion_por_unidad(A, esc):
    reps = _a_la_mesa(A, esc, ["SN-1", "SN-2"])
    assert len(reps) == 2
    assert all(r.quantity == 1 for r in reps)
    assert sorted(s for r in reps for s in _serials(A, r)) == ["SN-1", "SN-2"]
    assert _stock(A, esc["cam"].id, esc["rep"].id) == 2


def test_movimiento_a_la_mesa_de_un_comun_sigue_siendo_una_por_cantidad(A, esc):
    A.upsert_stock(esc["placa"].id, esc["jaula"].id, 3)
    A.db.session.commit()
    _admin(A).post("/movements", data={
        "item_id": str(esc["placa"].id), "qty": "3",
        "from_location_id": str(esc["jaula"].id),
        "to_location_id": str(esc["rep"].id),
    })
    reps = A.Repair.query.filter_by(item_id=esc["placa"].id).all()
    assert len(reps) == 1 and reps[0].quantity == 3
    assert A.RepairUnit.query.count() == 0


def test_serializado_sin_seriales_cargados_sigue_entrando_por_cantidad(A, esc):
    """Sin unidades en el origen no hay qué vincular: una por la cantidad."""
    A.upsert_stock(esc["cam"].id, esc["jaula"].id, 2)
    A.db.session.commit()
    _admin(A).post("/movements", data={
        "item_id": str(esc["cam"].id), "qty": "2",
        "from_location_id": str(esc["jaula"].id),
        "to_location_id": str(esc["rep"].id),
    })
    reps = A.Repair.query.filter_by(item_id=esc["cam"].id).all()
    assert len(reps) == 1 and reps[0].quantity == 2
    assert A.RepairUnit.query.count() == 0


def test_cierre_de_pendiente_a_reparacion_vincula_el_serial_que_vuelve(A, esc):
    y, seq, number = A.next_movement_number()
    m = A.Movement(item_id=esc["cam"].id, qty=1,
                   from_location_id=esc["jaula"].id, to_location_id=esc["truck"].id,
                   user_id=1, observation="entrega", year=y, seq=seq, number=number)
    A.db.session.add(m)
    A.db.session.flush()
    p = A.PendingDelivery(movement_id=m.id, responsible_from_id=1,
                          responsible_to_id=esc["tec"].id, item_id=esc["cam"].id,
                          return_qty=1)
    A.db.session.add(p)
    A.db.session.commit()

    _admin(A).post("/pending-deliveries", data={
        "pending_id": str(p.id), "return_action": "repair",
        "return_origin": "campo", "unit_serial": "SN-CAMPO",
    })
    r = A.Repair.query.filter_by(item_id=esc["cam"].id).one()
    assert r.status == "EN_REPARACION" and r.pending_id == p.id
    assert _serials(A, r) == ["SN-CAMPO"]
    assert _estado(A, "SN-CAMPO") == (A.UNIT_EN_STOCK, esc["rep"].id)


def test_egreso_serializado_por_reparacion_queda_en_proveedor_por_unidad(A, esc):
    _unidad(A, esc["cam"], "SN-E1", esc["jaula"])
    _unidad(A, esc["cam"], "SN-E2", esc["jaula"])
    A.upsert_stock(esc["cam"].id, esc["jaula"].id, 2)
    A.db.session.commit()

    _admin(A).post("/ingresos-egresos", data={
        "tipo": "EGRESO", "motivo": "REPARACION", "supplier_id": str(esc["sup"].id),
        "item_id[]": [str(esc["cam"].id)], "qty[]": ["2"],
        "line_serials[]": ["SN-E1\nSN-E2"],
    })
    reps = A.Repair.query.filter_by(item_id=esc["cam"].id).order_by(A.Repair.id).all()
    assert [r.status for r in reps] == ["EN_PROVEEDOR", "EN_PROVEEDOR"]
    assert [r.quantity for r in reps] == [1, 1]
    assert sorted(s for r in reps for s in _serials(A, r)) == ["SN-E1", "SN-E2"]
    assert _estado(A, "SN-E1") == (A.UNIT_ENTREGADO, None)


# ==========================================================================
# En la mesa: sale ESA unidad
# ==========================================================================

def test_con_dos_iguales_en_la_mesa_cada_reparacion_saca_la_suya(A, esc):
    """El caso que antes obligaba a adivinar: ahora no se elige nada."""
    r1, r2 = _a_la_mesa(A, esc, ["SN-1", "SN-2"])
    suya = _serials(A, r2)[0]
    otra = _serials(A, r1)[0]

    _admin(A).post("/reparaciones", data={
        "repair_id": str(r2.id), "repair_action": "reparado",
    })
    assert A.Repair.query.get(r2.id).status == "REPARADO"
    assert _estado(A, suya) == (A.UNIT_EN_STOCK, esc["jaula"].id)
    assert _estado(A, otra) == (A.UNIT_EN_STOCK, esc["rep"].id)   # no se tocó
    obs = A.Movement.query.order_by(A.Movement.id.desc()).first().observation
    assert suya in obs and otra not in obs


def test_descartar_desde_la_mesa_descarta_su_unidad(A, esc):
    r1, r2 = _a_la_mesa(A, esc, ["SN-1", "SN-2"])
    suya = _serials(A, r1)[0]
    _admin(A).post("/reparaciones", data={
        "repair_id": str(r1.id), "repair_action": "descartado",
        "scrap_reason": "Irreparable",
    })
    assert A.Repair.query.get(r1.id).status == "DESCARTADO"
    assert _estado(A, suya) == (A.UNIT_DESCARTADO, None)
    assert A.Scrap.query.filter_by(item_id=esc["cam"].id, source="REPARACION").count() == 1


def test_si_su_unidad_ya_no_esta_en_la_mesa_se_elige_y_no_se_traba(A, esc):
    """Alguien la sacó a mano: se cae a elegir entre las que están."""
    r1, r2 = _a_la_mesa(A, esc, ["SN-1", "SN-2"])
    suya = A.repair_units_of(A.Repair.query.get(r1.id))[0]
    suya.location_id = esc["truck"].id
    A.upsert_stock(esc["cam"].id, esc["rep"].id, -1)
    A.upsert_stock(esc["cam"].id, esc["truck"].id, 1)
    _unidad(A, esc["cam"], "SN-3", esc["rep"])
    A.upsert_stock(esc["cam"].id, esc["rep"].id, 1)
    A.db.session.commit()

    _unidad(A, esc["cam"], "SN-4", esc["rep"])
    A.upsert_stock(esc["cam"].id, esc["rep"].id, 1)
    A.db.session.commit()

    c = _admin(A)
    # En la mesa hay SN-2 (de la otra reparación), SN-3 y SN-4: entre las dos
    # libres hay que elegir.
    c.post("/reparaciones", data={"repair_id": str(r1.id), "repair_action": "reparado"})
    assert A.Repair.query.get(r1.id).status == "EN_REPARACION"

    sn3 = A.ItemUnit.query.filter_by(serial="SN-3").first()
    c.post("/reparaciones", data={
        "repair_id": str(r1.id), "repair_action": "reparado", "unit_id": str(sn3.id),
    })
    assert A.Repair.query.get(r1.id).status == "REPARADO"
    assert _estado(A, "SN-3") == (A.UNIT_EN_STOCK, esc["jaula"].id)
    assert _estado(A, "SN-2") == (A.UNIT_EN_STOCK, esc["rep"].id)   # la de la otra


def test_reparacion_vieja_sin_vinculo_se_vincula_al_resolverla(A, esc):
    """Las anteriores al cambio: se elige como siempre y queda registrado."""
    _unidad(A, esc["cam"], "SN-V1", esc["rep"])
    _unidad(A, esc["cam"], "SN-V2", esc["rep"])
    A.upsert_stock(esc["cam"].id, esc["rep"].id, 2)
    r = A.Repair(item_id=esc["cam"].id, quantity=1, status="EN_REPARACION",
                 source_location_id=esc["truck"].id, created_by_user_id=1)
    A.db.session.add(r)
    A.db.session.commit()

    elegida = A.ItemUnit.query.filter_by(serial="SN-V2").first()
    _admin(A).post("/reparaciones", data={
        "repair_id": str(r.id), "repair_action": "enviar_proveedor",
        "supplier_id": str(esc["sup"].id), "unit_id": str(elegida.id),
    })
    assert A.Repair.query.get(r.id).status == "EN_PROVEEDOR"
    assert _serials(A, r) == ["SN-V2"]


# ==========================================================================
# Ida y vuelta del proveedor
# ==========================================================================

def _al_proveedor(A, esc, serial="SN-P"):
    (r,) = _a_la_mesa(A, esc, [serial])
    _admin(A).post("/reparaciones", data={
        "repair_id": str(r.id), "repair_action": "enviar_proveedor",
        "supplier_id": str(esc["sup"].id),
    })
    assert A.Repair.query.get(r.id).status == "EN_PROVEEDOR"
    assert _estado(A, serial) == (A.UNIT_ENTREGADO, None)
    return r


def test_vuelve_del_proveedor_la_misma_unidad_sin_tipear(A, esc):
    r = _al_proveedor(A, esc)
    _admin(A).post("/reparaciones", data={
        "repair_id": str(r.id), "repair_action": "reparado_proveedor",
        "supplier_id": str(esc["sup"].id),
    })
    assert A.Repair.query.get(r.id).status == "REPARADO"
    assert _estado(A, "SN-P") == (A.UNIT_EN_STOCK, esc["jaula"].id)
    assert A.ItemUnit.query.filter_by(serial="SN-P").count() == 1
    assert _stock(A, esc["cam"].id, esc["jaula"].id) == 1


def test_el_proveedor_devuelve_otra_unidad_reemplazo(A, esc):
    r = _al_proveedor(A, esc)
    _admin(A).post("/reparaciones", data={
        "repair_id": str(r.id), "repair_action": "reparado_proveedor",
        "supplier_id": str(esc["sup"].id),
        "return_mode": "other", "unit_serial": "SN-NUEVA",
    })
    assert A.Repair.query.get(r.id).status == "REPARADO"
    assert _estado(A, "SN-NUEVA") == (A.UNIT_EN_STOCK, esc["jaula"].id)
    assert _estado(A, "SN-P") == (A.UNIT_ENTREGADO, None)   # nunca volvió
    reemp = [u.serial for u in A.repair_units_of(A.Repair.query.get(r.id),
                                                  kind=A.REPAIR_UNIT_REEMPLAZO)]
    assert reemp == ["SN-NUEVA"]
    obs = A.Movement.query.order_by(A.Movement.id.desc()).first().observation
    assert "reemplaza a SN-P" in obs and "SN-NUEVA" in obs


def test_otro_serial_igual_al_enviado_no_es_reemplazo(A, esc):
    r = _al_proveedor(A, esc)
    _admin(A).post("/reparaciones", data={
        "repair_id": str(r.id), "repair_action": "reparado_proveedor",
        "supplier_id": str(esc["sup"].id),
        "return_mode": "other", "unit_serial": "SN-P",
    })
    assert _estado(A, "SN-P") == (A.UNIT_EN_STOCK, esc["jaula"].id)
    assert A.repair_units_of(A.Repair.query.get(r.id), kind=A.REPAIR_UNIT_REEMPLAZO) == []


def test_si_la_unidad_ya_figura_en_stock_no_vuelve_y_no_mueve_nada(A, esc):
    r = _al_proveedor(A, esc)
    u = A.ItemUnit.query.filter_by(serial="SN-P").first()
    u.status = A.UNIT_EN_STOCK            # alguien la dio de alta por otro lado
    u.location_id = esc["truck"].id
    A.db.session.commit()
    movs = A.Movement.query.count()

    _admin(A).post("/reparaciones", data={
        "repair_id": str(r.id), "repair_action": "reparado_proveedor",
        "supplier_id": str(esc["sup"].id),
    })
    assert A.Repair.query.get(r.id).status == "EN_PROVEEDOR"
    assert A.Movement.query.count() == movs
    assert _estado(A, "SN-P") == (A.UNIT_EN_STOCK, esc["truck"].id)


def test_reemplazo_con_serial_que_ya_esta_en_stock_se_rechaza(A, esc):
    r = _al_proveedor(A, esc)
    _unidad(A, esc["cam"], "SN-OCUPADA", esc["jaula"])
    movs = A.Movement.query.count()
    _admin(A).post("/reparaciones", data={
        "repair_id": str(r.id), "repair_action": "reparado_proveedor",
        "supplier_id": str(esc["sup"].id),
        "return_mode": "other", "unit_serial": "SN-OCUPADA",
    })
    assert A.Repair.query.get(r.id).status == "EN_PROVEEDOR"
    assert A.Movement.query.count() == movs


def test_egreso_por_reparacion_vuelve_por_la_mesa_con_su_serial(A, esc):
    """El circuito que antes no existía: Egreso -> En proveedor -> vuelve."""
    _unidad(A, esc["cam"], "SN-EG", esc["jaula"])
    A.upsert_stock(esc["cam"].id, esc["jaula"].id, 1)
    A.db.session.commit()
    c = _admin(A)
    c.post("/ingresos-egresos", data={
        "tipo": "EGRESO", "motivo": "REPARACION", "supplier_id": str(esc["sup"].id),
        "item_id[]": [str(esc["cam"].id)], "qty[]": ["1"], "line_serials[]": ["SN-EG"],
    })
    r = A.Repair.query.filter_by(item_id=esc["cam"].id).one()
    c.post("/reparaciones", data={
        "repair_id": str(r.id), "repair_action": "reparado_proveedor",
        "supplier_id": str(esc["sup"].id),
    })
    assert A.Repair.query.get(r.id).status == "REPARADO"
    assert _estado(A, "SN-EG") == (A.UNIT_EN_STOCK, esc["jaula"].id)
    assert _stock(A, esc["cam"].id, esc["jaula"].id) == 1


def test_reparacion_vieja_en_proveedor_sigue_pidiendo_el_serial(A, esc):
    _unidad(A, esc["cam"], "SN-OLD", None, status=A.UNIT_ENTREGADO)
    r = A.Repair(item_id=esc["cam"].id, quantity=1, status="EN_PROVEEDOR",
                 source_location_id=esc["jaula"].id, created_by_user_id=1)
    A.db.session.add(r)
    A.db.session.commit()
    c = _admin(A)
    c.post("/reparaciones", data={
        "repair_id": str(r.id), "repair_action": "reparado_proveedor",
        "supplier_id": str(esc["sup"].id),
    })
    assert A.Repair.query.get(r.id).status == "EN_PROVEEDOR"   # sin serial, no
    c.post("/reparaciones", data={
        "repair_id": str(r.id), "repair_action": "reparado_proveedor",
        "supplier_id": str(esc["sup"].id), "unit_serial": "SN-OLD",
    })
    assert A.Repair.query.get(r.id).status == "REPARADO"
    assert _estado(A, "SN-OLD") == (A.UNIT_EN_STOCK, esc["jaula"].id)


# ==========================================================================
# Permisos y pantalla
# ==========================================================================

def test_lector_no_puede_resolver(A, esc):
    (r,) = _a_la_mesa(A, esc, ["SN-L"])
    make_user(A, "lec", "LECTOR")
    c = A.app.test_client()
    login(c, "lec")
    c.post("/reparaciones", data={"repair_id": str(r.id), "repair_action": "reparado"})
    assert A.Repair.query.get(r.id).status == "EN_REPARACION"


def test_tecnico_no_entra_a_la_mesa(A, esc):
    (r,) = _a_la_mesa(A, esc, ["SN-T"])
    c = A.app.test_client()
    login(c, "tec")
    resp = c.post("/reparaciones", data={"repair_id": str(r.id), "repair_action": "reparado"})
    assert resp.status_code in (302, 403)
    assert A.Repair.query.get(r.id).status == "EN_REPARACION"


def test_la_mesa_muestra_el_serial_de_cada_fila_y_no_pide_elegir(A, esc):
    _a_la_mesa(A, esc, ["SN-1", "SN-2"])
    html = _admin(A).get("/reparaciones").get_data(as_text=True)
    assert ">Serial<" in html
    assert "Sale el de esta reparación: SN-1" in html
    assert "Sale el de esta reparación: SN-2" in html
    assert 'name="unit_id"' not in html


def test_en_proveedor_ofrece_el_mismo_o_reemplazo(A, esc):
    _al_proveedor(A, esc)
    html = _admin(A).get("/reparaciones").get_data(as_text=True)
    assert 'name="return_mode"' in html and 'value="other"' in html
    assert "El mismo: SN-P" in html


def test_historial_muestra_el_serial_y_el_reemplazo(A, esc):
    r = _al_proveedor(A, esc)
    _admin(A).post("/reparaciones", data={
        "repair_id": str(r.id), "repair_action": "reparado_proveedor",
        "supplier_id": str(esc["sup"].id),
        "return_mode": "other", "unit_serial": "SN-NUEVA",
    })
    html = _admin(A).get("/reparaciones").get_data(as_text=True)
    assert "SN-P &rarr; SN-NUEVA" in html or "SN-P → SN-NUEVA" in html
    assert "(reemplazo)" in html


def test_una_reparacion_vieja_no_puede_llevarse_la_unidad_de_otra(A, esc):
    """La vieja elige entre las de la mesa, pero no entre las que ya son de
    otra reparación: si se la llevara, la otra quedaría sin su unidad."""
    (r_nueva,) = _a_la_mesa(A, esc, ["SN-AJENA"])
    _unidad(A, esc["cam"], "SN-LIBRE-1", esc["rep"])
    _unidad(A, esc["cam"], "SN-LIBRE-2", esc["rep"])
    A.upsert_stock(esc["cam"].id, esc["rep"].id, 2)
    vieja = A.Repair(item_id=esc["cam"].id, quantity=1, status="EN_REPARACION",
                     source_location_id=esc["truck"].id, created_by_user_id=1)
    A.db.session.add(vieja)
    A.db.session.commit()

    ajena = A.ItemUnit.query.filter_by(serial="SN-AJENA").first()
    # Lo que la pantalla ofrece sale de acá (ver también
    # test_selector_seriales_popup: el selector no la lista).
    assert A.repair_units_reserved(esc["cam"].id, except_repair_id=vieja.id) == {ajena.id}

    c = _admin(A)
    c.post("/reparaciones", data={
        "repair_id": str(vieja.id), "repair_action": "reparado", "unit_id": str(ajena.id),
    })
    assert A.Repair.query.get(vieja.id).status == "EN_REPARACION"
    assert _estado(A, "SN-AJENA") == (A.UNIT_EN_STOCK, esc["rep"].id)

    # La nueva sigue sacando la suya sin elegir.
    c.post("/reparaciones", data={"repair_id": str(r_nueva.id), "repair_action": "reparado"})
    assert A.Repair.query.get(r_nueva.id).status == "REPARADO"
    assert _estado(A, "SN-AJENA") == (A.UNIT_EN_STOCK, esc["jaula"].id)
