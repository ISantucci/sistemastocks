"""Consumir ítems serializados desde Utilizados y desde Descartes.

Hasta ahora las dos pantallas rechazaban los ítems serializados y mandaban al
usuario a Movimientos para poder elegir los seriales. Eso obligaba a salir de la
pantalla que se llama justo para eso ("se instaló un equipo, pasalo a
utilizado").

Ahora las tres pantallas usan la MISMA regla auto/elegir y el serial queda en la
observación del movimiento, que es lo que después se lee en el historial.

Estas pruebas cubren sobre todo lo que NO tiene que pasar: que un serial de otra
ubicación, de otro ítem o en cantidad equivocada no se consuma, y que un rechazo
no mueva stock ni deje registros a medias.
"""
import pytest
from conftest import make_user, make_item, make_category, make_location, login


@pytest.fixture()
def esc(A):
    """Una cámara serializada con 3 seriales en Jaula y 1 en otro depósito."""
    A.app.config["WTF_CSRF_ENABLED"] = False
    cat = make_category(A, "Camaras", "CAM")

    jaula = make_location(A, "Jaula TNG")
    dep = make_location(A, "Deposito 2")

    cam = make_item(A, code="CAM-001", name="Camara domo", category=cat)
    otra = make_item(A, code="CAM-009", name="Otra camara", category=cat)
    cable = make_item(A, code="CAM-050", name="Cable UTP", category=cat)
    cam.serialized = True
    otra.serialized = True
    A.db.session.commit()

    def unidad(item, serial, loc):
        u = A.ItemUnit(item_id=item.id, serial=serial,
                       status=A.UNIT_EN_STOCK, location_id=loc.id)
        A.db.session.add(u)
        return u

    for s in ("SN-A", "SN-B", "SN-C"):
        unidad(cam, s, jaula)
    unidad(cam, "SN-DEP", dep)
    unidad(otra, "OTRA-1", jaula)

    A.upsert_stock(cam.id, jaula.id, 3)
    A.upsert_stock(cam.id, dep.id, 1)
    A.upsert_stock(otra.id, jaula.id, 1)
    A.upsert_stock(cable.id, jaula.id, 10)
    A.db.session.commit()

    def uid(serial):
        return A.ItemUnit.query.filter_by(serial=serial).first().id

    return {"cam": cam, "otra": otra, "cable": cable, "jaula": jaula, "dep": dep,
            "uid": uid}


def _estado(A, serial):
    u = A.ItemUnit.query.filter_by(serial=serial).first()
    return (u.status, u.location_id)


def _stock(A, item_id, loc_id):
    row = A.Stock.query.filter_by(item_id=item_id, location_id=loc_id).first()
    return row.quantity if row else 0


# --------------------------------------------------------------------------
# Utilizados
# --------------------------------------------------------------------------

def test_utilizado_consume_el_serial_elegido(A, client, esc):
    login(client, "admin", "admin123")

    client.post("/item-usage", data={
        "from_location_id": str(esc["jaula"].id),
        "observation": "instalado en obra Palermo",
        "item_id[]": str(esc["cam"].id), "qty[]": "1",
        "unit_ids[]": str(esc["uid"]("SN-B")),
    })

    assert _estado(A, "SN-B") == ("ENTREGADO", None)
    assert _estado(A, "SN-A")[0] == "EN_STOCK"
    assert _stock(A, esc["cam"].id, esc["jaula"].id) == 2


def test_el_serial_queda_en_la_observacion_del_movimiento(A, client, esc):
    """Es lo que después se lee en el historial de Movimientos."""
    login(client, "admin", "admin123")

    client.post("/item-usage", data={
        "from_location_id": str(esc["jaula"].id),
        "observation": "instalado en obra Palermo",
        "item_id[]": str(esc["cam"].id), "qty[]": "1",
        "unit_ids[]": str(esc["uid"]("SN-B")),
    })

    m = A.Movement.query.order_by(A.Movement.id.desc()).first()
    assert "instalado en obra Palermo" in m.observation
    assert "SN-B" in m.observation
    assert m.to_location.name == "Utilizado"


def test_sin_observacion_igual_queda_el_serial(A, client, esc):
    login(client, "admin", "admin123")

    client.post("/item-usage", data={
        "from_location_id": str(esc["jaula"].id),
        "item_id[]": str(esc["cam"].id), "qty[]": "1",
        "unit_ids[]": str(esc["uid"]("SN-A")),
    })

    m = A.Movement.query.order_by(A.Movement.id.desc()).first()
    assert "SN-A" in m.observation


def test_si_hay_tantos_seriales_como_la_cantidad_se_resuelven_solos(A, client, esc):
    """Misma regla que Movimientos: no se le pide elegir lo obvio."""
    login(client, "admin", "admin123")

    client.post("/item-usage", data={
        "from_location_id": str(esc["jaula"].id),
        "item_id[]": str(esc["cam"].id), "qty[]": "3",
        "unit_ids[]": "",
    })

    for s in ("SN-A", "SN-B", "SN-C"):
        assert _estado(A, s) == ("ENTREGADO", None)
    assert _stock(A, esc["cam"].id, esc["jaula"].id) == 0


def test_multifila_mezclando_serializado_y_comun(A, client, esc):
    login(client, "admin", "admin123")

    r = client.post("/item-usage", data={
        "from_location_id": str(esc["jaula"].id),
        "item_id[]": [str(esc["cam"].id), str(esc["cable"].id)],
        "qty[]": ["1", "2"],
        "unit_ids[]": [str(esc["uid"]("SN-C")), ""],
    }, follow_redirects=True)

    assert "Movimientos creados: 2" in r.get_data(as_text=True)
    assert _estado(A, "SN-C") == ("ENTREGADO", None)
    assert _stock(A, esc["cable"].id, esc["jaula"].id) == 8


@pytest.mark.parametrize("elegido,caso", [
    ("", "no eligió ninguno"),
    ("999999", "id inexistente"),
    ("SN-DEP", "serial de otra ubicación"),
    ("OTRA-1", "serial de otro ítem"),
])
def test_una_eleccion_invalida_no_consume_nada(A, client, esc, elegido, caso):
    login(client, "admin", "admin123")
    valor = esc["uid"](elegido) if elegido.startswith("SN-") or elegido.startswith("OTRA") else elegido

    r = client.post("/item-usage", data={
        "from_location_id": str(esc["jaula"].id),
        "item_id[]": str(esc["cam"].id), "qty[]": "1",
        "unit_ids[]": str(valor),
    }, follow_redirects=True)

    assert "elegí exactamente 1" in r.get_data(as_text=True), caso
    assert A.ItemUnit.query.filter(A.ItemUnit.status != "EN_STOCK").count() == 0, caso
    assert _stock(A, esc["cam"].id, esc["jaula"].id) == 3, caso
    assert A.Movement.query.count() == 0, caso


def test_elegir_menos_de_los_necesarios_se_rechaza(A, client, esc):
    login(client, "admin", "admin123")

    r = client.post("/item-usage", data={
        "from_location_id": str(esc["jaula"].id),
        "item_id[]": str(esc["cam"].id), "qty[]": "2",
        "unit_ids[]": str(esc["uid"]("SN-A")),
    }, follow_redirects=True)

    assert "elegí exactamente 2" in r.get_data(as_text=True)
    assert A.Movement.query.count() == 0


def test_una_linea_invalida_no_deja_pasar_las_otras(A, client, esc):
    """Todo-o-nada: la línea común tampoco entra."""
    login(client, "admin", "admin123")

    client.post("/item-usage", data={
        "from_location_id": str(esc["jaula"].id),
        "item_id[]": [str(esc["cam"].id), str(esc["cable"].id)],
        "qty[]": ["1", "2"],
        "unit_ids[]": ["", ""],
    })

    assert A.Movement.query.count() == 0
    assert _stock(A, esc["cable"].id, esc["jaula"].id) == 10


def test_el_serializado_ya_aparece_en_la_pantalla(A, client, esc):
    login(client, "admin", "admin123")
    body = client.get("/item-usage").get_data(as_text=True)

    assert "CAM-001" in body
    assert "unit_ids[]" in body
    assert "Cargalo desde Movimientos" not in body


# --------------------------------------------------------------------------
# Descartes
# --------------------------------------------------------------------------

def test_descarte_consume_el_serial_y_registra_el_scrap(A, client, esc):
    login(client, "admin", "admin123")

    client.post("/scrap", data={
        "from_location_id": str(esc["jaula"].id),
        "item_id[]": str(esc["cam"].id), "qty[]": "1",
        "scrap_reason[]": "Roto",
        "unit_ids[]": str(esc["uid"]("SN-C")),
    })

    assert _estado(A, "SN-C") == ("DESCARTADO", None)
    assert _stock(A, esc["cam"].id, esc["jaula"].id) == 2
    scrap = A.Scrap.query.one()
    assert (scrap.reason, scrap.quantity) == ("Roto", 1)
    m = A.Movement.query.order_by(A.Movement.id.desc()).first()
    assert "SN-C" in m.observation


def test_descarte_rechazado_no_deja_scrap_ni_mueve_stock(A, client, esc):
    login(client, "admin", "admin123")

    r = client.post("/scrap", data={
        "from_location_id": str(esc["jaula"].id),
        "item_id[]": str(esc["cam"].id), "qty[]": "1",
        "scrap_reason[]": "Roto", "unit_ids[]": "",
    }, follow_redirects=True)

    assert "elegí exactamente 1" in r.get_data(as_text=True)
    assert A.Scrap.query.count() == 0
    assert _stock(A, esc["cam"].id, esc["jaula"].id) == 3
    assert A.ItemUnit.query.filter(A.ItemUnit.status != "EN_STOCK").count() == 0


def test_el_serializado_ya_aparece_en_descartes(A, client, esc):
    login(client, "admin", "admin123")
    body = client.get("/scrap").get_data(as_text=True)

    assert "unit_ids[]" in body
    assert "Cargalo desde Movimientos" not in body


# --------------------------------------------------------------------------
# Alcance del TÉCNICO
# --------------------------------------------------------------------------

def test_el_tecnico_no_ve_los_seriales_de_otra_ubicacion(A, client, esc):
    """El mapa de seriales viaja en el HTML: lo que no le toca, no viaja."""
    tec = make_user(A, "tec", "TECNICO")
    truck = make_location(A, "Camioneta A", is_truck=True)
    A.db.session.add(A.LocationResponsible(location_id=truck.id, user_id=tec.id))
    A.db.session.add(A.ItemUnit(item_id=esc["cam"].id, serial="SN-TRUCK",
                                status=A.UNIT_EN_STOCK, location_id=truck.id))
    A.upsert_stock(esc["cam"].id, truck.id, 1)
    A.db.session.commit()

    login(client, "tec", "pass1234")
    body = client.get("/item-usage").get_data(as_text=True)

    assert "SN-TRUCK" in body
    assert "SN-A" not in body      # los de la Jaula no son suyos


def test_el_tecnico_no_puede_consumir_un_serial_ajeno_por_post_directo(A, client, esc):
    tec = make_user(A, "tec", "TECNICO")
    truck = make_location(A, "Camioneta A", is_truck=True)
    A.db.session.add(A.LocationResponsible(location_id=truck.id, user_id=tec.id))
    A.db.session.commit()

    login(client, "tec", "pass1234")
    client.post("/item-usage", data={
        "from_location_id": str(esc["jaula"].id),      # no es suya
        "item_id[]": str(esc["cam"].id), "qty[]": "1",
        "unit_ids[]": str(esc["uid"]("SN-A")),
    })

    assert _estado(A, "SN-A")[0] == "EN_STOCK"
    assert A.Movement.query.count() == 0


# --------------------------------------------------------------------------
# Regresión: Movimientos no cambió
# --------------------------------------------------------------------------

def test_movimientos_sigue_moviendo_seriales_igual(A, client, esc):
    login(client, "admin", "admin123")

    client.post("/movements", data={
        "item_id": str(esc["cam"].id), "qty": "1",
        "from_location_id": str(esc["jaula"].id),
        "to_location_id": str(esc["dep"].id),
        "unit_id": str(esc["uid"]("SN-A")),
    })

    assert _estado(A, "SN-A") == ("EN_STOCK", esc["dep"].id)
    m = A.Movement.query.order_by(A.Movement.id.desc()).first()
    assert "SN-A" in m.observation


def test_el_selector_de_seriales_sigue_enganchado_en_las_tres_pantallas():
    """Guarda: si alguien cambia las clases, el selector queda mudo sin avisar."""
    with open("static/js/serial_picker.js", encoding="utf-8") as fh:
        js = fh.read()
    assert "idsInput" in js and "summaryInput" in js

    for nombre in ("item_usage", "scrap_report"):
        with open(f"templates/{nombre}.html", encoding="utf-8") as fh:
            html = fh.read()
        assert 'name="unit_ids[]"' in html, nombre
        assert "serial-pick-list" in html, nombre
        assert "initSerialPicker" in html, nombre

    # Movimientos sigue mandando los seriales por los checkboxes (unit_id),
    # pero el modal ahora los muestra por el resumen en texto.
    with open("templates/movements.html", encoding="utf-8") as fh:
        movs = fh.read()
    assert "summaryInput" in movs
    assert 'id="serial_summary"' in movs


def test_el_modal_muestra_los_seriales_en_las_tres_pantallas(A, client, esc):
    """El resumen tiene que ser un input VISIBLE: confirm_move.js saltea los
    ocultos y de un checkbox sólo sabría decir "Sí"."""
    login(client, "admin", "admin123")

    for ruta, marca in (("/item-usage", 'class="serial-summary"'),
                        ("/scrap", 'class="serial-summary"'),
                        ("/movements", 'id="serial_summary"')):
        html = client.get(ruta).get_data(as_text=True)
        assert marca in html, ruta
        # El campo tiene que ser de texto, no oculto: si alguien lo pasa a
        # hidden, el modal deja de decir qué seriales salen y nadie se entera.
        trozo = html[html.index(marca) - 120: html.index(marca) + 40]
        assert 'type="text"' in trozo, ruta


def test_el_popup_de_seriales_cierra_sin_navegar_el_historial():
    """El popup de la ficha de Seriales tiene que cerrar al PRIMER clic afuera.

    Cerraba con `history.back()`, y como su contenido vive en un iframe, cada
    operación adentro (cargar un serial) empuja su propia entrada en el
    historial de la pestaña: el back() retrocedía una operación DENTRO del popup
    en vez de cerrarlo. El síntoma era un "control z" y hacía falta un clic por
    cada cosa hecha adentro. Sólo se veía en Seriales, la única ficha donde se
    opera.

    Si alguien vuelve a poner history.back() acá, vuelve el bug.
    """
    with open("templates/base.html", encoding="utf-8") as fh:
        html = fh.read()

    bloque = html[html.index("function dismissDetail"):]
    bloque = bloque[:bloque.index("window.addEventListener('popstate'")]

    assert "history.back()" not in bloque
    assert "closeDetail();" in bloque
    assert "history.replaceState" in bloque
    # El botón "atrás" del navegador sigue cerrando el popup abierto.
    assert "popstate" in html and "closeDetail()" in html
