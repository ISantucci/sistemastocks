"""Smoke test: email en usuarios + destinatarios dinámicos de solicitudes de
compra. (El rol PROVEEDOR y el teléfono se movieron a la entidad Supplier.)"""
from conftest import login, make_item, make_location


def _admin_login(client):
    return login(client, "admin", "admin123")


def test_crear_usuario_con_email(A, client):
    _admin_login(client)
    r = client.post("/users", data={
        "username": "encargado",
        "full_name": "Encargado Compras",
        "role": "SUPERVISOR",
        "email": "compras@empresa.com",
        "password": "clave1234",
    }, follow_redirects=True)
    assert r.status_code == 200
    u = A.User.query.filter_by(username="encargado").first()
    assert u is not None and u.email == "compras@empresa.com"


def test_solicitud_con_destinatarios_arma_mail(A, client):
    _admin_login(client)
    dest = A.User(username="dest", full_name="Destino", role="SUPERVISOR",
                  email="dest@empresa.com")
    dest.set_password("clave1234")
    A.db.session.add(dest)
    it = make_item(A, code="CAB-777", name="Cable 777", stock_min=5)
    jaula = make_location(A, "Jaula TNG")
    A.db.session.add(A.Stock(item_id=it.id, location_id=jaula.id, quantity=1))
    A.db.session.commit()

    r = client.post("/solicitudes-compra/new", data={
        "item_id": str(it.id),
        f"qty_{it.id}": "10",
        "recipient_id": str(dest.id),
    }, follow_redirects=True)
    assert r.status_code == 200
    pr = A.PurchaseRequest.query.order_by(A.PurchaseRequest.id.desc()).first()
    assert pr is not None and len(pr.recipients) == 1
    email = A.build_purchase_request_email(pr)
    assert email["to"] == "dest@empresa.com"
