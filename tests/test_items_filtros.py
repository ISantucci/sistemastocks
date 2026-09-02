"""Ítems: filtros que sobreviven al guardado, buscador por texto y baja lógica.

Cubre los tres cambios pedidos el 2026-09-02:

1. Guardar / crear / eliminar un ítem vuelve a la MISMA vista filtrada.
2. El buscador acepta texto libre (una palabra trae todos los que coincidan)
   además de permitir elegir un ítem puntual.
3. Los ítems inactivos no se listan salvo que se marque "Mostrar inactivos".

Nada de esto toca stock, movimientos ni permisos: son filtros de pantalla.
"""
import re
from io import BytesIO
from urllib.parse import urlparse, parse_qs

from conftest import make_item, make_category, login


def filas(body):
    """Solo el cuerpo de la tabla.

    El <select> del buscador lista TODO el catálogo a propósito (si no, no se
    podría cambiar de filtro), así que buscar un código en el HTML entero no
    dice nada sobre lo que la tabla está mostrando.
    """
    m = re.search(r"<tbody>(.*?)</tbody>", body, re.S)
    return m.group(1) if m else ""


def _setup(A, client):
    cat = make_category(A, "Cables", "CAB")
    otra = make_category(A, "Fuentes", "NEO")
    activo = make_item(A, code="CAB-001", name="Cable comun", category=cat)
    neo1 = make_item(A, code="NEO-001", name="Fuente NEO chica", category=otra)
    neo2 = make_item(A, code="NEO-002", name="Fuente NEO grande", category=otra)
    inactivo = make_item(A, code="CAB-009", name="Cable viejo", category=cat,
                         is_active=False)
    login(client, "admin", "admin123")
    return activo, neo1, neo2, inactivo


# --------------------------------------------------------------------------
# 1. Los filtros sobreviven al guardado
# --------------------------------------------------------------------------

FILTROS = "/items?category_id=1&sort_by=name&sort_dir=desc&page=2&limit=25&q=NEO"


def test_guardar_item_vuelve_a_la_vista_filtrada(A, client):
    activo, neo1, neo2, inactivo = _setup(A, client)

    r = client.post(f"/items/{activo.id}/edit", data={
        "name": "Cable comun", "trackable": "", "is_active": "on",
        "_filtros": FILTROS,
    })

    assert r.status_code == 302
    url = urlparse(r.headers["Location"])
    assert url.path == "/items"
    args = parse_qs(url.query)
    assert args["sort_by"] == ["name"]
    assert args["sort_dir"] == ["desc"]
    assert args["page"] == ["2"]
    assert args["limit"] == ["25"]
    assert args["q"] == ["NEO"]
    assert args["category_id"] == ["1"]


def test_guardar_sin_filtros_vuelve_a_items_pelado(A, client):
    """Comportamiento anterior intacto cuando no hay filtros vigentes."""
    activo, neo1, neo2, inactivo = _setup(A, client)

    r = client.post(f"/items/{activo.id}/edit", data={
        "name": "Cable comun", "is_active": "on", "_filtros": "",
    })

    assert r.status_code == 302
    assert urlparse(r.headers["Location"]).path == "/items"
    assert urlparse(r.headers["Location"]).query == ""


def test_los_filtros_de_otra_pantalla_no_se_aplican(A, client):
    """Solo se repone si el destino es exactamente la pantalla de origen."""
    activo, neo1, neo2, inactivo = _setup(A, client)

    r = client.post(f"/items/{activo.id}/edit", data={
        "name": "Cable comun", "is_active": "on",
        "_filtros": "/movements?item_id=1&page=5",
    })

    assert r.headers["Location"] == "/items"


def test_el_campo_de_filtros_no_puede_cambiar_el_destino(A, client):
    """El path y el host los pone url_for: no hay redirect abierto posible."""
    activo, neo1, neo2, inactivo = _setup(A, client)

    for veneno in ("https://evil.example/items?q=x", "//evil.example/items?q=x",
                   "/admin?q=x", "http://evil.example", "items?q=x",
                   "/items?q=x\r\nX-Inyectado: 1", "/items?" + "a=1&" * 900):
        r = client.post(f"/items/{activo.id}/edit", data={
            "name": "Cable comun", "is_active": "on", "_filtros": veneno,
        })
        destino = r.headers["Location"]
        assert urlparse(destino).netloc == ""
        assert urlparse(destino).path == "/items"
        assert "X-Inyectado" not in destino
        assert "evil.example" not in destino


def test_guardar_no_pierde_el_valor_de_los_campos_del_item(A, client):
    """El campo oculto de filtros no debe pisar los campos del formulario.

    `trackable` y `category_id` son a la vez filtros de la pantalla y campos
    del ítem: por eso los filtros viajan en UN solo campo (`_filtros`) y no
    como inputs sueltos con el mismo nombre.
    """
    activo, neo1, neo2, inactivo = _setup(A, client)

    client.post(f"/items/{activo.id}/edit", data={
        "name": "Cable comun", "trackable": "on", "is_active": "on",
        "_filtros": "/items?trackable=0&category_id=999",
    })

    with A.app.app_context():
        it = A.Item.query.get(activo.id)
        assert it.trackable is True          # ganó el checkbox, no el filtro
        assert it.category_id == activo.category_id


def test_eliminar_item_conserva_los_filtros(A, client):
    activo, neo1, neo2, inactivo = _setup(A, client)

    r = client.post(f"/items/{neo2.id}/delete", data={"_filtros": "/items?sort_by=name&page=3"})

    assert r.status_code == 302
    args = parse_qs(urlparse(r.headers["Location"]).query)
    assert args["sort_by"] == ["name"]
    assert args["page"] == ["3"]


def test_alta_por_ajax_devuelve_el_destino_filtrado(A, client):
    activo, neo1, neo2, inactivo = _setup(A, client)
    cat = A.Category.query.filter_by(name="Cables").first()

    r = client.post("/items/new", data={
        "name": "Cable nuevo", "category_id": str(cat.id),
        "_filtros": "/items?sort_by=name&limit=50",
    }, headers={"X-Requested-With": "XMLHttpRequest"})

    assert r.status_code == 200
    destino = r.get_json()["redirect"]
    assert destino.startswith("/items?")
    args = parse_qs(urlparse(destino).query)
    assert args["sort_by"] == ["name"]
    assert args["limit"] == ["50"]


# --------------------------------------------------------------------------
# 2. Buscador por texto libre
# --------------------------------------------------------------------------

def test_una_palabra_trae_todos_los_que_coinciden(A, client):
    activo, neo1, neo2, inactivo = _setup(A, client)

    tabla = filas(client.get("/items?q=NEO").get_data(as_text=True))

    assert "NEO-001" in tabla
    assert "NEO-002" in tabla
    assert "CAB-001" not in tabla


def test_la_busqueda_por_texto_no_distingue_mayusculas(A, client):
    activo, neo1, neo2, inactivo = _setup(A, client)

    tabla = filas(client.get("/items?q=neo").get_data(as_text=True))

    assert "NEO-001" in tabla and "NEO-002" in tabla


def test_elegir_un_item_puntual_sigue_filtrando_a_uno(A, client):
    activo, neo1, neo2, inactivo = _setup(A, client)

    tabla = filas(client.get("/items?q=NEO-001").get_data(as_text=True))

    assert "NEO-001" in tabla
    assert "NEO-002" not in tabla


def test_el_buscador_es_un_select_por_codigo_con_texto_libre(A, client):
    """La estética se mantiene (sigue siendo el select buscable de siempre)."""
    activo, neo1, neo2, inactivo = _setup(A, client)

    body = client.get("/items").get_data(as_text=True)

    assert 'name="q"' in body
    assert "js-buscar-libre" in body
    assert '<option value="NEO-001"' in body      # el value es el código


def test_el_texto_libre_buscado_queda_visible_en_el_control(A, client):
    activo, neo1, neo2, inactivo = _setup(A, client)

    body = client.get("/items?q=NEO").get_data(as_text=True)

    assert '<option value="NEO" selected>NEO</option>' in body


# --------------------------------------------------------------------------
# 3. Inactivos
# --------------------------------------------------------------------------

def test_por_defecto_no_se_listan_inactivos(A, client):
    activo, neo1, neo2, inactivo = _setup(A, client)

    body = client.get("/items").get_data(as_text=True)

    assert "CAB-001" in body
    assert inactivo.code not in body


def test_con_el_tilde_se_listan_los_inactivos(A, client):
    activo, neo1, neo2, inactivo = _setup(A, client)

    body = client.get("/items?show_inactive=1").get_data(as_text=True)

    assert inactivo.code in body


def test_el_inactivo_no_aparece_ni_eligiendolo_a_mano(A, client):
    """Sin el tilde no se ve, aunque se busque por su código exacto."""
    activo, neo1, neo2, inactivo = _setup(A, client)

    sin_tilde = client.get(f"/items?q={inactivo.code}").get_data(as_text=True)
    con_tilde = client.get(
        f"/items?q={inactivo.code}&show_inactive=1"
    ).get_data(as_text=True)

    assert "Sin ítems." in sin_tilde
    assert inactivo.name not in sin_tilde
    assert inactivo.name in con_tilde


def test_el_tilde_aparece_en_la_pantalla_y_arranca_apagado(A, client):
    activo, neo1, neo2, inactivo = _setup(A, client)

    body = client.get("/items").get_data(as_text=True)

    assert 'name="show_inactive"' in body
    assert "Mostrar inactivos" in body
    assert 'name="show_inactive" value="1" checked' not in body


def test_el_orden_y_la_paginacion_conservan_el_tilde(A, client):
    activo, neo1, neo2, inactivo = _setup(A, client)

    body = client.get("/items?show_inactive=1&sort_by=name").get_data(as_text=True)

    assert "show_inactive=1" in body


def test_el_export_respeta_el_filtro_de_inactivos(A, client):
    activo, neo1, neo2, inactivo = _setup(A, client)
    openpyxl = __import__("openpyxl")

    def codigos(url):
        r = client.get(url)
        assert r.status_code == 200
        ws = openpyxl.load_workbook(BytesIO(r.data)).active
        return {c.value for c in ws["A"]}

    assert inactivo.code not in codigos("/items/export.xlsx")
    assert inactivo.code in codigos("/items/export.xlsx?show_inactive=1")
