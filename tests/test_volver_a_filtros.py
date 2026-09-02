"""Volver a la MISMA vista filtrada después de un POST (mecanismo transversal).

Casi todas las pantallas de listado terminan sus POST con
`redirect(url_for("<listado>"))`, sin query string: cada guardado devolvía la
pantalla sin filtros, sin orden y en la página 1.

Las pantallas con formulario de filtros mandan la URL vigente en el campo
oculto `_filtros` (lo agrega `app.js`, o el template donde ya estaba) y el hook
`_volver_a_la_vista_filtrada` de app.py la repone.

Estas pruebas fijan las DOS mitades: que reponga cuando corresponde y —sobre
todo— que NO toque nada cuando no corresponde.
"""
from urllib.parse import urlparse

from conftest import login


def _ctx(A, filtros):
    """Contexto de un POST que trae `_filtros`."""
    return A.app.test_request_context(
        "/lo-que-sea", method="POST", data={"_filtros": filtros}
    )


# --------------------------------------------------------------------------
# El helper, caso por caso
# --------------------------------------------------------------------------

def test_repone_los_filtros_de_la_misma_pantalla(A):
    with _ctx(A, "/movements?item_id=3&page=2"):
        assert A.url_de_vuelta("/movements") == "/movements?item_id=3&page=2"


def test_funciona_tambien_con_un_location_absoluto(A):
    """Werkzeug lo emite relativo, pero un proxy puede reescribirlo."""
    with _ctx(A, "/movements?item_id=3"):
        assert (A.url_de_vuelta("http://stocks.local/movements")
                == "http://stocks.local/movements?item_id=3")


def test_no_toca_un_destino_de_otra_pantalla(A):
    with _ctx(A, "/movements?item_id=3"):
        assert A.url_de_vuelta("/remitos") == "/remitos"


def test_respeta_el_destino_que_ya_trae_parametros(A):
    """Si la ruta decidió a dónde va con parámetros, manda la ruta."""
    with _ctx(A, "/movements?item_id=3&page=2"):
        assert A.url_de_vuelta("/movements?page=1") == "/movements?page=1"


def test_sin_filtros_vigentes_no_hay_nada_que_reponer(A):
    with _ctx(A, "/movements?"):
        assert A.url_de_vuelta("/movements") == "/movements"
    with _ctx(A, ""):
        assert A.url_de_vuelta("/movements") == "/movements"


def test_no_acepta_urls_externas(A):
    for veneno in ("https://evil.example/movements?a=1",
                   "//evil.example/movements?a=1",
                   "http://evil.example",
                   "movements?a=1"):
        with _ctx(A, veneno):
            assert A.url_de_vuelta("/movements") == "/movements"


def test_no_acepta_saltos_de_linea_ni_urls_gigantes(A):
    with _ctx(A, "/movements?a=1\r\nX-Inyectado: 1"):
        assert A.url_de_vuelta("/movements") == "/movements"
    with _ctx(A, "/movements?" + "a=1&" * 900):
        assert A.url_de_vuelta("/movements") == "/movements"


def test_no_se_aplica_a_un_get(A, client):
    """El hook solo mira POSTs: un GET con querystring no se reescribe."""
    login(client, "admin", "admin123")
    r = client.get("/items?q=NEO")
    assert r.status_code == 200


# --------------------------------------------------------------------------
# De punta a punta, en una pantalla que no es Ítems
# --------------------------------------------------------------------------

def test_crear_un_proveedor_vuelve_a_la_vista_filtrada(A, client):
    login(client, "admin", "admin123")

    r = client.post("/proveedores", data={
        "contact_name": "Juan", "business_name": "Casa Juan",
        "_filtros": "/proveedores?inactivos=1",
    })

    assert r.status_code == 302
    assert r.headers["Location"] == "/proveedores?inactivos=1"


def test_sin_el_campo_el_comportamiento_es_el_de_antes(A, client):
    """Compatibilidad: un POST sin `_filtros` redirige como siempre."""
    login(client, "admin", "admin123")

    r = client.post("/proveedores", data={
        "contact_name": "Ana", "business_name": "Casa Ana",
    })

    assert urlparse(r.headers["Location"]).path == "/proveedores"
    assert urlparse(r.headers["Location"]).query == ""


def test_un_post_rechazado_tambien_vuelve_filtrado(A, client):
    """El error de validación no debería costar los filtros tampoco."""
    login(client, "admin", "admin123")

    r = client.post("/proveedores", data={
        "contact_name": "", "_filtros": "/proveedores?inactivos=1",
    })

    assert r.headers["Location"] == "/proveedores?inactivos=1"


# --------------------------------------------------------------------------
# El enganche del lado del navegador
# --------------------------------------------------------------------------

def test_el_js_agrega_el_campo_en_las_pantallas_con_filtros():
    """Si alguien saca esto de app.js, el mecanismo queda mudo sin avisar."""
    with open("static/js/app.js", encoding="utf-8") as fh:
        js = fh.read()

    assert "_filtros" in js
    assert "form.filters-grid, form.filters-row" in js


def test_las_pantallas_de_listado_conservan_el_form_de_filtros():
    """El JS se engancha por esas clases: si cambian, hay que actualizarlo."""
    pantallas = [
        "items", "movements", "remitos", "pending_deliveries", "scrap_report",
        "item_usage", "descartes", "costos_ingresos", "stock", "users",
        "suppliers", "reparaciones",
    ]
    for nombre in pantallas:
        with open(f"templates/{nombre}.html", encoding="utf-8") as fh:
            html = fh.read()
        assert 'class="filters-grid"' in html or 'class="filters-row"' in html, nombre
