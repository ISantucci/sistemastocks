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
        "tipo": "EGRESO", "supplier_id": str(s.id),
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
        "tipo": "EGRESO", "supplier_id": str(s.id),
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
        "tipo": "EGRESO", "supplier_id": str(s.id),
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
        "tipo": "EGRESO", "supplier_id": str(s.id),
        "item_id[]": [str(it.id)], "qty[]": ["10"], "line_serials[]": [""],
    }, follow_redirects=True)
    # Stock intacto (no dejó sobregirar)
    assert A.Stock.query.filter_by(item_id=it.id, location_id=jaula.id).first().quantity == 3
    assert A.Movement.query.filter(A.Movement.supplier_id == s.id).count() == 0


def test_ingreso_egreso_no_aparece_en_movements(A, client):
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-300", name="Cable 300")
    s = _mk_supplier(A)
    client.post("/ingresos-egresos", data={
        "tipo": "INGRESO", "supplier_id": str(s.id),
        "item_id[]": [str(it.id)], "qty[]": ["3"], "line_serials[]": [""],
    }, follow_redirects=True)
    html = client.get("/movements").get_data(as_text=True)
    m = A.Movement.query.filter(A.Movement.supplier_id == s.id).first()
    assert m.number not in html


def test_marcar_remito_impreso(A, client):
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-400", name="Cable 400")
    s = _mk_supplier(A)
    client.post("/ingresos-egresos", data={
        "tipo": "INGRESO", "supplier_id": str(s.id),
        "item_id[]": [str(it.id)], "qty[]": ["1"], "line_serials[]": [""],
    }, follow_redirects=True)
    rem = A.Remito.query.filter_by(print_pending=True).first()
    client.post(f"/remitos/{rem.id}/impreso", follow_redirects=True)
    assert A.Remito.query.get(rem.id).print_pending is False
