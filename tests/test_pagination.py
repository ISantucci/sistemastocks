"""Regresión de paginación.

Objetivo doble:
  1) Verificar que los listados ya no vuelcan TODAS las filas en un solo HTML.
  2) Verificar que no se perdió información ni cambió el contrato de las URLs
     que ya se usaban (?limit=N sigue significando lo mismo en la página 1).
"""
import re

import pytest
from conftest import make_item, make_location, make_user, login


def _cuerpo_tabla(html):
    """Contenido del <tbody> de la primera tabla de datos.

    Se mira SOLO el cuerpo de la tabla a propósito: el resto del HTML tiene
    selects de filtro y modales que también contienen códigos de ítem y
    falsearían el conteo.
    """
    m = re.search(r"<tbody>(.*?)</tbody>", html, re.S)
    return m.group(1) if m else ""


def _rows_en_tabla(html):
    cuerpo = _cuerpo_tabla(html)
    if not cuerpo or "Sin resultados" in cuerpo or "Sin ítems" in cuerpo:
        return 0
    return len(re.findall(r"<tr", cuerpo))


@pytest.fixture()
def admin(A):
    A.app.config["WTF_CSRF_ENABLED"] = False
    c = A.app.test_client()
    login(c, "admin", "admin123")
    return A, c


# --------------------------------------------------------------- /items


def test_items_pagina_y_no_vuelca_todo(admin):
    A, c = admin
    for i in range(25):
        make_item(A, code=f"CAB-{i:03d}", name=f"Cable {i}")

    html = c.get("/items?limit=10").get_data(as_text=True)
    assert _rows_en_tabla(html) == 10, "la página 1 debe traer exactamente 10 filas"
    assert "Mostrando" in html and "25" in html, "debe informarse el total real"


def test_items_paginas_cubren_el_total_sin_repetir(admin):
    A, c = admin
    for i in range(25):
        make_item(A, code=f"CAB-{i:03d}", name=f"Cable {i}")

    vistos = []
    for page in (1, 2, 3):
        html = c.get(f"/items?limit=10&page={page}").get_data(as_text=True)
        vistos += re.findall(r"CAB-\d{3}", _cuerpo_tabla(html))

    assert len(vistos) == 25, "no debe haber filas repetidas entre páginas"
    assert len(set(vistos)) == 25, "las 3 páginas deben cubrir los 25 ítems"


def test_items_pagina_fuera_de_rango_no_rompe(admin):
    A, c = admin
    make_item(A, code="CAB-001")
    r = c.get("/items?limit=10&page=9999")
    assert r.status_code == 200, "una página inexistente debe caer a la última, no dar error"


def test_items_page_negativa_o_basura_no_rompe(admin):
    A, c = admin
    make_item(A, code="CAB-001")
    for bad in ("0", "-3", "abc", "9e9", ""):
        assert c.get(f"/items?page={bad}").status_code == 200


# --------------------------------------------------------------- /stock


def test_stock_pagina(admin):
    A, c = admin
    loc = make_location(A, "Dep1")
    for i in range(15):
        it = make_item(A, code=f"STK-{i:03d}", name=f"Item {i}")
        A.upsert_stock(it.id, loc.id, 5)
    A.db.session.commit()

    html = c.get("/stock?limit=5").get_data(as_text=True)
    assert _rows_en_tabla(html) == 5
    assert "Mostrando" in html


def test_stock_export_csv_no_se_pagina(admin):
    """El export debe seguir bajando TODO el filtro, no solo la página actual."""
    A, c = admin
    loc = make_location(A, "Dep1")
    for i in range(15):
        it = make_item(A, code=f"STK-{i:03d}", name=f"Item {i}")
        A.upsert_stock(it.id, loc.id, 5)
    A.db.session.commit()

    csv_text = c.get("/stock/export.csv?limit=5").get_data(as_text=True)
    assert csv_text.count("STK-") >= 15, "el CSV no debe quedar recortado por la paginación"


def test_stock_filtros_siguen_funcionando_con_paginacion(admin):
    A, c = admin
    loc = make_location(A, "Dep1")
    it_a = make_item(A, code="AAA-001", name="Buscado")
    it_b = make_item(A, code="BBB-001", name="Otro")
    A.upsert_stock(it_a.id, loc.id, 3)
    A.upsert_stock(it_b.id, loc.id, 3)
    A.db.session.commit()

    html = c.get("/stock?q=AAA-001").get_data(as_text=True)
    assert "AAA-001" in html
    assert _rows_en_tabla(html) == 1


# --------------------------------------------------------------- compatibilidad


@pytest.mark.parametrize("path", ["/items", "/stock", "/movements", "/remitos"])
def test_urls_historicas_sin_page_siguen_andando(admin, path):
    """Una URL vieja (sin ?page) tiene que seguir mostrando el principio del listado."""
    A, c = admin
    assert c.get(path).status_code == 200
    assert c.get(path + "?limit=100").status_code == 200


def test_tecnico_sigue_viendo_solo_su_ubicacion_con_paginacion(A):
    """La paginación no debe abrir un agujero en el filtro por ubicación."""
    A.app.config["WTF_CSRF_ENABLED"] = False
    tec = make_user(A, "tec", "TECNICO")
    mia = make_location(A, "Camioneta Tec")
    ajena = make_location(A, "Camioneta Otro")
    A.db.session.add(A.LocationResponsible(location_id=mia.id, user_id=tec.id))
    A.db.session.commit()

    it_mio = make_item(A, code="MIO-001", name="Mio")
    it_ajeno = make_item(A, code="AJE-001", name="Ajeno")
    A.upsert_stock(it_mio.id, mia.id, 5)
    A.upsert_stock(it_ajeno.id, ajena.id, 5)
    A.db.session.commit()

    c = A.app.test_client()
    login(c, "tec")
    for page in (1, 2):
        cuerpo = _cuerpo_tabla(c.get(f"/stock?limit=1&page={page}").get_data(as_text=True))
        assert "AJE-001" not in cuerpo, "el TÉCNICO no debe ver stock de otra ubicación"


# --------------------------------------------------------------- selector remoto


def test_api_items_search_requiere_login(A):
    c = A.app.test_client()
    r = c.get("/api/items/search?q=CAB")
    assert r.status_code in (302, 401), "el endpoint no puede ser público"


def test_api_items_search_filtra(admin):
    A, c = admin
    make_item(A, code="CAB-001", name="Cable")
    make_item(A, code="TOR-001", name="Tornillo")

    data = c.get("/api/items/search?q=CAB").get_json()
    codigos = [d["code"] for d in data]
    assert "CAB-001" in codigos
    assert "TOR-001" not in codigos


def test_api_items_search_no_devuelve_inactivos(admin):
    A, c = admin
    make_item(A, code="OFF-001", name="Inactivo", is_active=False)
    data = c.get("/api/items/search?q=OFF").get_json()
    assert data == []


def test_selector_items_inline_con_catalogo_chico(admin):
    """Con pocos ítems, el <select> se sigue renderizando entero (sin cambio de UX)."""
    A, c = admin
    make_item(A, code="CAB-001", name="Cable")
    html = c.get("/stock").get_data(as_text=True)
    assert "CAB-001" in html
    assert "js-buscar-remoto" not in html


def test_selector_items_pasa_a_remoto_con_catalogo_grande(admin, monkeypatch):
    """Superado el umbral, el HTML deja de incluir el catálogo completo."""
    A, c = admin
    monkeypatch.setattr(A, "ITEM_PICKER_MAX_INLINE", 2)
    for i in range(5):
        make_item(A, code=f"CAB-{i:03d}", name=f"Cable {i}")

    html = c.get("/stock").get_data(as_text=True)
    assert "js-buscar-remoto" in html
    assert "/api/items/search" in html
    assert "CAB-004" not in html, "el catálogo no debe volcarse en el HTML"
