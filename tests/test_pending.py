"""Tarea 3.3 / regresión: pendientes y devolución.

No se agregan fechas de vencimiento ni estados nuevos.
"""
import pytest
from conftest import make_user, make_item, make_location, login


@pytest.fixture()
def esc(A):
    A.app.config["WTF_CSRF_ENABLED"] = False
    make_user(A, "tec", "TECNICO")
    make_user(A, "lec", "LECTOR")
    resp = make_user(A, "resp", "SUPERVISOR")
    dep = make_location(A, "Deposito")
    truck = make_location(A, "Camioneta", is_truck=True)
    A.db.session.add(A.LocationResponsible(location_id=truck.id, user_id=resp.id))
    it = make_item(A, code="CAB-001", name="Cable")
    A.upsert_stock(it.id, dep.id, 10)
    A.db.session.commit()
    return {"dep": dep, "truck": truck, "item": it, "resp": resp}


def _admin(A):
    c = A.app.test_client()
    login(c, "admin", "admin123")
    return c


def _crear_pendiente(A, esc):
    c = _admin(A)
    c.post("/movements", data={
        "item_id": str(esc["item"].id), "qty": "2",
        "from_location_id": str(esc["dep"].id), "to_location_id": str(esc["truck"].id),
        "generate_pending": "1", "pending_comment": "test",
    })
    return c


def test_creacion_pendiente_junto_con_movimiento(A, esc):
    _crear_pendiente(A, esc)
    assert A.PendingDelivery.query.count() == 1
    assert A.Movement.query.count() == 1


def test_cierre_genera_movimiento_inverso_y_returned_true(A, esc):
    c = _crear_pendiente(A, esc)
    p = A.PendingDelivery.query.first()
    movs_before = A.Movement.query.count()
    c.post("/pending-deliveries", data={"pending_id": str(p.id), "return_action": "return"})
    assert A.Movement.query.count() == movs_before + 1  # movimiento inverso
    assert A.PendingDelivery.query.get(p.id).returned is True


def test_segundo_cierre_no_genera_otro_movimiento(A, esc):
    c = _crear_pendiente(A, esc)
    p = A.PendingDelivery.query.first()
    c.post("/pending-deliveries", data={"pending_id": str(p.id), "return_action": "return"})
    movs_after_first = A.Movement.query.count()
    c.post("/pending-deliveries", data={"pending_id": str(p.id), "return_action": "return"})
    assert A.Movement.query.count() == movs_after_first  # no duplica


def test_tecnico_y_lector_no_acceden_pendientes(A, esc):
    for role in ("tec", "lec"):
        c = A.app.test_client()
        login(c, role)
        assert c.get("/pending-deliveries").status_code in (302, 403)
