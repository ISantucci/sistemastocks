"""Stock: el buscador acepta texto libre, igual que el de Ítems.

El backend de /stock ya filtraba por texto (ilike contra código, nombre y
descripción); al <select> le faltaba la clase `js-buscar-libre`, así que la
pantalla obligaba a elegir un ítem puntual y no había forma de escribir una
palabra y traer todos los que coincidan.

Estos tests fijan las tres cosas que tienen que valer al mismo tiempo:

1. una palabra trae TODOS los que coincidan (por código o por nombre),
2. elegir un ítem puntual sigue filtrando a ese solo (no se rompe lo de antes),
3. la palabra buscada queda visible en el control después de recargar.

No toca stock, movimientos ni permisos: es un filtro de pantalla.
"""
import re

from conftest import make_category, make_item, make_location, make_user, login


def filas(body):
    """Solo el cuerpo de la tabla.

    Hace falta acotar al <tbody>: el <select> del buscador emite un <option>
    por cada ítem del catálogo, así que buscar un código en el HTML entero da
    positivo siempre, esté o no la fila en la tabla.
    """
    m = re.search(r"<tbody>(.*?)</tbody>", body, re.S)
    return m.group(1) if m else ""


def _setup(A, client):
    """Cuatro ítems con stock: tres que matchean 'neo' y uno que no."""
    make_user(A, "admin1", "ADMIN")
    cat = make_category(A, "Cables", "CAB")
    loc = make_location(A, "Jaula")
    creados = {}
    for code, name in [
        ("NEO-001", "Cable coaxil"),      # matchea por CÓDIGO
        ("ANT-500", "Antena Neolink"),    # matchea por NOMBRE
        ("CBL-777", "Cable NEOprene"),    # matchea en medio de la palabra
        ("RTR-900", "Router"),            # no matchea
    ]:
        it = make_item(A, code=code, name=name, category=cat)
        A.db.session.add(A.Stock(item_id=it.id, location_id=loc.id, quantity=5))
        creados[code] = it
    A.db.session.commit()
    login(client, "admin1", A=A)
    return creados


def test_una_palabra_trae_todos_los_que_coinciden(A, client):
    _setup(A, client)

    tabla = filas(client.get("/stock?q=neo").get_data(as_text=True))

    assert "NEO-001" in tabla      # por código
    assert "ANT-500" in tabla      # por nombre ("Neolink")
    assert "CBL-777" in tabla      # "NEOprene": la palabra en el medio
    assert "RTR-900" not in tabla


def test_elegir_un_item_puntual_sigue_filtrando_a_uno(A, client):
    _setup(A, client)

    tabla = filas(client.get("/stock?q=RTR-900").get_data(as_text=True))

    assert "RTR-900" in tabla
    assert "NEO-001" not in tabla


def test_el_buscador_es_un_select_por_codigo_con_texto_libre(A, client):
    """La estética se mantiene: sigue siendo el select buscable de siempre."""
    _setup(A, client)

    body = client.get("/stock").get_data(as_text=True)

    assert 'name="q"' in body
    assert "js-buscar-libre" in body
    assert '<option value="NEO-001"' in body      # el value es el código


def test_el_texto_libre_buscado_queda_visible_en_el_control(A, client):
    """Sin esto el filtro queda aplicado en la tabla y el select dice "Todos"."""
    _setup(A, client)

    body = client.get("/stock?q=NEO").get_data(as_text=True)

    assert '<option value="NEO" selected>NEO</option>' in body
