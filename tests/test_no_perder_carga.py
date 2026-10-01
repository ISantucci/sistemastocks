"""Que un rechazo del servidor no borre lo cargado (2026-10-01).

Bug reportado: en Carga múltiple un compañero perdió toda la carga porque el
servidor rechazó un dato y la pantalla volvió vacía, con un error que no
entendió. Dos arreglos, uno de cada lado:

1. FRONT (static/js/form_draft.js): los formularios de carga guardan un
   borrador en la pestaña al enviar y, si vuelven con error, se reponen. Eso
   se prueba en un navegador (jsdom con el HTML real de cada pantalla); acá
   quedan las guardas de que cada pantalla lo tiene enganchado.
2. BACKEND (flash_error_inesperado): un error técnico ya no muestra el texto
   crudo de Python. Sale un mensaje claro con un código, y el detalle queda en
   logs/errores.log. Los errores de negocio (stock insuficiente) se siguen
   mostrando tal cual.
"""
import os
import re

import pytest
from conftest import make_user, make_item, make_location, login

_APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _leer(*partes):
    with open(os.path.join(_APP_DIR, *partes), encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------------------ guardas del front

@pytest.mark.parametrize("template, borrador", [
    ("movements_bulk.html", 'data-draft="carga-multiple"'),
    ("movements.html", 'data-draft="movimiento"'),
    ("ingresos_egresos.html", 'data-draft="ingresos-egresos"'),
    ("item_usage.html", 'data-draft="utilizados"'),
    ("scrap_report.html", 'data-draft="descartes"'),
    ("repair_requests.html", 'data-draft="solicitud-repuestos"'),
    ("repair_request_detail.html", 'data-draft="cerrar-solicitud"'),
    ("stock_count_new.html", 'data-draft="conteo-camioneta"'),
    ("item_units.html", 'data-draft="tanda-seriales"'),
    ("conteo.html", 'data-draft="conteo"'),
    ("purchase_requests.html", 'data-draft="solicitud-compra"'),
    ("item_new.html", 'data-draft="item-nuevo"'),
    ("edit_item.html", 'data-draft="item-editar"'),
    ("pending_deliveries.html", 'data-draft="pendiente-sin-entrega"'),
])
def test_las_pantallas_de_carga_tienen_borrador(template, borrador):
    assert borrador in _leer("templates", template)


@pytest.mark.parametrize("template, boton", [
    ("movements_bulk.html", "#add-line-btn"),
    ("ingresos_egresos.html", "#io-add-line"),
    ("item_usage.html", "#add-line-btn"),
    ("scrap_report.html", "#add-line-btn"),
    ("repair_requests.html", "#add-line-btn"),
    ("stock_count_new.html", "#sc-add-line-btn"),
    ("purchase_requests.html", "#pr-add-line-btn"),
])
def test_las_pantallas_con_filas_dicen_como_agregarlas(template, boton):
    html = _leer("templates", template)
    assert f'data-draft-add="{boton}"' in html
    assert f'id="{boton[1:]}"' in html


def test_base_carga_el_borrador_despues_de_los_errores():
    """form_draft.js mira los errores del servidor mientras se arma la página,
    antes de que form_errors.js los pase al popup."""
    base = _leer("templates", "base.html")
    i_err = base.index("js/form_errors.js")
    i_draft = base.index("js/form_draft.js")
    assert i_err < i_draft


def test_el_borrador_no_guarda_contrasenas_ni_archivos():
    js = _leer("static", "js", "form_draft.js")
    assert '"password"' in js and '"file"' in js
    # Solo se repone si la pantalla vuelve con error: reponer algo que se
    # guardó bien invita a mandarlo dos veces.
    assert "if (!raw || !huboError) return;" in js


def test_los_selectores_de_seriales_exponen_el_gancho():
    js = _leer("static", "js", "serial_picker.js")
    assert js.count('setAttribute("data-sn-hook", "")') == 2
    assert js.count("_snRestore = function") == 2


def test_el_popup_de_errores_avisa_al_borrador():
    assert "window.TNG_SERVER_ERRORS = textos" in _leer("static", "js", "form_errors.js")


# ------------------------------------------------------------ errores entendibles

@pytest.fixture()
def esc(A):
    A.app.config["WTF_CSRF_ENABLED"] = False
    jaula = make_location(A, A.LOCATION_JAULA_TNG)
    truck = make_location(A, "Camioneta Uno", is_truck=True)
    it = make_item(A, code="CAB-201", name="Cable")
    A.upsert_stock(it.id, jaula.id, 10)
    A.db.session.commit()
    c = A.app.test_client()
    login(c, "admin", "admin123")
    return {"jaula": jaula, "truck": truck, "item": it, "c": c}


def _bulk(esc):
    return esc["c"].post("/movements/bulk", data={
        "from_location_id": str(esc["jaula"].id), "to_location_id": str(esc["truck"].id),
        "item_id[]": [str(esc["item"].id)], "qty[]": ["1"], "generate_pending[]": ["0"],
        "pending_comment[]": [""], "pending_return_item_id[]": [""],
        "pending_return_qty[]": [""], "scrap_reason[]": [""], "unit_ids[]": [""],
    }, follow_redirects=True)


def test_un_error_tecnico_no_muestra_texto_crudo_y_queda_en_el_log(A, esc, monkeypatch):
    def _rompe():
        raise RuntimeError("(sqlite3.OperationalError) database is locked")
    monkeypatch.setattr(A, "next_movement_number", _rompe)

    html = _bulk(esc).get_data(as_text=True)

    assert "database is locked" not in html          # el usuario no ve Python
    assert "por un error del sistema" in html
    assert "No se guardó nada" in html
    m = re.search(r"con este código: (\d{4}-[0-9A-F]{4})", html)
    assert m, "el aviso tiene que traer el código para buscarlo en el log"

    log = (A.LOG_DIR / "errores.log").read_text(encoding="utf-8")
    assert f"ref={m.group(1)}" in log
    assert "usuario=admin" in log and "/movements/bulk" in log
    assert "database is locked" in log and "Traceback" in log

    # Todo o nada: no se movió stock ni quedó el movimiento.
    assert A.Movement.query.count() == 0
    st = A.Stock.query.filter_by(item_id=esc["item"].id, location_id=esc["jaula"].id).one()
    assert st.quantity == 10


def test_un_error_de_negocio_se_sigue_mostrando_tal_cual(A, esc, monkeypatch):
    def _sin_stock(*a, **k):
        raise ValueError("Stock insuficiente en la ubicacion de origen")
    monkeypatch.setattr(A, "upsert_stock", _sin_stock)

    html = _bulk(esc).get_data(as_text=True)

    assert "Stock insuficiente en la ubicacion de origen" in html
    assert "por un error del sistema" not in html
    assert not (A.LOG_DIR / "errores.log").exists() or \
        "Stock insuficiente" not in (A.LOG_DIR / "errores.log").read_text(encoding="utf-8")


def test_las_pantallas_operativas_ya_no_muestran_la_excepcion_cruda():
    """Quedan solo las herramientas administrativas (reset, backup, ajuste,
    limpiezas), que usa el admin y ya registran en destructive_ops.log, y los
    errores de stock (ValueError, que son mensajes para el usuario)."""
    src = _leer("app.py")
    crudos = re.findall(r'flash\(f"([^"]*)\{e\}', src)
    permitidos = {
        "No se pudo reiniciar: ", "No se pudo generar el backup: ",
        "No se pudo ajustar: ", "No se pudo limpiar stock: ",
        "No se pudo limpiar items: ", "Error de stock: ",
        "No se pudo ...: ",   # el docstring de flash_error_inesperado que lo cuenta
    }
    assert set(crudos) <= permitidos, set(crudos) - permitidos


def test_conteo_si_falla_al_confirmar_vuelve_a_la_misma_ubicacion(A, esc, monkeypatch):
    """Antes volvía a /conteo sin ubicación y había que contar todo de nuevo."""
    def _rompe(*a, **k):
        raise RuntimeError("falla inesperada")
    monkeypatch.setattr(A, "next_movement_number", _rompe)
    r = esc["c"].post("/conteo", data={
        "location_id": str(esc["jaula"].id), "action": "apply", "motivo": "mensual",
        f"contado_{esc['item'].id}": "8",
    }, follow_redirects=False)
    assert r.status_code == 302
    assert f"location_id={esc['jaula'].id}" in r.headers["Location"]
    html = esc["c"].get(r.headers["Location"]).get_data(as_text=True)
    assert "por un error del sistema" in html          # fue la falla, no otra validación
    assert 'data-draft="conteo"' in html                # y ahí está el form que se repone
