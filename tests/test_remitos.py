"""Remitos: permisos, vista y semántica (agrupa movimientos, no toca stock).

NOTA: en esta versión el remito NO crea movimientos ni mueve stock: agrupa
movimientos YA existentes de una relación Desde->Hacia y les da número. Se
crea directamente como CONFIRMADO. Se testea esa semántica real.
"""
import pytest
from conftest import make_user, make_item, make_location, login


@pytest.fixture()
def escenario(A):
    """Proveedor (externo) -> Dep1 (interno con responsable), 1 movimiento."""
    A.app.config["WTF_CSRF_ENABLED"] = False
    make_user(A, "sup", "SUPERVISOR")
    make_user(A, "tec", "TECNICO")
    make_user(A, "lec", "LECTOR")
    resp = make_user(A, "resp", "SUPERVISOR", full_name="Responsable Dep1")

    proveedor = A.Location.query.filter_by(name="Proveedor").first()
    dep1 = make_location(A, "Dep1")
    A.db.session.add(A.LocationResponsible(location_id=dep1.id, user_id=resp.id))

    it = make_item(A, code="CAB-001", name="Cable")
    # Movimiento Proveedor(externo) -> Dep1: entra 1 al stock de Dep1
    A.upsert_stock(it.id, dep1.id, 1)
    y, seq, number = A.next_movement_number()
    admin = A.User.query.filter_by(role="ADMIN").first()
    mov = A.Movement(item_id=it.id, qty=1, from_location_id=proveedor.id,
                     to_location_id=dep1.id, user_id=admin.id, year=y, seq=seq, number=number)
    A.db.session.add(mov)
    A.db.session.commit()
    return {"proveedor": proveedor, "dep1": dep1, "mov": mov, "resp": resp}


def _client(A, u, p="pass1234"):
    c = A.app.test_client()
    login(c, u, p)
    return c


def _crear_remito(client, esc):
    return client.post("/remitos/new", data={
        "from_location_id": str(esc["proveedor"].id),
        "to_location_id": str(esc["dep1"].id),
        "movement_id": str(esc["mov"].id),
        "responsible_to_id": str(esc["resp"].id),
    }, follow_redirects=False)


def test_admin_crea_remito(A, escenario):
    _crear_remito(_client(A, "admin", "admin123"), escenario)
    assert A.Remito.query.count() == 1


def test_supervisor_crea_remito(A, escenario):
    _crear_remito(_client(A, "sup"), escenario)
    assert A.Remito.query.count() == 1


def test_tecnico_no_crea_remito(A, escenario):
    _crear_remito(_client(A, "tec"), escenario)
    assert A.Remito.query.count() == 0


def test_lector_no_crea_remito(A, escenario):
    _crear_remito(_client(A, "lec"), escenario)
    assert A.Remito.query.count() == 0


def test_remito_se_crea_confirmado(A, escenario):
    _crear_remito(_client(A, "admin", "admin123"), escenario)
    r = A.Remito.query.first()
    assert r.status == "CONFIRMADO"


def test_remito_agrupa_movimiento_sin_tocar_stock(A, escenario):
    dep1 = escenario["dep1"]
    it = escenario["mov"].item
    qty_before = A.Stock.query.filter_by(item_id=it.id, location_id=dep1.id).first().quantity
    _crear_remito(_client(A, "admin", "admin123"), escenario)
    r = A.Remito.query.first()
    # línea creada agrupando el movimiento existente
    assert A.RemitoLine.query.filter_by(remito_id=r.id).count() == 1
    # el stock NO cambió por crear el remito
    assert A.Stock.query.filter_by(item_id=it.id, location_id=dep1.id).first().quantity == qty_before


def test_tecnico_y_lector_pueden_ver_remitos(A, escenario):
    _crear_remito(_client(A, "admin", "admin123"), escenario)
    r = A.Remito.query.first()
    for role in ("tec", "lec"):
        c = _client(A, role)
        assert c.get("/remitos").status_code == 200
        assert c.get(f"/remitos/{r.id}").status_code == 200
