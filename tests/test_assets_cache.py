# -*- coding: utf-8 -*-
"""Assets del front: de dónde salen y cómo se cachean.

Cubre los tres arreglos del bloque de assets:

  1. Ninguna pantalla depende de un CDN externo ni de Google Fonts.
  2. Cada URL de /static lleva ?v=<hash>, y sólo con esa marca se sirve con
     cache largo. Sin la marca, el cache queda como estaba.
  3. El HTML nunca se guarda en el navegador (son datos de stock).

Lo que se prueba acá NO es cosmético: si el ?v= se pierde, el cache de un año
se vuelve una trampa (CSS viejo con HTML nuevo después de cada deploy).
"""
import re

from conftest import make_user, login


ORIGENES_EXTERNOS = (
    "cdn.jsdelivr.net",
    "cdnjs.cloudflare.com",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
)

# Pantallas representativas: las que cargan Tom Select, las que cargan Chart.js
# y las que no cargan nada de terceros.
PANTALLAS = ["/", "/stock", "/movements", "/movements/bulk", "/ingresos-egresos",
             "/items", "/metricas", "/remitos", "/item-usage"]


def _admin(A, client):
    make_user(A, "adm", "ADMIN")
    login(client, "adm")


def test_ninguna_pantalla_pide_nada_a_un_cdn(A, client):
    """El sistema es interno: tiene que poder abrir sin salida a Internet."""
    _admin(A, client)
    for url in PANTALLAS:
        html = client.get(url).get_data(as_text=True)
        for origen in ORIGENES_EXTERNOS:
            assert origen not in html, f"{url} todavía pide algo a {origen}"


def test_la_tipografia_es_local(A, client):
    _admin(A, client)
    html = client.get("/stock").get_data(as_text=True)
    assert "vendor/inter/" in html
    # Y el archivo existe de verdad, no es sólo una URL escrita.
    r = client.get("/static/vendor/inter/5.0.17/files/inter-latin-400-normal.woff2")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "font/woff2"


def test_tom_select_y_chartjs_salen_de_static(A, client):
    _admin(A, client)
    html = client.get("/metricas").get_data(as_text=True)
    assert "/static/vendor/chartjs/" in html
    html = client.get("/movements").get_data(as_text=True)
    assert "/static/vendor/tom-select" in html


def test_la_csp_ya_no_habilita_origenes_externos(A, client):
    """Con todo local, la CSP se cierra sola. Si alguien vuelve a meter un CDN,
    esto falla y obliga a decidirlo a propósito."""
    _admin(A, client)
    csp = client.get("/stock").headers.get("Content-Security-Policy-Report-Only", "")
    assert csp, "la CSP dejó de mandarse"
    for origen in ORIGENES_EXTERNOS:
        assert origen not in csp


def test_todo_static_lleva_version_en_la_url(A, client):
    _admin(A, client)
    html = client.get("/stock").get_data(as_text=True)
    urls = re.findall(r'(?:href|src)="(/static/[^"]+)"', html)
    assert urls, "no se encontró ninguna URL de /static"
    sin_version = [u for u in urls if "?v=" not in u]
    assert not sin_version, f"URLs de static sin versión: {sin_version}"


def test_static_con_version_se_cachea_un_anio(A, client):
    _admin(A, client)
    html = client.get("/stock").get_data(as_text=True)
    url = re.search(r'/static/css/app\.css\?v=[0-9a-f]+', html).group(0)
    cc = client.get(url).headers["Cache-Control"]
    assert "max-age=31536000" in cc
    assert "immutable" in cc


def test_los_estaticos_no_varian_por_cookie(A, client):
    """Sin esto el cache largo NO SIRVE, y no se nota mirando el código.

    Se probó con un navegador real: con "Vary: Cookie" puesto, los 16 archivos
    del front se volvían a pedir en CADA pantalla aunque la respuesta dijera
    "immutable". El navegador entiende que la respuesta depende de la cookie y
    prefiere preguntar de nuevo. Sacarla dejó los pedidos en cero a partir de
    la segunda pantalla.

    La cabecera la agrega Flask al cerrar la respuesta (después de todos los
    after_request), así que se saca en un middleware WSGI. Si alguien lo quita
    creyendo que sobra, este test avisa.
    """
    _admin(A, client)
    html = client.get("/stock").get_data(as_text=True)
    url = re.search(r'/static/css/app\.css\?v=[0-9a-f]+', html).group(0)
    vary = client.get(url).headers.get("Vary", "")
    assert "cookie" not in vary.lower(), (
        f"volvió el Vary: Cookie en /static ({vary!r}): el cache deja de funcionar"
    )


def test_una_pantalla_si_varia_por_cookie(A, client):
    """El contrapeso del test de arriba: en el HTML, que depende del usuario
    logueado, el Vary tiene que seguir estando. El middleware sólo puede tocar
    /static."""
    _admin(A, client)
    assert "cookie" in client.get("/stock").headers.get("Vary", "").lower()


def test_la_tipografia_se_baja_una_sola_vez_por_peso(A, client):
    """El @font-face pide el .woff2 con ?v=5017. Si alguien agrega un
    <link rel=preload> apuntando a otra URL (por ejemplo con el ?v= que genera
    url_for), el navegador baja el MISMO archivo dos veces."""
    css = client.get("/static/vendor/inter/5.0.17/inter.css").get_data(as_text=True)
    # Se cuentan las urls reales del @font-face, no las menciones del comentario.
    assert css.count("?v=5017) format") == 8, "las urls del @font-face perdieron su versión"
    html = client.get("/stock").get_data(as_text=True)
    assert 'rel="preload"' not in html, (
        "volvió el preload de la fuente: revisá que apunte a la misma URL que el @font-face"
    )


def test_static_sin_version_no_se_cachea_largo(A, client):
    """Una URL vieja (un favorito, un link pegado) tiene que seguir andando y
    NO quedarse pegada un año."""
    r = client.get("/static/css/app.css")
    assert r.status_code == 200
    assert "31536000" not in r.headers.get("Cache-Control", "")


def test_si_cambia_el_archivo_cambia_la_version(A, tmp_path, monkeypatch):
    """El corazón del cache-busting: mismo contenido, misma URL; contenido
    distinto, URL distinta. Sin esto, el cache largo es una bomba de tiempo.

    Se trabaja sobre un /static de mentira (tmp_path) para no escribir dentro
    del repo: un test no tiene por qué dejar archivos en static/.
    """
    import os

    falso_static = tmp_path / "static" / "css"
    falso_static.mkdir(parents=True)
    archivo = falso_static / "prueba.css"
    archivo.write_text("a{color:red}", encoding="utf-8")

    monkeypatch.setattr(A, "BASE_DIR", tmp_path)
    A._ASSET_VERSION_CACHE.clear()

    v1 = A._asset_version("css/prueba.css")
    v2 = A._asset_version("css/prueba.css")
    assert v1 and v1 == v2, "el mismo archivo cambió de versión sin cambiar"

    archivo.write_text("a{color:blue}", encoding="utf-8")
    # Se fuerza el mtime: en Windows la resolución puede ser gruesa y dos
    # escrituras seguidas quedarían con la misma marca de tiempo.
    st = archivo.stat()
    os.utime(archivo, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    v3 = A._asset_version("css/prueba.css")
    assert v3 != v1, "cambió el contenido y la versión quedó igual"

    A._ASSET_VERSION_CACHE.clear()


def test_un_static_que_no_existe_no_rompe_la_url(A):
    """Si falta el archivo, url_for no tiene que explotar: devuelve la URL pelada,
    igual que antes de todo esto."""
    with A.app.test_request_context():
        url = A.url_for("static", filename="css/no_existe_en_ningun_lado.css")
    assert url.endswith("/static/css/no_existe_en_ningun_lado.css")


def test_las_pantallas_no_se_guardan_en_el_navegador(A, client):
    """Datos de stock de un usuario logueado no pueden quedar en el cache del
    navegador: con el botón atrás se verían después de cerrar sesión."""
    _admin(A, client)
    for url in ["/stock", "/movements", "/items"]:
        cc = client.get(url).headers.get("Cache-Control", "")
        assert "no-store" in cc, f"{url} no manda no-store"


def test_el_csv_no_queda_marcado_como_no_store(A, client):
    """El no-store es sólo para las pantallas. Un export es un archivo que se
    baja una vez; no tiene por qué entrar en esa regla."""
    _admin(A, client)
    r = client.get("/stock/export.csv")
    assert r.status_code == 200
    assert "no-store" not in r.headers.get("Cache-Control", "")


def test_el_estado_del_menu_se_aplica_antes_de_pintar(A, client):
    """El salto del sidebar: la clase la pone un script inline al abrir el
    contenedor, NO app.js al final del body. Si alguien lo devuelve a app.js,
    vuelve el parpadeo en cada pantalla."""
    _admin(A, client)
    html = client.get("/stock").get_data(as_text=True)
    assert "tngstocks.sidebarCollapsed" in html
    assert html.index("tngstocks.sidebarCollapsed") < html.index("js/app.js")
