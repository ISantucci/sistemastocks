"""El modal de confirmación está enganchado en todas las pantallas que mueven stock.

Es un test de markup a propósito: no puede probar el comportamiento del modal
(eso es JavaScript), pero sí que nadie borre el enganche sin darse cuenta. Si
falta el atributo, la pantalla vuelve a registrar movimientos sin confirmar.
"""
import pytest

from conftest import make_user, make_item, make_location, login


@pytest.fixture()
def base(A):
    make_user(A, "admin2", "ADMIN")
    it = make_item(A, code="CAB-300", name="Cable")
    jaula = make_location(A, "Jaula TNG")
    make_location(A, "Proveedor", is_external=True)
    make_location(A, "Descartes")
    make_location(A, "Utilizado")
    cam = make_location(A, "Camioneta 1", is_truck=True)
    A.upsert_stock(it.id, jaula.id, 5)
    A.db.session.commit()
    return it, jaula, cam


PANTALLAS = ["/movements", "/movements/bulk", "/ingresos-egresos", "/item-usage", "/scrap"]


@pytest.mark.parametrize("path", PANTALLAS)
def test_las_pantallas_de_movimiento_piden_confirmacion(A, client, base, path):
    login(client, "admin2")
    html = client.get(path).get_data(as_text=True)
    assert "data-confirm-move" in html, f"{path} envía sin confirmar"


def test_el_script_de_confirmacion_se_carga_en_todas_las_paginas(A, client, base):
    login(client, "admin2")
    html = client.get("/movements").get_data(as_text=True)
    assert "confirm_move.js" in html


def test_el_descarte_se_marca_como_irreversible(A, client, base):
    login(client, "admin2")
    html = client.get("/scrap").get_data(as_text=True)
    assert "data-confirm-danger" in html


def test_las_filas_repetibles_estan_marcadas(A, client, base):
    """Sin data-confirm-row el resumen no lista los ítems cargados."""
    login(client, "admin2")
    for path in ["/movements/bulk", "/ingresos-egresos", "/item-usage", "/scrap"]:
        html = client.get(path).get_data(as_text=True)
        assert "data-confirm-row" in html, f"{path} no marca sus filas"
