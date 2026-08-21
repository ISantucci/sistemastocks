"""Smoke tests de Ingresos/Egresos (multi-ítem), Proveedores y remito automático."""
from conftest import login, make_item, make_location


def _admin(client):
    return login(client, "admin", "admin123")


def _mk_supplier(A, name="ACME"):
    s = A.Supplier(contact_name=name, email="a@acme.com", is_active=True)
    A.db.session.add(s)
    A.db.session.commit()
    return s


def _base(A):
    make_location(A, "Jaula TNG")
    make_location(A, "Proveedor", is_external=True)


def test_abm_proveedor(A, client):
    _admin(client)
    r = client.post("/proveedores", data={
        "contact_name": "Juan Perez", "business_name": "Ferretería Perez",
        "legal_name": "Perez SA", "cuit": "20-11111111-2",
        "email": "juan@perez.com", "phone": "11-5555",
    }, follow_redirects=True)
    assert r.status_code == 200
    s = A.Supplier.query.filter_by(contact_name="Juan Perez").first()
    assert s and s.cuit == "20-11111111-2" and s.is_active
    client.post(f"/proveedores/{s.id}/baja", follow_redirects=True)
    assert A.Supplier.query.get(s.id).is_active is False


def test_ingreso_multi_item_un_remito(A, client):
    _admin(client)
    _base(A)
    i1 = make_item(A, code="CAB-100", name="Cable 100")
    i2 = make_item(A, code="CAB-101", name="Cable 101")
    s = _mk_supplier(A)

    r = client.post("/ingresos-egresos", data={
        "tipo": "INGRESO", "supplier_id": str(s.id),
        "item_id[]": [str(i1.id), str(i2.id)],
        "qty[]": ["7", "3"],
        "line_serials[]": ["", ""],
        "line_total[]": ["1000,00", "1000,00"],
    }, follow_redirects=True)
    assert r.status_code == 200

    jaula = A.Location.query.filter_by(name="Jaula TNG").first()
    assert A.Stock.query.filter_by(item_id=i1.id, location_id=jaula.id).first().quantity == 7
    assert A.Stock.query.filter_by(item_id=i2.id, location_id=jaula.id).first().quantity == 3
    # Dos movimientos, un solo remito
    assert A.Movement.query.filter(A.Movement.supplier_id == s.id).count() == 2
    assert A.Remito.query.filter_by(print_pending=True).count() == 1


def test_egreso_descuenta_jaula(A, client):
    _admin(client)
    jaula = make_location(A, "Jaula TNG")
    make_location(A, "Proveedor", is_external=True)
    it = make_item(A, code="CAB-200", name="Cable 200")
    A.db.session.add(A.Stock(item_id=it.id, location_id=jaula.id, quantity=5))
    A.db.session.commit()
    s = _mk_supplier(A)

    client.post("/ingresos-egresos", data={
        "tipo": "EGRESO", "motivo": "OTRO", "supplier_id": str(s.id),
        "item_id[]": [str(it.id)], "qty[]": ["2"], "line_serials[]": [""],
    }, follow_redirects=True)
    assert A.Stock.query.filter_by(item_id=it.id, location_id=jaula.id).first().quantity == 3


def test_ingreso_serializado_solo_suma_stock(A, client):
    # Ingreso serializado: solo entra la cantidad (cupo). Los seriales se
    # etiquetan DESPUÉS desde la ficha; el ingreso NO crea unidades.
    _admin(client)
    _base(A)
    it = make_item(A, code="DOM-001", name="Cámara Domo")
    it.serialized = True
    A.db.session.commit()
    s = _mk_supplier(A)

    r = client.post("/ingresos-egresos", data={
        "tipo": "INGRESO", "supplier_id": str(s.id),
        "item_id[]": [str(it.id)], "qty[]": ["3"], "line_serials[]": [""],
        "line_total[]": ["1000,00"],
    }, follow_redirects=True)
    assert r.status_code == 200

    jaula = A.Location.query.filter_by(name="Jaula TNG").first()
    assert A.Stock.query.filter_by(item_id=it.id, location_id=jaula.id).first().quantity == 3
    assert A.ItemUnit.query.filter_by(item_id=it.id).count() == 0  # se etiquetan luego


def test_egreso_serializado_elige_seriales(A, client):
    _admin(client)
    jaula = make_location(A, "Jaula TNG")
    make_location(A, "Proveedor", is_external=True)
    it = make_item(A, code="DOM-002", name="Cámara Domo 2")
    it.serialized = True
    A.db.session.add(A.Stock(item_id=it.id, location_id=jaula.id, quantity=2))
    # Dos seriales ya etiquetados en Jaula
    A.db.session.add(A.ItemUnit(item_id=it.id, serial="SN-1",
                                status=A.UNIT_EN_STOCK, location_id=jaula.id))
    A.db.session.add(A.ItemUnit(item_id=it.id, serial="SN-2",
                                status=A.UNIT_EN_STOCK, location_id=jaula.id))
    A.db.session.commit()
    s = _mk_supplier(A)

    r = client.post("/ingresos-egresos", data={
        "tipo": "EGRESO", "motivo": "OTRO", "supplier_id": str(s.id),
        "item_id[]": [str(it.id)], "qty[]": ["1"], "line_serials[]": ["SN-1"],
    }, follow_redirects=True)
    assert r.status_code == 200

    # Stock bajó a 1 y el serial elegido ya no está EN_STOCK en Jaula
    assert A.Stock.query.filter_by(item_id=it.id, location_id=jaula.id).first().quantity == 1
    u1 = A.ItemUnit.query.filter_by(item_id=it.id, serial="SN-1").first()
    assert u1.status != A.UNIT_EN_STOCK
    u2 = A.ItemUnit.query.filter_by(item_id=it.id, serial="SN-2").first()
    assert u2.status == A.UNIT_EN_STOCK  # el no elegido queda


def test_egreso_serializado_sin_seriales_se_rechaza(A, client):
    # Egreso serializado SIN elegir seriales: se rechaza (no egresa nada).
    _admin(client)
    jaula = make_location(A, "Jaula TNG")
    make_location(A, "Proveedor", is_external=True)
    it = make_item(A, code="DOM-003", name="Cámara Domo 3")
    it.serialized = True
    A.db.session.add(A.Stock(item_id=it.id, location_id=jaula.id, quantity=2))
    A.db.session.add(A.ItemUnit(item_id=it.id, serial="Z-1",
                                status=A.UNIT_EN_STOCK, location_id=jaula.id))
    A.db.session.commit()
    s = _mk_supplier(A)

    client.post("/ingresos-egresos", data={
        "tipo": "EGRESO", "motivo": "OTRO", "supplier_id": str(s.id),
        "item_id[]": [str(it.id)], "qty[]": ["1"], "line_serials[]": [""],
    }, follow_redirects=True)
    # Stock intacto y no se generó movimiento
    assert A.Stock.query.filter_by(item_id=it.id, location_id=jaula.id).first().quantity == 2
    assert A.Movement.query.filter(A.Movement.supplier_id == s.id).count() == 0


def test_egreso_mayor_al_stock_se_rechaza(A, client):
    # Red de seguridad server-side: no se puede egresar más de lo que hay.
    _admin(client)
    jaula = make_location(A, "Jaula TNG")
    make_location(A, "Proveedor", is_external=True)
    it = make_item(A, code="CAB-500", name="Cable 500")
    A.db.session.add(A.Stock(item_id=it.id, location_id=jaula.id, quantity=3))
    A.db.session.commit()
    s = _mk_supplier(A)
    client.post("/ingresos-egresos", data={
        "tipo": "EGRESO", "motivo": "OTRO", "supplier_id": str(s.id),
        "item_id[]": [str(it.id)], "qty[]": ["10"], "line_serials[]": [""],
    }, follow_redirects=True)
    # Stock intacto (no dejó sobregirar)
    assert A.Stock.query.filter_by(item_id=it.id, location_id=jaula.id).first().quantity == 3
    assert A.Movement.query.filter(A.Movement.supplier_id == s.id).count() == 0


def test_ingreso_egreso_aparece_en_movements_pero_no_en_el_export(A, client):
    """El LISTADO de /movements incluye ingresos/egresos; el EXPORT CSV no.

    Es deliberado (_build_movements_query(include_supplier=...)): en pantalla
    conviene ver todo junto, pero el CSV mantiene el alcance historico de
    movimientos internos.
    """
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-300", name="Cable 300")
    s = _mk_supplier(A)
    client.post("/ingresos-egresos", data={
        "tipo": "INGRESO", "supplier_id": str(s.id),
        "item_id[]": [str(it.id)], "qty[]": ["3"], "line_serials[]": [""],
        "line_total[]": ["1000,00"],
    }, follow_redirects=True)
    m = A.Movement.query.filter(A.Movement.supplier_id == s.id).first()
    assert m is not None

    html = client.get("/movements").get_data(as_text=True)
    assert m.number in html, "el listado debe mostrar los ingresos/egresos"

    csv_text = client.get("/movements/export.csv").get_data(as_text=True)
    assert m.number not in csv_text, "el export mantiene solo movimientos internos"

def test_marcar_remito_impreso(A, client):
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-400", name="Cable 400")
    s = _mk_supplier(A)
    client.post("/ingresos-egresos", data={
        "tipo": "INGRESO", "supplier_id": str(s.id),
        "item_id[]": [str(it.id)], "qty[]": ["1"], "line_serials[]": [""],
        "line_total[]": ["1000,00"],
    }, follow_redirects=True)
    rem = A.Remito.query.filter_by(print_pending=True).first()
    client.post(f"/remitos/{rem.id}/impreso", follow_redirects=True)
    assert A.Remito.query.get(rem.id).print_pending is False


# ---------------------------------------------------------------------------
# Motivo del egreso (Devolución / Reparación / Otro)
#
# Por qué: un egreso sin motivo no deja rastro de para qué salió la mercadería.
# Con motivo "Reparación" además queda esperando devolución en /reparaciones.
# ---------------------------------------------------------------------------

def _egreso_loc(A, code, qty=5, serialized=False):
    jaula = make_location(A, "Jaula TNG")
    make_location(A, "Proveedor", is_external=True)
    it = make_item(A, code=code, name=f"Item {code}")
    if serialized:
        it.serialized = True
    A.db.session.add(A.Stock(item_id=it.id, location_id=jaula.id, quantity=qty))
    A.db.session.commit()
    return jaula, it


def test_egreso_sin_motivo_se_rechaza(A, client):
    _admin(client)
    jaula, it = _egreso_loc(A, "MOT-001")
    s = _mk_supplier(A)
    client.post("/ingresos-egresos", data={
        "tipo": "EGRESO", "supplier_id": str(s.id),
        "item_id[]": [str(it.id)], "qty[]": ["2"], "line_serials[]": [""],
    }, follow_redirects=True)
    # Nada se movió: sin motivo el egreso no se registra.
    assert A.Stock.query.filter_by(item_id=it.id, location_id=jaula.id).first().quantity == 5
    assert A.Movement.query.filter(A.Movement.supplier_id == s.id).count() == 0


def test_egreso_motivo_invalido_se_rechaza(A, client):
    _admin(client)
    jaula, it = _egreso_loc(A, "MOT-002")
    s = _mk_supplier(A)
    client.post("/ingresos-egresos", data={
        "tipo": "EGRESO", "motivo": "CUALQUIERA", "supplier_id": str(s.id),
        "item_id[]": [str(it.id)], "qty[]": ["2"], "line_serials[]": [""],
    }, follow_redirects=True)
    assert A.Stock.query.filter_by(item_id=it.id, location_id=jaula.id).first().quantity == 5
    assert A.Movement.query.filter(A.Movement.supplier_id == s.id).count() == 0


def test_ingreso_no_necesita_motivo(A, client):
    # El motivo es solo del egreso: el ingreso sigue funcionando igual que antes.
    _admin(client)
    _base(A)
    it = make_item(A, code="MOT-003", name="Item MOT-003")
    s = _mk_supplier(A)
    r = client.post("/ingresos-egresos", data={
        "tipo": "INGRESO", "supplier_id": str(s.id),
        "item_id[]": [str(it.id)], "qty[]": ["4"], "line_serials[]": [""],
        "line_total[]": ["1000,00"],
    }, follow_redirects=True)
    assert r.status_code == 200
    m = A.Movement.query.filter(A.Movement.supplier_id == s.id).first()
    assert m is not None
    assert (m.observation or "") == ""  # sin motivo en la observación


def test_egreso_devolucion_deja_el_motivo_en_la_observacion(A, client):
    _admin(client)
    jaula, it = _egreso_loc(A, "MOT-004")
    s = _mk_supplier(A)
    client.post("/ingresos-egresos", data={
        "tipo": "EGRESO", "motivo": "DEVOLUCION", "supplier_id": str(s.id),
        "observation": "vino fallado",
        "item_id[]": [str(it.id)], "qty[]": ["2"], "line_serials[]": [""],
    }, follow_redirects=True)

    m = A.Movement.query.filter(A.Movement.supplier_id == s.id).first()
    assert m is not None
    assert "Devolución" in m.observation and "vino fallado" in m.observation

    # El remito separa las dos cosas: el motivo va en el CONCEPTO (lo pone el
    # sistema) y la observación queda con lo que escribió la persona, sin
    # mezclarse. Antes iban concatenados en observation.
    rem = A.Remito.query.order_by(A.Remito.id.desc()).first()
    assert "Devolución" in rem.concept
    assert rem.observation == "vino fallado"

    # Devolución NO genera reparación pendiente.
    assert A.Repair.query.count() == 0
    assert A.Stock.query.filter_by(item_id=it.id, location_id=jaula.id).first().quantity == 3


def test_egreso_reparacion_queda_esperando_en_proveedor(A, client):
    _admin(client)
    jaula, it = _egreso_loc(A, "MOT-005")
    s = _mk_supplier(A)
    client.post("/ingresos-egresos", data={
        "tipo": "EGRESO", "motivo": "REPARACION", "supplier_id": str(s.id),
        "item_id[]": [str(it.id)], "qty[]": ["5"], "line_serials[]": [""],
    }, follow_redirects=True)

    # El stock salió de la Jaula (igual que antes).
    assert A.Stock.query.filter_by(item_id=it.id, location_id=jaula.id).first().quantity == 0

    # Y quedó una reparación esperando devolución del proveedor.
    reps = A.Repair.query.all()
    assert len(reps) == 1
    r = reps[0]
    assert r.status == "EN_PROVEEDOR"
    assert r.item_id == it.id and r.quantity == 5
    assert r.source_location_id == jaula.id

    # Se ve en la sección "En proveedor" de /reparaciones.
    html = client.get("/reparaciones").get_data(as_text=True)
    assert "MOT-005" in html

    m = A.Movement.query.filter(A.Movement.supplier_id == s.id).first()
    assert "Reparación" in m.observation


def test_egreso_reparacion_se_cierra_con_el_flujo_existente(A, client):
    # Al volver del proveedor se cierra con el botón que ya existía: el stock
    # vuelve a la Jaula y la reparación queda REPARADO.
    _admin(client)
    jaula, it = _egreso_loc(A, "MOT-006", qty=3)
    make_location(A, "En reparación")
    s = _mk_supplier(A)
    client.post("/ingresos-egresos", data={
        "tipo": "EGRESO", "motivo": "REPARACION", "supplier_id": str(s.id),
        "item_id[]": [str(it.id)], "qty[]": ["3"], "line_serials[]": [""],
    }, follow_redirects=True)
    rep = A.Repair.query.first()

    client.post("/reparaciones", data={
        "repair_id": str(rep.id), "repair_action": "reparado_proveedor",
        "supplier_id": str(s.id),
    }, follow_redirects=True)

    rep = A.Repair.query.get(rep.id)
    assert rep.status == "REPARADO" and rep.resolved_at is not None
    assert A.Stock.query.filter_by(item_id=it.id, location_id=jaula.id).first().quantity == 3


def test_egreso_reparacion_multi_item_crea_una_reparacion_por_linea(A, client):
    _admin(client)
    jaula, it1 = _egreso_loc(A, "MOT-007", qty=4)
    it2 = make_item(A, code="MOT-008", name="Item MOT-008")
    A.db.session.add(A.Stock(item_id=it2.id, location_id=jaula.id, quantity=2))
    A.db.session.commit()
    s = _mk_supplier(A)

    client.post("/ingresos-egresos", data={
        "tipo": "EGRESO", "motivo": "REPARACION", "supplier_id": str(s.id),
        "item_id[]": [str(it1.id), str(it2.id)],
        "qty[]": ["4", "2"], "line_serials[]": ["", ""],
    }, follow_redirects=True)

    reps = {r.item_id: r for r in A.Repair.query.all()}
    assert set(reps) == {it1.id, it2.id}
    assert reps[it1.id].quantity == 4 and reps[it2.id].quantity == 2
    assert all(r.status == "EN_PROVEEDOR" for r in reps.values())


def test_egreso_reparacion_serializado_no_crea_reparacion(A, client):
    # /reparaciones todavía no resuelve seriales: no se crea una fila que
    # después no se pueda cerrar. El egreso sí se registra.
    _admin(client)
    jaula, it = _egreso_loc(A, "MOT-009", qty=2, serialized=True)
    A.db.session.add(A.ItemUnit(item_id=it.id, serial="SNR-1",
                                status=A.UNIT_EN_STOCK, location_id=jaula.id))
    A.db.session.commit()
    s = _mk_supplier(A)

    client.post("/ingresos-egresos", data={
        "tipo": "EGRESO", "motivo": "REPARACION", "supplier_id": str(s.id),
        "item_id[]": [str(it.id)], "qty[]": ["1"], "line_serials[]": ["SNR-1"],
    }, follow_redirects=True)

    assert A.Repair.query.count() == 0
    m = A.Movement.query.filter(A.Movement.supplier_id == s.id).first()
    assert m is not None and "Reparación" in m.observation
    assert A.Stock.query.filter_by(item_id=it.id, location_id=jaula.id).first().quantity == 1


def test_form_egreso_tiene_el_selector_de_motivo(A, client):
    _admin(client)
    _base(A)
    html = client.get("/ingresos-egresos").get_data(as_text=True)
    assert 'name="motivo"' in html
    assert 'value="REPARACION"' in html
