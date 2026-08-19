"""Orden del listado de movimientos + numeración por año.

El test de numeración es el importante: cubre un bug que se disparaba solo al
cambiar de año (el número no llevaba el año, el seq se reiniciaba y el UNIQUE
de `number` hacía fallar el primer movimiento de enero).
"""
import re

from conftest import make_user, make_item, make_location, login


def _setup(A):
    make_user(A, "op", "ADMIN")
    it = make_item(A, code="CAB-100", name="Cable UTP")
    jaula = make_location(A, "Jaula TNG")
    cam = make_location(A, "Camioneta 1", is_truck=True)
    A.upsert_stock(it.id, jaula.id, 50)
    A.db.session.commit()
    return it, jaula, cam


def _mover(client, it, desde, hacia, qty=1, **extra):
    data = {"item_id": str(it.id), "qty": str(qty),
            "from_location_id": str(desde.id), "to_location_id": str(hacia.id)}
    data.update(extra)
    return client.post("/movements", data=data, follow_redirects=True)


# ------------------ numeración ------------------

def test_el_numero_de_movimiento_lleva_el_anio(A, client):
    it, jaula, cam = _setup(A)
    login(client, "op")
    _mover(client, it, jaula, cam)

    m = A.Movement.query.first()
    assert re.fullmatch(r"MOV-\d{4}-\d{4}", m.number), m.number
    assert str(m.year) in m.number


def test_el_cambio_de_anio_no_rompe_el_registro(A, client):
    """Antes: en enero el seq volvía a 1, el número se repetía y el UNIQUE
    hacía fallar el registro. El movimiento no se podía cargar."""
    it, jaula, cam = _setup(A)
    login(client, "op")
    _mover(client, it, jaula, cam)

    primero = A.Movement.query.first()
    anio_viejo = primero.year

    # Se simula que el movimiento existente quedó del año anterior.
    primero.year = anio_viejo - 1
    primero.number = f"MOV-{anio_viejo - 1}-0001"
    primero.seq = 1
    A.db.session.commit()

    _mover(client, it, jaula, cam)

    nuevos = A.Movement.query.filter_by(year=anio_viejo).all()
    assert len(nuevos) == 1, "el primer movimiento del año nuevo debe poder registrarse"
    assert nuevos[0].number == f"MOV-{anio_viejo}-0001"
    assert nuevos[0].number != primero.number, "no puede repetirse el número entre años"


def test_los_numeros_no_se_repiten_en_una_tanda(A, client):
    it, jaula, cam = _setup(A)
    login(client, "op")
    for _ in range(5):
        _mover(client, it, jaula, cam)

    numeros = [m.number for m in A.Movement.query.all()]
    assert len(numeros) == len(set(numeros)) == 5


# ------------------ orden ------------------

def _ids_en_pantalla(client, qs=""):
    html = client.get("/movements" + qs).get_data(as_text=True)
    cuerpo = html.split("<tbody>")[1].split("</tbody>")[0]
    return re.findall(r"MOV-\d{4}-\d{4}", cuerpo)


def test_orden_por_id_ascendente_y_descendente(A, client):
    it, jaula, cam = _setup(A)
    login(client, "op")
    for _ in range(3):
        _mover(client, it, jaula, cam)

    asc = _ids_en_pantalla(client, "?sort_by=id&sort_dir=asc")
    desc = _ids_en_pantalla(client, "?sort_by=id&sort_dir=desc")

    assert asc == sorted(asc)
    assert desc == list(reversed(asc))


def test_orden_por_accion_agrupa_por_estado(A, client):
    it, jaula, cam = _setup(A)
    login(client, "op")
    for _ in range(3):
        _mover(client, it, jaula, cam)

    # Se revierte uno: queda 1 revertido + 1 reversión + 2 normales.
    m = A.Movement.query.order_by(A.Movement.id.desc()).first()
    client.post(f"/movements/{m.id}/revertir", follow_redirects=True)

    html = client.get("/movements?sort_by=action&sort_dir=asc").get_data(as_text=True)
    cuerpo = html.split("<tbody>")[1].split("</tbody>")[0]
    estados = re.findall(r">(Revertido|Reversión)<", cuerpo)
    # Ascendente: primero los normales, después revertido, al final la reversión.
    assert estados == ["Revertido", "Reversión"], estados

    html_desc = client.get("/movements?sort_by=action&sort_dir=desc").get_data(as_text=True)
    cuerpo_desc = html_desc.split("<tbody>")[1].split("</tbody>")[0]
    assert re.findall(r">(Revertido|Reversión)<", cuerpo_desc) == ["Reversión", "Revertido"]


def test_las_columnas_id_y_accion_son_clickeables(A, client):
    _setup(A)
    login(client, "op")
    html = client.get("/movements").get_data(as_text=True)
    assert "sort_by=id" in html
    assert "sort_by=action" in html


def test_un_sort_by_invalido_no_rompe(A, client):
    it, jaula, cam = _setup(A)
    login(client, "op")
    _mover(client, it, jaula, cam)

    for qs in ["?sort_by=zzz", "?sort_by=", "?sort_by=' or 1=1--", "?sort_dir=raro",
               "?sort_by=action&sort_dir=; drop table movements"]:
        assert client.get("/movements" + qs).status_code == 200, qs


def test_ordenar_vuelve_a_la_primera_pagina(A, client):
    """Si el link conservara `page`, ordenar desde la página 3 dejaría al
    usuario mirando el medio de la lista nueva."""
    _setup(A)
    login(client, "op")
    html = client.get("/movements?page=3").get_data(as_text=True)
    assert "sort_by=id" in html
    for link in re.findall(r'href="([^"]*sort_by=id[^"]*)"', html):
        assert "page=" not in link, link


def test_ordenar_conserva_los_filtros(A, client):
    _setup(A)
    login(client, "op")
    html = client.get("/movements?limit=50&user_id=1").get_data(as_text=True)
    links = re.findall(r'href="([^"]*sort_by=action[^"]*)"', html)
    assert links, "falta el link de orden por acción"
    assert all("limit=50" in l and "user_id=1" in l for l in links), links


def test_el_tecnico_no_ve_la_columna_accion(A, client):
    it, jaula, cam = _setup(A)
    tec = make_user(A, "tec", "TECNICO")
    A.db.session.add(A.LocationResponsible(location_id=cam.id, user_id=tec.id))
    A.db.session.commit()
    login(client, "op")
    _mover(client, it, jaula, cam)

    client.get("/logout")
    login(client, "tec")
    html = client.get("/movements").get_data(as_text=True)
    assert "sort_by=action" not in html
    # y forzar el orden por la URL tampoco debe romperle la pantalla
    assert client.get("/movements?sort_by=action").status_code == 200
