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
    """Se genera UN pendiente POR UNIDAD a devolver, no uno por movimiento.

    Es deliberado: de dos unidades entregadas, una puede volver bien y la otra
    ir a reparacion, y cada una se cierra por separado.
    """
    _crear_pendiente(A, esc)          # el helper entrega qty=2
    assert A.PendingDelivery.query.count() == 2
    assert A.Movement.query.count() == 1
    assert all(p.return_qty == 1 for p in A.PendingDelivery.query.all())


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


def test_tecnico_ve_pendientes_y_lector_no(A, esc):
    """El TECNICO SI accede a /pending-deliveries: ve los suyos.

    Confirmado con Ignacio (2026-08-13). La pantalla filtra por objeto, y eso
    se verifica aparte en test_tecnico_scope.py. El LECTOR no accede.
    """
    c_tec = A.app.test_client()
    login(c_tec, "tec")
    assert c_tec.get("/pending-deliveries").status_code == 200

    c_lec = A.app.test_client()
    login(c_lec, "lec")
    assert c_lec.get("/pending-deliveries").status_code in (302, 403)
