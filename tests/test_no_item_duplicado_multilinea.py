"""Regresión: un ítem no puede aparecer en más de una línea de una carga multi-fila.

Bug: en las pantallas multi-línea (Carga múltiple, Utilizados, Descartes,
Ingresos/Egresos, Solicitud de repuestos) se podía elegir el MISMO ítem en
varias filas. El tope de cantidad del front es POR FILA, así que 3 filas de 1
pasaban la validación aunque hubiera 1 sola unidad, y aun con stock suficiente
quedaban movimientos separados del mismo ítem (trazabilidad sucia).

El front ahora saca el ítem elegido del listado de las demás filas
(static/js/line_dedupe.js), pero eso es solo UX: acá se verifica la validación
REAL del backend, que es la que no se puede saltear con un POST armado a mano.

Criterio verificado en cada caso:
  - el POST se rechaza,
  - NO se crea ningún movimiento/registro (todo-o-nada),
  - el stock queda intacto.
"""
import pytest
from conftest import make_user, make_item, make_location, login


@pytest.fixture()
def esc(A):
    A.app.config["WTF_CSRF_ENABLED"] = False
    dep = make_location(A, "Deposito")
    truck = make_location(A, "Camioneta", is_truck=True)
    jaula = make_location(A, A.LOCATION_JAULA_TNG)
    cable = make_item(A, code="CAB-001", name="Cable")
    domo = make_item(A, code="DOM-001", name="Domo")
    A.upsert_stock(cable.id, dep.id, 1)      # una sola unidad: el caso del bug
    A.upsert_stock(domo.id, dep.id, 5)
    A.upsert_stock(cable.id, jaula.id, 5)
    A.db.session.commit()
    return {"dep": dep, "truck": truck, "jaula": jaula, "cable": cable, "domo": domo}


def _admin(A):
    c = A.app.test_client()
    login(c, "admin", "admin123")
    return c


def _stock(A, item_id, loc_id):
    row = A.Stock.query.filter_by(item_id=item_id, location_id=loc_id).first()
    return row.quantity if row else 0


# ------------------------------------------------------------------ MOVER

def test_carga_multiple_rechaza_item_repetido(A, esc):
    c = _admin(A)
    movs = A.Movement.query.count()
    r = c.post("/movements/bulk", data={
        "from_location_id": str(esc["dep"].id),
        "to_location_id": str(esc["truck"].id),
        "item_id[]": [str(esc["cable"].id), str(esc["cable"].id), str(esc["cable"].id)],
        "qty[]": ["1", "1", "1"],
        "generate_pending[]": ["0", "0", "0"],
        "scrap_reason[]": ["", "", ""],
    }, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "ya esta cargado" in html or "ya está cargado" in html
    assert A.Movement.query.count() == movs          # no se creó nada
    assert _stock(A, esc["cable"].id, esc["dep"].id) == 1   # stock intacto


def test_carga_multiple_permite_items_distintos(A, esc):
    """Control: el caso legítimo sigue funcionando."""
    c = _admin(A)
    c.post("/movements/bulk", data={
        "from_location_id": str(esc["dep"].id),
        "to_location_id": str(esc["truck"].id),
        "item_id[]": [str(esc["cable"].id), str(esc["domo"].id)],
        "qty[]": ["1", "2"],
        "generate_pending[]": ["0", "0"],
        "scrap_reason[]": ["", ""],
    }, follow_redirects=True)
    assert A.Movement.query.count() == 2
    assert _stock(A, esc["cable"].id, esc["truck"].id) == 1
    assert _stock(A, esc["domo"].id, esc["truck"].id) == 2


# --------------------------------------------------------------- UTILIZAR

def test_utilizados_rechaza_item_repetido(A, esc):
    c = _admin(A)
    movs = A.Movement.query.count()
    r = c.post("/item-usage", data={
        "from_location_id": str(esc["jaula"].id),
        "item_id[]": [str(esc["cable"].id), str(esc["cable"].id)],
        "qty[]": ["1", "1"],
    }, follow_redirects=True)
    assert "ya está cargado" in r.get_data(as_text=True)
    assert A.Movement.query.count() == movs
    assert _stock(A, esc["cable"].id, esc["jaula"].id) == 5


# --------------------------------------------------------------- DESCARTE

def test_descartes_rechaza_item_repetido_aunque_el_motivo_sea_distinto(A, esc):
    """Decidido con Ignacio: bloqueo total, incluso con motivos distintos.

    Para descartar el mismo ítem con dos motivos hay que hacer dos cargas.
    """
    c = _admin(A)
    scraps = A.Scrap.query.count()
    movs = A.Movement.query.count()
    r = c.post("/scrap", data={
        "from_location_id": str(esc["dep"].id),
        "item_id[]": [str(esc["domo"].id), str(esc["domo"].id)],
        "qty[]": ["1", "1"],
        "scrap_reason[]": ["Roto", "Vencido"],
    }, follow_redirects=True)
    assert "ya está cargado" in r.get_data(as_text=True)
    assert A.Scrap.query.count() == scraps
    assert A.Movement.query.count() == movs
    assert _stock(A, esc["domo"].id, esc["dep"].id) == 5


def test_descartes_permite_items_distintos(A, esc):
    c = _admin(A)
    c.post("/scrap", data={
        "from_location_id": str(esc["dep"].id),
        "item_id[]": [str(esc["cable"].id), str(esc["domo"].id)],
        "qty[]": ["1", "1"],
        "scrap_reason[]": ["Roto", "Vencido"],
    }, follow_redirects=True)
    assert A.Scrap.query.count() == 2
    assert _stock(A, esc["cable"].id, esc["dep"].id) == 0


# -------------------------------------------------- EL BUG CONCRETO REPORTADO

def test_un_cable_no_se_puede_mover_tres_veces(A, esc):
    """«Si tengo 1 cable puedo cargarlo 3 veces y mover 3 veces el cable x1»."""
    c = _admin(A)
    c.post("/movements/bulk", data={
        "from_location_id": str(esc["dep"].id),
        "to_location_id": str(esc["truck"].id),
        "item_id[]": [str(esc["cable"].id)] * 3,
        "qty[]": ["1", "1", "1"],
        "generate_pending[]": ["0", "0", "0"],
        "scrap_reason[]": ["", "", ""],
    }, follow_redirects=True)
    # Ni un movimiento: antes se creaba el primero y recién el segundo fallaba
    # por stock, con un mensaje que no explicaba nada.
    assert A.Movement.query.count() == 0
    assert _stock(A, esc["cable"].id, esc["truck"].id) == 0
    assert _stock(A, esc["cable"].id, esc["dep"].id) == 1


# ------------------------------------------------------- INGRESOS / EGRESOS

def test_ingresos_egresos_rechaza_item_repetido(A, esc):
    c = _admin(A)
    sup = A.Supplier(contact_name="Prov Test", is_active=True)
    A.db.session.add(sup)
    A.db.session.commit()
    movs = A.Movement.query.count()
    r = c.post("/ingresos-egresos", data={
        "tipo": "INGRESO",
        "supplier_id": str(sup.id),
        "item_id[]": [str(esc["cable"].id), str(esc["cable"].id)],
        "qty[]": ["3", "2"],
        "line_serials[]": ["", ""],
    }, follow_redirects=True)
    assert "en m\u00e1s de una fila" in r.get_data(as_text=True)
    assert A.Movement.query.count() == movs
    assert _stock(A, esc["cable"].id, esc["jaula"].id) == 5   # nada ingresado


# -------------------------------------------------- SOLICITUD DE REPUESTOS

def test_solicitud_repuestos_rechaza_item_repetido(A, esc):
    tec = make_user(A, "tec", "TECNICO")
    A.db.session.add(A.LocationResponsible(location_id=esc["truck"].id, user_id=tec.id))
    A.db.session.commit()
    c = A.app.test_client()
    login(c, "tec")
    r = c.post("/solicitudes-repuestos/new", data={
        "dest_location_id": str(esc["truck"].id),
        "item_id[]": [str(esc["cable"].id), str(esc["cable"].id)],
        "qty[]": ["1", "1"],
    }, follow_redirects=True)
    assert "ya est\u00e1 cargado" in r.get_data(as_text=True)
    assert A.RepairRequest.query.count() == 0
