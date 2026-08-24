"""Alcance del TECNICO en la ficha de seriales y en el mapa de stock embebido.

Contexto: el TECNICO solo debe ver lo de SUS ubicaciones, y el acote tiene que
estar en la consulta, no en el template: lo que no le corresponde no tiene que
viajar en el HTML.

Dos agujeros que estas pruebas cubren:

  A) /items/<id>/units devolvia TODAS las unidades del item y el stock de TODAS
     las ubicaciones internas. Un tecnico podia leer los seriales y las
     cantidades de la camioneta de otro escribiendo la URL a mano.

  B) /movements y /item-usage embebian en el HTML el mapa completo
     ubicacion -> items y ubicacion -> cantidades. No se veia en pantalla, pero
     estaba en el codigo fuente de la pagina.

Verificadas contra el codigo previo al parche: fallan. No pasan de casualidad.
"""
import json
import re

from conftest import make_user, make_category


def _armar_escenario(A):
    """Dos tecnicos, una camioneta cada uno, un item serializado en cada una."""
    t1 = make_user(A, "tec_uno", "TECNICO")
    t2 = make_user(A, "tec_dos", "TECNICO")

    cam1 = A.Location(name="CamionetaUno", is_truck=True)
    cam2 = A.Location(name="CamionetaDos", is_truck=True)
    A.db.session.add_all([cam1, cam2])
    A.db.session.commit()

    A.db.session.add(A.LocationResponsible(location_id=cam1.id, user_id=t1.id))
    A.db.session.add(A.LocationResponsible(location_id=cam2.id, user_id=t2.id))
    A.db.session.commit()

    cat = make_category(A, "Equipos", "EQP")
    item = A.Item(code="EQP-001", name="Radio", category_id=cat.id, serialized=True)
    otro = A.Item(code="EQP-002", name="Antena", category_id=cat.id)
    A.db.session.add_all([item, otro])
    A.db.session.commit()

    A.upsert_stock(item.id, cam1.id, 1)
    A.upsert_stock(item.id, cam2.id, 1)
    A.upsert_stock(otro.id, cam2.id, 9)      # solo en la camioneta ajena
    A.db.session.add(A.ItemUnit(item_id=item.id, serial="SERIE-PROPIA",
                                location_id=cam1.id, status=A.UNIT_EN_STOCK))
    A.db.session.add(A.ItemUnit(item_id=item.id, serial="SERIE-AJENA",
                                location_id=cam2.id, status=A.UNIT_EN_STOCK))
    A.db.session.commit()
    return {"item": item.id, "otro": otro.id, "cam1": cam1.id, "cam2": cam2.id}


def _login(client, username, password="pass1234"):
    return client.post("/login", data={"username": username, "password": password},
                       follow_redirects=True)


def _stock_map(html):
    m = re.search(r"(?:stockMap|stock_map)\s*=\s*(\{.*?\});", html, re.S)
    return json.loads(m.group(1)) if m else None


# --------------------------------------------------------------- A: seriales

def test_tecnico_no_ve_seriales_de_otra_camioneta(client, A):
    ids = _armar_escenario(A)
    _login(client, "tec_uno")
    html = client.get(f"/items/{ids['item']}/units").data.decode("utf-8")
    assert "SERIE-PROPIA" in html, "tiene que seguir viendo lo suyo"
    assert "SERIE-AJENA" not in html
    assert "CamionetaDos" not in html


def test_tecnico_no_puede_forzar_la_ubicacion_ajena_por_querystring(client, A):
    ids = _armar_escenario(A)
    _login(client, "tec_uno")
    html = client.get(f"/items/{ids['item']}/units?loc={ids['cam2']}").data.decode("utf-8")
    assert "SERIE-AJENA" not in html
    assert "CamionetaDos" not in html


def test_tecnico_no_ve_el_cupo_de_ubicaciones_ajenas(client, A):
    """loc_rooms expone la cantidad por ubicacion. No debe traer las ajenas."""
    ids = _armar_escenario(A)
    _login(client, "tec_uno")
    html = client.get(f"/items/{ids['otro']}/units").data.decode("utf-8")
    assert "CamionetaDos" not in html, "el item solo esta en la camioneta ajena"


def test_admin_sigue_viendo_la_ficha_completa(client, A):
    ids = _armar_escenario(A)
    _login(client, "admin", "admin123")
    html = client.get(f"/items/{ids['item']}/units").data.decode("utf-8")
    assert "SERIE-PROPIA" in html
    assert "SERIE-AJENA" in html
    assert "CamionetaDos" in html


# ------------------------------------------------------- B: mapa de stock

def test_movements_no_embebe_el_stock_de_ubicaciones_ajenas(client, A):
    ids = _armar_escenario(A)
    _login(client, "tec_uno")
    mapa = _stock_map(client.get("/movements").data.decode("utf-8"))
    assert mapa is not None, "el mapa tiene que seguir estando"
    assert str(ids["cam1"]) in mapa, "tiene que seguir teniendo lo suyo"
    assert str(ids["cam2"]) not in mapa


def test_item_usage_no_embebe_el_stock_de_ubicaciones_ajenas(client, A):
    ids = _armar_escenario(A)
    _login(client, "tec_uno")
    mapa = _stock_map(client.get("/item-usage").data.decode("utf-8"))
    assert mapa is not None
    assert str(ids["cam1"]) in mapa
    assert str(ids["cam2"]) not in mapa


def test_el_item_ajeno_no_aparece_en_el_mapa_del_tecnico(client, A):
    ids = _armar_escenario(A)
    _login(client, "tec_uno")
    html = client.get("/movements").data.decode("utf-8")
    mapa = _stock_map(html)
    todos = [i for lista in mapa.values() for i in lista]
    assert ids["otro"] not in todos


def test_admin_sigue_recibiendo_el_mapa_completo(client, A):
    ids = _armar_escenario(A)
    _login(client, "admin", "admin123")
    mapa = _stock_map(client.get("/movements").data.decode("utf-8"))
    assert str(ids["cam1"]) in mapa
    assert str(ids["cam2"]) in mapa


def test_el_tecnico_sigue_pudiendo_operar_su_camioneta(client, A):
    """El acote no puede romperle la operacion: tiene que poder declarar consumo."""
    ids = _armar_escenario(A)
    _login(client, "tec_uno")
    r = client.get("/item-usage")
    assert r.status_code == 200
    assert b"CamionetaUno" in r.data
