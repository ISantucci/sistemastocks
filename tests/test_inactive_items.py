"""Tarea 4 (adaptada): items inactivos no operables.

En esta versión el remito AGRUPA movimientos existentes (no tiene selector de
items ni crea movimientos/líneas que muevan stock). La protección real de
"item inactivo" vive en los formularios que SÍ crean movimientos (movimientos,
ajustes, descartes, utilizados): el item inactivo no aparece en el selector y
el POST directo se rechaza sin mover stock ni crear Movement.
"""
from conftest import make_item, make_location, login


def _setup(A, client):
    make_location(A, "Dep1")
    make_location(A, "Dep2")
    activo = make_item(A, code="CAB-001", name="Activo", is_active=True)
    inactivo = make_item(A, code="CAB-002", name="Inactivo", is_active=False)
    with A.app.app_context():
        A.upsert_stock(activo.id, A.Location.query.filter_by(name="Dep1").first().id, 10)
        A.upsert_stock(inactivo.id, A.Location.query.filter_by(name="Dep1").first().id, 10)
        A.db.session.commit()
    login(client, "admin", "admin123")
    return activo, inactivo


def test_item_activo_aparece_en_selector(A, client):
    activo, inactivo = _setup(A, client)
    body = client.get("/movements").get_data(as_text=True)
    assert activo.code in body


def test_item_inactivo_no_aparece_en_selector(A, client):
    activo, inactivo = _setup(A, client)
    body = client.get("/movements").get_data(as_text=True)
    assert inactivo.code not in body


def test_post_item_inactivo_rechazado_no_mueve_stock(A, client):
    activo, inactivo = _setup(A, client)
    dep1 = A.Location.query.filter_by(name="Dep1").first()
    dep2 = A.Location.query.filter_by(name="Dep2").first()
    movs_before = A.Movement.query.count()
    qty_before = A.Stock.query.filter_by(item_id=inactivo.id, location_id=dep1.id).first().quantity

    client.post("/movements", data={
        "item_id": str(inactivo.id), "qty": "1",
        "from_location_id": str(dep1.id), "to_location_id": str(dep2.id),
    })

    # No se creó Movement ni se movió stock
    assert A.Movement.query.count() == movs_before
    assert A.Stock.query.filter_by(item_id=inactivo.id, location_id=dep1.id).first().quantity == qty_before
    assert A.Stock.query.filter_by(item_id=inactivo.id, location_id=dep2.id).first() is None
