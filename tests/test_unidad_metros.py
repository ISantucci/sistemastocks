# -*- coding: utf-8 -*-
"""La unidad de medida tiene que verse también al CARGAR, no sólo al leer.

El problema real: un cable se mide en metros, pero el selector decía
"CAB-001 - Cable UTP" y el campo de al lado decía "Cantidad", igual que para
una cámara. Quien cargaba 300 metros escribía "300" en un campo que no le
aclaraba qué estaba contando, y después eso se leía como 300 unidades.

Estos tests cubren el lado del servidor: que la lista de ítems en metros salga
bien, que la API la incluya, que los formularios traigan el cartel declarado y
que los exports digan la unidad. La parte de navegador (mostrar y ocultar el
cartel al cambiar de ítem) vive en static/js/unit_hint.js.
"""
import csv
import io

from conftest import make_user, make_item, make_category, make_location, login


def _items(A):
    """Un ítem en metros y uno en unidades, que es el caso que hay que separar."""
    cat = make_category(A)
    cable = make_item(A, code="CAB-001", name="Cable UTP", category=cat, stock_min=100)
    camara = make_item(A, code="CAM-001", name="Camara domo", category=cat, stock_min=2)
    cable.unit = "metros"
    camara.unit = "unidad"
    A.db.session.commit()
    return cable, camara


def _admin(A, client):
    make_user(A, "adm", "ADMIN")
    login(client, "adm")


# ------------------------------------------------------------------ backend

def test_items_en_metros_devuelve_solo_los_de_metros(A, client):
    cable, camara = _items(A)
    _admin(A, client)
    with A.app.test_request_context():
        # Necesita un usuario logueado: se resuelve por la sesión del cliente.
        pass
    html = client.get("/movements").get_data(as_text=True)
    assert f"window.TNG_ITEMS_METROS = [{cable.id}]" in html
    assert str(camara.id) not in html.split("TNG_ITEMS_METROS = ")[1].split("]")[0]


def test_sin_sesion_no_se_filtra_el_catalogo(A):
    """La lista viaja en el HTML: para un anónimo tiene que ser vacía."""
    _items(A)
    with A.app.test_request_context():
        assert A.items_en_metros() == []


def test_la_busqueda_remota_de_items_dice_la_unidad(A, client):
    """Con catálogos grandes el <option> lo crea el navegador desde esta API:
    si la unidad no viaja acá, el cartel no puede aparecer."""
    _items(A)
    _admin(A, client)
    filas = client.get("/api/items/search?q=CAB").get_json()
    assert filas and filas[0]["code"] == "CAB-001"
    assert filas[0]["unit"] == "metros"
    filas = client.get("/api/items/search?q=CAM").get_json()
    assert filas[0]["unit"] == "unidad"


def test_item_unit_name(A):
    cable, camara = _items(A)
    assert A.item_unit_name(cable) == "metros"
    assert A.item_unit_name(camara) == "unidad"
    assert A.item_unit_name(None) == "unidad"


def test_fmt_qty_no_cambio(A):
    """Regresión: el formato de lectura es el de siempre."""
    cable, camara = _items(A)
    assert A.fmt_qty(300, cable) == "300 metros"
    assert A.fmt_qty(5, camara) == "5"
    assert A.fmt_qty(5, None) == "5"


# ------------------------------------------------- formularios de carga/consumo

FORMULARIOS = [
    ("/movements", "item_id"),            # movimiento simple
    ("/movements/bulk", "item_id[]"),     # carga múltiple
    ("/ingresos-egresos", "item_id[]"),   # ingreso / egreso
    ("/item-usage", "item_id[]"),         # utilizados (consumo)
    ("/admin/adjust-stock", "item_id"),   # ajuste administrativo
]


def test_los_formularios_declaran_el_cartel_de_unidad(A, client):
    """Cada pantalla donde se escribe una cantidad tiene que decir de qué ítem
    depende esa unidad. Si alguien agrega un formulario nuevo y se olvida, este
    test no lo detecta: detecta que los que ya están no se rompan."""
    _items(A)
    _admin(A, client)
    for url, name in FORMULARIOS:
        html = client.get(url).get_data(as_text=True)
        assert f'data-unit-hint="{name}"' in html, f"{url} perdió el cartel de unidad"


def test_la_devolucion_de_un_pendiente_hereda_la_unidad(A, client):
    """Si el pendiente devuelve el mismo ítem, el select queda vacío y la unidad
    tiene que salir del ítem del movimiento."""
    _items(A)
    _admin(A, client)
    html = client.get("/movements").get_data(as_text=True)
    assert 'data-unit-hint="pending_return_item_id,item_id"' in html


def test_el_front_carga_el_script_de_unidades(A, client):
    _items(A)
    _admin(A, client)
    html = client.get("/stock").get_data(as_text=True)
    assert "js/unit_hint.js" in html
    assert html.index("TNG_ITEMS_METROS") < html.index("js/unit_hint.js")


# ------------------------------------------------------------------ lecturas

def test_el_catalogo_marca_los_items_en_metros(A, client):
    _items(A)
    _admin(A, client)
    html = client.get("/items").get_data(as_text=True)
    assert "(en metros)" in html
    # Y el stock mínimo del cable se lee con su unidad, no como piezas.
    assert "100 metros" in html


def test_stock_marca_la_unidad_en_el_nombre_y_en_la_cantidad(A, client):
    cable, _ = _items(A)
    jaula = make_location(A, "Jaula")
    A.db.session.add(A.Stock(item_id=cable.id, location_id=jaula.id, quantity=300))
    A.db.session.commit()
    _admin(A, client)
    html = client.get("/stock").get_data(as_text=True)
    assert "(en metros)" in html
    assert "300 metros" in html


# ------------------------------------------------------------------- exports

def test_el_csv_de_stock_trae_la_unidad_sin_mover_las_columnas(A, client):
    cable, camara = _items(A)
    jaula = make_location(A, "Jaula")
    A.db.session.add_all([
        A.Stock(item_id=cable.id, location_id=jaula.id, quantity=300),
        A.Stock(item_id=camara.id, location_id=jaula.id, quantity=5),
    ])
    A.db.session.commit()
    _admin(A, client)

    texto = client.get("/stock/export.csv").get_data(as_text=True)
    filas = list(csv.reader(io.StringIO(texto.lstrip("﻿"))))
    cabecera = filas[0]

    # Compatibilidad: las columnas viejas siguen en la MISMA posición.
    assert cabecera[:7] == ["ubicacion", "codigo_item", "nombre_item",
                            "categoria", "rastreable", "descripcion", "cantidad"]
    assert cabecera[7] == "unidad"

    por_codigo = {f[1]: f for f in filas[1:]}
    assert por_codigo["CAB-001"][6] == "300"
    assert por_codigo["CAB-001"][7] == "metros"
    assert por_codigo["CAM-001"][7] == "unidad"


def test_el_csv_de_movimientos_trae_la_unidad(A, client):
    cable, _ = _items(A)
    origen = make_location(A, "Proveedor")
    destino = make_location(A, "Jaula")
    adm = A.User.query.filter_by(username="adm").first() or make_user(A, "adm", "ADMIN")
    A.db.session.add(A.Movement(
        item_id=cable.id, qty=300, from_location_id=origen.id,
        to_location_id=destino.id, user_id=adm.id, number="MOV-TEST-1",
    ))
    A.db.session.commit()
    login(client, "adm")

    texto = client.get("/movements/export.csv").get_data(as_text=True)
    filas = list(csv.reader(io.StringIO(texto.lstrip("﻿"))))
    assert filas[0][:9] == ["fecha", "hora", "item_codigo", "item_nombre", "cantidad",
                            "desde", "hacia", "responsable", "observacion"]
    assert filas[0][9] == "unidad"
    assert filas[1][4] == "300"
    assert filas[1][9] == "metros"
