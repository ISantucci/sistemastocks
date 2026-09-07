"""Filas en blanco en las pantallas de carga múltiple.

El bug reportado: una persona dejó una fila sin completar y al enviar **no pasó
nada**. Ni movimiento, ni mensaje: un error vacío.

Eran dos problemas encadenados, uno de cada lado:

1. **Front:** el `<select>` del ítem está oculto detrás de TomSelect y llevaba
   `required`. Un control oculto y obligatorio hace que el navegador BLOQUEE el
   envío sin poder mostrar el globito de aviso. Se veía como "no pasa nada".
   Encima Ingresos/Egresos arrancaba con TRES filas, así que el que llenaba una
   sola caía siempre en el bug. (Se cubre con las guardas de plantilla de abajo;
   el comportamiento del navegador se verificó aparte en Chromium.)

2. **Backend:** una fila se salteaba solo si el ítem Y la cantidad estaban
   vacíos. Pero la cantidad viene con un `1` por defecto en el HTML, así que una
   fila en blanco NUNCA cumplía las dos condiciones: caía en "ítem inválido" y
   tiraba abajo la carga entera. Eso es lo que se prueba acá.

Regla nueva, en las cinco pantallas: **una fila sin ítem es una fila que no
existe.** No se valida, no se envía y no aparece en el resumen de confirmación.
"""
import pytest
from conftest import make_user, make_item, make_location, login


@pytest.fixture()
def esc(A):
    A.app.config["WTF_CSRF_ENABLED"] = False
    dep = make_location(A, "Deposito")
    truck = make_location(A, "Camioneta", is_truck=True)
    jaula = make_location(A, A.LOCATION_JAULA_TNG)
    make_location(A, "Utilizado", is_external=True)
    make_location(A, A.LOCATION_DESCARTES)
    tec = make_user(A, "tec", "TECNICO")
    A.db.session.add(A.LocationResponsible(location_id=truck.id, user_id=tec.id))
    cable = make_item(A, code="CAB-001", name="Cable")
    domo = make_item(A, code="DOM-001", name="Domo")
    A.upsert_stock(cable.id, dep.id, 10)
    A.upsert_stock(domo.id, dep.id, 10)
    A.upsert_stock(cable.id, jaula.id, 10)
    A.upsert_stock(cable.id, truck.id, 10)
    A.db.session.commit()
    return {"dep": dep, "truck": truck, "jaula": jaula, "cable": cable,
            "domo": domo, "tec": tec}


def _admin(A):
    c = A.app.test_client()
    login(c, "admin", "admin123")
    return c


def _stock(A, item_id, loc_id):
    row = A.Stock.query.filter_by(item_id=item_id, location_id=loc_id).first()
    return row.quantity if row else 0


# ======================================================================
# La fila en blanco no rompe la carga — una pantalla por vez
# ======================================================================

def test_carga_multiple_ignora_la_fila_en_blanco(A, esc):
    """Fila vacía = ítem sin elegir y la cantidad con el 1 que trae el HTML."""
    c = _admin(A)
    movs = A.Movement.query.count()
    r = c.post("/movements/bulk", data={
        "from_location_id": str(esc["dep"].id),
        "to_location_id": str(esc["truck"].id),
        "item_id[]": [str(esc["cable"].id), "", ""],
        "qty[]": ["2", "1", "1"],
        "generate_pending[]": ["0", "0", "0"],
        "scrap_reason[]": ["", "", ""],
    }, follow_redirects=True)

    assert "inv" not in r.get_data(as_text=True).lower() or "Movimiento" in r.get_data(as_text=True)
    assert A.Movement.query.count() == movs + 1
    assert _stock(A, esc["cable"].id, esc["truck"].id) == 12


def test_utilizados_ignora_la_fila_en_blanco(A, esc):
    c = _admin(A)
    movs = A.Movement.query.count()
    c.post("/item-usage", data={
        "from_location_id": str(esc["jaula"].id),
        "item_id[]": [str(esc["cable"].id), ""],
        "qty[]": ["3", "1"],
    }, follow_redirects=True)
    assert A.Movement.query.count() == movs + 1
    assert _stock(A, esc["cable"].id, esc["jaula"].id) == 7


def test_descartes_ignora_la_fila_en_blanco(A, esc):
    c = _admin(A)
    c.post("/scrap", data={
        "from_location_id": str(esc["dep"].id),
        "item_id[]": [str(esc["domo"].id), ""],
        "qty[]": ["1", "1"],
        "scrap_reason[]": ["Roto", ""],
    }, follow_redirects=True)
    assert A.Scrap.query.count() == 1
    assert _stock(A, esc["domo"].id, esc["dep"].id) == 9


def test_ingresos_egresos_ignora_la_fila_en_blanco(A, esc):
    sup = A.Supplier(contact_name="Prov SA", is_active=True)
    A.db.session.add(sup)
    A.db.session.commit()
    make_location(A, A.LOCATION_PROVEEDOR, is_external=True)
    A.db.session.commit()

    c = _admin(A)
    c.post("/ingresos-egresos", data={
        "tipo": "INGRESO",
        "supplier_id": str(sup.id),
        "item_id[]": [str(esc["cable"].id), "", ""],
        "qty[]": ["4", "1", "1"],
        "line_serials[]": ["", "", ""],
        "line_total[]": ["1000,00", "", ""],
    }, follow_redirects=True)
    assert _stock(A, esc["cable"].id, esc["jaula"].id) == 14


def test_solicitud_de_repuestos_ignora_la_fila_en_blanco(A, esc):
    c = A.app.test_client()
    login(c, "tec")
    c.post("/solicitudes-repuestos/new", data={
        "dest_location_id": str(esc["truck"].id),
        "item_id[]": [str(esc["cable"].id), ""],
        "qty[]": ["1", "1"],
    }, follow_redirects=True)
    assert A.RepairRequest.query.count() == 1
    assert len(A.RepairRequest.query.first().lines) == 1


# ======================================================================
# Todo en blanco sí es un error, y lo dice
# ======================================================================

def test_todas_las_filas_en_blanco_avisa_y_no_mueve_nada(A, esc):
    c = _admin(A)
    movs = A.Movement.query.count()
    r = c.post("/movements/bulk", data={
        "from_location_id": str(esc["dep"].id),
        "to_location_id": str(esc["truck"].id),
        "item_id[]": ["", ""],
        "qty[]": ["1", "1"],
        "generate_pending[]": ["0", "0"],
        "scrap_reason[]": ["", ""],
    }, follow_redirects=True)

    assert A.Movement.query.count() == movs
    assert "al menos un item" in r.get_data(as_text=True).lower()


def test_utilizados_todo_en_blanco_avisa(A, esc):
    c = _admin(A)
    r = c.post("/item-usage", data={
        "from_location_id": str(esc["jaula"].id),
        "item_id[]": ["", ""],
        "qty[]": ["1", "1"],
    }, follow_redirects=True)
    assert "al menos un" in r.get_data(as_text=True).lower()


# ======================================================================
# Guardas de las plantillas
# ======================================================================

def test_ingresos_egresos_arranca_con_una_sola_fila(A, esc):
    """Arrancar con tres dejaba dos filas que el usuario no pidió."""
    import re
    html = open("templates/ingresos_egresos.html", encoding="utf-8").read()
    arranque = re.search(r"^\s*addLine\(\);.*$", html, re.M)
    assert arranque, "no se encontró el arranque de filas"
    assert arranque.group(0).count("addLine()") == 1


def test_todas_las_pantallas_multifila_arrancan_con_una_fila(A):
    """Misma regla en las cinco: una fila al entrar, como Utilizados."""
    import re
    pantallas = {
        "movements_bulk.html": "addBulkLine",
        "item_usage.html": "addUsageLine",
        "scrap_report.html": "addScrapLine",
        "ingresos_egresos.html": "addLine",
        "repair_requests.html": "addLine",
    }
    for archivo, fn in pantallas.items():
        html = open(f"templates/{archivo}", encoding="utf-8").read()
        # Llamadas de arranque: las que están solas en su línea, fuera de la
        # definición de la función y del listener del botón "+ Agregar".
        sueltas = [l.strip() for l in html.split("\n")
                   if l.strip().startswith(f"{fn}();")]
        assert len(sueltas) == 1, f"{archivo}: arranca con {len(sueltas)} filas"


def test_el_popup_de_errores_esta_cargado_en_todas_las_pantallas(A):
    base = open("templates/base.html", encoding="utf-8").read()
    assert "js/form_errors.js" in base
    # Tiene que cargarse ANTES que confirm_move.js: su validación corre primero
    # y frena el envío antes de que se abra el modal de confirmación.
    assert base.index("js/form_errors.js") < base.index("js/confirm_move.js")


def test_las_filas_repetibles_estan_marcadas(A):
    """`data-confirm-row` es lo que hace que una fila vacía no se valide ni salga
    en el resumen. Si falta la marca, la fila vuelve a contar como llena."""
    for archivo in ("movements_bulk.html", "item_usage.html", "scrap_report.html",
                    "ingresos_egresos.html", "repair_requests.html"):
        html = open(f"templates/{archivo}", encoding="utf-8").read()
        assert "data-confirm-row" in html, f"{archivo} sin data-confirm-row"
