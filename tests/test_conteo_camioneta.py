"""Conteo de camioneta: lo declara el TECNICO, lo aprueba ADMIN/SUPERVISOR.

Lo que se prueba acá no es sólo que la pantalla funcione: es que **enviar un
conteo no toque stock**, que el técnico no pueda aprobar el suyo ni ver los
ajenos (por POST directo, no por botón oculto) y que el ajuste salga del stock
ACTUAL y no de la foto que vio el técnico al contar.
"""
import pytest
from conftest import make_user, make_item, make_location, login


@pytest.fixture()
def esc(A):
    """Dos técnicos con camioneta propia, un admin y algo de stock cargado."""
    A.app.config["WTF_CSRF_ENABLED"] = False

    tec = make_user(A, "tec", "TECNICO")
    otro = make_user(A, "otro", "TECNICO")
    sup = make_user(A, "sup", "SUPERVISOR")
    lec = make_user(A, "lec", "LECTOR")

    truck = make_location(A, "Camioneta 1", is_truck=True)
    truck2 = make_location(A, "Camioneta 2", is_truck=True)
    jaula = make_location(A, A.LOCATION_JAULA_TNG)

    A.db.session.add(A.LocationResponsible(location_id=truck.id, user_id=tec.id))
    A.db.session.add(A.LocationResponsible(location_id=truck2.id, user_id=otro.id))
    A.db.session.commit()

    cable = make_item(A, code="CAB-001", name="Cable")
    ficha = make_item(A, code="CAB-002", name="Ficha")
    nuevo = make_item(A, code="CAB-003", name="Item que no tiene asignado")

    A.upsert_stock(cable.id, truck.id, 10)
    A.upsert_stock(ficha.id, truck.id, 4)
    A.upsert_stock(cable.id, jaula.id, 100)
    A.db.session.commit()

    return {
        "tec": tec, "otro": otro, "sup": sup, "lec": lec,
        "truck": truck, "truck2": truck2, "jaula": jaula,
        "cable": cable, "ficha": ficha, "nuevo": nuevo,
    }


def _como(A, username):
    c = A.app.test_client()
    login(c, username)
    return c


def _qty(A, item_id, location_id):
    row = A.Stock.query.filter_by(item_id=item_id, location_id=location_id).first()
    return row.quantity if row else 0


def _enviar(client, esc, contados, extras=None, comment="", serials=None,
            sin_serial=None, sin_ninguno=None):
    """POST del conteo.

    `contados` es {item: cantidad} de los renglones fijos NO serializados.
    `serials` es {item: [unit_id, ...]} para los serializados (que no llevan
    cantidad: la cantidad es cuántos seriales declaró).
    """
    serials = serials or {}
    filas = list(contados) + [i for i in serials if i not in contados]
    data = {
        "location_id": str(esc["truck"].id),
        "row_item_id[]": [str(i.id) for i in filas],
        "comment": comment,
    }
    for item, qty in contados.items():
        data[f"contado_{item.id}"] = str(qty)
    for item, unit_ids in serials.items():
        data[f"serial_unit_ids_{item.id}"] = [str(u) for u in unit_ids]
    for item, n in (sin_serial or {}).items():
        data[f"sin_serial_{item.id}"] = str(n)
    for item in (sin_ninguno or []):
        data[f"sin_ninguno_{item.id}"] = "1"
    if extras:
        data["extra_item_id[]"] = [str(i.id) for i, _ in extras]
        data["extra_qty[]"] = [str(q) for _, q in extras]
    return client.post("/conteo-camioneta/nuevo", data=data, follow_redirects=False)


def _serializado(A, esc, code, seriales, location=None):
    """Crea un ítem serializado con sus unidades en una ubicación."""
    it = make_item(A, code=code, name="Camara " + code)
    it.serialized = True
    A.db.session.commit()
    loc = location or esc["truck"]
    A.upsert_stock(it.id, loc.id, len(seriales))
    units = []
    for s in seriales:
        u = A.ItemUnit(item_id=it.id, serial=s, status=A.UNIT_EN_STOCK, location_id=loc.id)
        A.db.session.add(u)
        units.append(u)
    A.db.session.commit()
    return it, units


# ---------------- enviar un conteo NO toca stock ----------------

def test_enviar_conteo_no_modifica_stock(A, esc):
    c = _como(A, "tec")
    _enviar(c, esc, {esc["cable"]: 3, esc["ficha"]: 4})
    # El técnico declaró 3 cables donde el sistema tiene 10: hasta que no se
    # apruebe, el stock tiene que seguir intacto.
    assert _qty(A, esc["cable"].id, esc["truck"].id) == 10
    assert A.Movement.query.count() == 0
    assert A.Scrap.query.count() == 0


def test_score_por_renglones_y_estado_pendiente(A, esc):
    c = _como(A, "tec")
    _enviar(c, esc, {esc["cable"]: 3, esc["ficha"]: 4})
    sc = A.StockCount.query.one()
    assert sc.lines_total == 2 and sc.lines_ok == 1
    assert sc.score_pct == 50
    assert sc.status == A.STOCK_COUNT_PENDIENTE


def test_conteo_perfecto_no_queda_pendiente(A, esc):
    c = _como(A, "tec")
    _enviar(c, esc, {esc["cable"]: 10, esc["ficha"]: 4})
    sc = A.StockCount.query.one()
    assert sc.score_pct == 100
    assert sc.status == A.STOCK_COUNT_SIN_DIFERENCIAS


def test_renglon_vacio_no_se_guarda(A, esc):
    """Contar a ciegas y dejar un campo vacío inflaría el puntaje: se rechaza."""
    c = _como(A, "tec")
    data = {
        "location_id": str(esc["truck"].id),
        "row_item_id[]": [str(esc["cable"].id), str(esc["ficha"].id)],
        "contado_%d" % esc["cable"].id: "7",
        "contado_%d" % esc["ficha"].id: "",
    }
    c.post("/conteo-camioneta/nuevo", data=data)
    assert A.StockCount.query.count() == 0


def test_tecnico_puede_agregar_item_que_no_tiene_asignado(A, esc):
    c = _como(A, "tec")
    _enviar(c, esc, {esc["cable"]: 10, esc["ficha"]: 4},
            extras=[(esc["nuevo"], 2)], comment="compré 2 por mi cuenta")
    sc = A.StockCount.query.one()
    linea = [ln for ln in sc.lines if ln.item_id == esc["nuevo"].id][0]
    assert linea.added_by_tech is True
    assert linea.qty_sistema == 0 and linea.qty_contada == 2
    # Un ítem que el sistema no le tenía es, por definición, una diferencia.
    assert sc.status == A.STOCK_COUNT_PENDIENTE
    assert sc.comment == "compré 2 por mi cuenta"
    # Y sigue sin tocar stock.
    assert _qty(A, esc["nuevo"].id, esc["truck"].id) == 0


def test_comentario_llega_al_que_aprueba(A, esc):
    c = _como(A, "tec")
    _enviar(c, esc, {esc["cable"]: 3, esc["ficha"]: 4},
            comment="tengo un rollo sin código")
    sc = A.StockCount.query.one()
    html = _como(A, "sup").get(f"/conteo-camioneta/{sc.id}").get_data(as_text=True)
    assert "tengo un rollo sin código" in html


# ---------------- alcance del técnico (backend, no menú) ----------------

def test_tecnico_no_puede_contar_camioneta_ajena(A, esc):
    c = _como(A, "tec")
    data = {
        "location_id": str(esc["truck2"].id),
        "row_item_id[]": [],
    }
    c.post("/conteo-camioneta/nuevo", data=data)
    assert A.StockCount.query.count() == 0


def test_tecnico_no_ve_conteos_ajenos(A, esc):
    _enviar(_como(A, "tec"), esc, {esc["cable"]: 3, esc["ficha"]: 4})
    sc = A.StockCount.query.one()
    # El otro técnico no lo ve ni en el listado ni entrando por id.
    c = _como(A, "otro")
    assert sc.number not in c.get("/conteo-camioneta").get_data(as_text=True)
    r = c.get(f"/conteo-camioneta/{sc.id}", follow_redirects=True)
    assert sc.number not in r.get_data(as_text=True)


def test_tecnico_no_puede_aprobar_por_post_directo(A, esc):
    _enviar(_como(A, "tec"), esc, {esc["cable"]: 3, esc["ficha"]: 4})
    sc = A.StockCount.query.one()
    _como(A, "tec").post(f"/conteo-camioneta/{sc.id}/aprobar")
    A.db.session.expire_all()
    assert A.StockCount.query.one().status == A.STOCK_COUNT_PENDIENTE
    assert _qty(A, esc["cable"].id, esc["truck"].id) == 10


def test_lector_no_entra_a_la_seccion(A, esc):
    r = _como(A, "lec").get("/conteo-camioneta", follow_redirects=False)
    assert r.status_code in (302, 303)
    r2 = _como(A, "lec").get("/api/conteo-camioneta/items", follow_redirects=False)
    assert r2.status_code in (302, 303)


def test_un_solo_conteo_pendiente_por_camioneta(A, esc):
    c = _como(A, "tec")
    _enviar(c, esc, {esc["cable"]: 3, esc["ficha"]: 4})
    _enviar(c, esc, {esc["cable"]: 5, esc["ficha"]: 4})
    assert A.StockCount.query.count() == 1


# ---------------- aprobar aplica los ajustes ----------------

def test_aprobar_aplica_sobrante_y_faltante(A, esc):
    """Faltante -> sale a Descartes + Scrap. Sobrante -> entra desde Proveedor."""
    _enviar(_como(A, "tec"), esc, {esc["cable"]: 7, esc["ficha"]: 6})
    sc = A.StockCount.query.one()
    _como(A, "sup").post(f"/conteo-camioneta/{sc.id}/aprobar")
    A.db.session.expire_all()

    baja = A.Location.query.filter_by(name=A.LOCATION_DESCARTES).first()
    # cable: 10 -> 7 (faltan 3)
    assert _qty(A, esc["cable"].id, esc["truck"].id) == 7
    assert _qty(A, esc["cable"].id, baja.id) == 3
    # ficha: 4 -> 6 (sobran 2)
    assert _qty(A, esc["ficha"].id, esc["truck"].id) == 6

    assert A.StockCount.query.one().status == A.STOCK_COUNT_APROBADO
    assert A.Movement.query.count() == 2
    scrap = A.Scrap.query.one()
    assert scrap.quantity == 3
    assert scrap.source == A.STOCK_COUNT_SCRAP_SOURCE


def test_aprobar_agrega_el_item_que_el_tecnico_sumo(A, esc):
    _enviar(_como(A, "tec"), esc, {esc["cable"]: 10, esc["ficha"]: 4},
            extras=[(esc["nuevo"], 2)])
    sc = A.StockCount.query.one()
    _como(A, "sup").post(f"/conteo-camioneta/{sc.id}/aprobar")
    A.db.session.expire_all()
    assert _qty(A, esc["nuevo"].id, esc["truck"].id) == 2


def test_aprobar_usa_el_stock_actual_no_la_foto(A, esc):
    """Entre contar y aprobar el stock se mueve: el ajuste sale del valor real.

    El técnico cuenta 7 cuando el sistema decía 10. Antes de aprobar, alguien
    entrega 5 más (sistema = 15). Si el ajuste saliera de la foto restaría 3 y
    dejaría 12; tiene que dejar exactamente lo contado: 7.
    """
    _enviar(_como(A, "tec"), esc, {esc["cable"]: 7, esc["ficha"]: 4})
    sc = A.StockCount.query.one()
    A.upsert_stock(esc["cable"].id, esc["truck"].id, 5)
    A.db.session.commit()

    _como(A, "sup").post(f"/conteo-camioneta/{sc.id}/aprobar")
    A.db.session.expire_all()
    assert _qty(A, esc["cable"].id, esc["truck"].id) == 7
    linea = [ln for ln in A.StockCount.query.one().lines
             if ln.item_id == esc["cable"].id][0]
    assert linea.qty_sistema == 10        # la foto no se toca
    assert linea.qty_sistema_aprob == 15  # contra qué se ajustó de verdad


def test_no_se_puede_aprobar_dos_veces(A, esc):
    _enviar(_como(A, "tec"), esc, {esc["cable"]: 7, esc["ficha"]: 4})
    sc = A.StockCount.query.one()
    c = _como(A, "sup")
    c.post(f"/conteo-camioneta/{sc.id}/aprobar")
    c.post(f"/conteo-camioneta/{sc.id}/aprobar")
    A.db.session.expire_all()
    assert _qty(A, esc["cable"].id, esc["truck"].id) == 7
    assert A.Movement.query.count() == 1


def test_rechazar_no_toca_nada(A, esc):
    _enviar(_como(A, "tec"), esc, {esc["cable"]: 7, esc["ficha"]: 4})
    sc = A.StockCount.query.one()
    _como(A, "sup").post(f"/conteo-camioneta/{sc.id}/rechazar",
                         data={"review_reason": "recontá el cable"})
    A.db.session.expire_all()
    sc = A.StockCount.query.one()
    assert sc.status == A.STOCK_COUNT_RECHAZADO
    assert sc.review_reason == "recontá el cable"
    assert _qty(A, esc["cable"].id, esc["truck"].id) == 10
    assert A.Movement.query.count() == 0
    assert A.Scrap.query.count() == 0
    # Rechazado no se borra: sigue contando para las métricas.
    assert sc.score_pct == 50


def test_rechazar_exige_motivo(A, esc):
    _enviar(_como(A, "tec"), esc, {esc["cable"]: 7, esc["ficha"]: 4})
    sc = A.StockCount.query.one()
    _como(A, "sup").post(f"/conteo-camioneta/{sc.id}/rechazar", data={"review_reason": "  "})
    A.db.session.expire_all()
    assert A.StockCount.query.one().status == A.STOCK_COUNT_PENDIENTE


# ---------------- serializados ----------------


# ---------------- pantallas (render) ----------------

def test_el_formulario_no_le_muestra_la_cantidad_del_sistema(A, esc):
    """El conteo es a ciegas: si ve el número del sistema, lo copia.

    Se usa una cantidad rara (137) justamente para que, si apareciera en el
    HTML, no pueda confundirse con cualquier otro número de la página.
    """
    A.upsert_stock(esc["cable"].id, esc["truck"].id, 127)  # 10 + 127 = 137
    A.db.session.commit()
    html = _como(A, "tec").get(
        f"/conteo-camioneta/nuevo?location_id={esc['truck'].id}"
    ).get_data(as_text=True)
    assert "CAB-001" in html          # el ítem sí se lista
    assert "137" not in html          # la cantidad no
    assert 'name="contado_%d"' % esc["cable"].id in html


def test_detalle_muestra_sistema_contado_y_diferencia(A, esc):
    _enviar(_como(A, "tec"), esc, {esc["cable"]: 7, esc["ficha"]: 4})
    sc = A.StockCount.query.one()
    html = _como(A, "sup").get(f"/conteo-camioneta/{sc.id}").get_data(as_text=True)
    assert "Sistema" in html and "Conteo técnico" in html and "Diferencia" in html
    assert "Aprobar y aplicar ajustes" in html
    assert "Rechazar conteo" in html


def test_el_tecnico_no_ve_los_botones_de_aprobar(A, esc):
    _enviar(_como(A, "tec"), esc, {esc["cable"]: 7, esc["ficha"]: 4})
    sc = A.StockCount.query.one()
    html = _como(A, "tec").get(f"/conteo-camioneta/{sc.id}").get_data(as_text=True)
    assert "Aprobar y aplicar ajustes" not in html


def test_cartel_de_resultado_al_terminar(A, esc):
    c = _como(A, "tec")
    r = _enviar(c, esc, {esc["cable"]: 10, esc["ficha"]: 4})
    html = c.get(r.headers["Location"]).get_data(as_text=True)
    assert "conteo perfecto" in html

    # Con diferencias, el cartel muestra el porcentaje en vez de la felicitación.
    A.StockCount.query.one().status = A.STOCK_COUNT_RECHAZADO  # libera la camioneta
    A.db.session.commit()
    r2 = _enviar(c, esc, {esc["cable"]: 3, esc["ficha"]: 4})
    html2 = c.get(r2.headers["Location"]).get_data(as_text=True)
    assert "50% perfecto" in html2
    assert "conteo perfecto" not in html2


# ---------------- serializados: se cuentan POR SERIAL ----------------

def test_serializado_se_cuenta_por_serial_y_el_faltante_sale_solo(A, esc):
    """El técnico declara qué seriales tiene; el que no declara, falta.

    Ya no hay que elegir a mano cuál falta al aprobar: lo dice el conteo.
    """
    eq, units = _serializado(A, esc, "EQP-001", ["SN-1", "SN-2"])
    u1, u2 = units

    _enviar(_como(A, "tec"), esc, {esc["cable"]: 10, esc["ficha"]: 4},
            serials={eq: [u1.id]})
    sc = A.StockCount.query.one()
    linea = [ln for ln in sc.lines if ln.item_id == eq.id][0]
    assert linea.qty_contada == 1          # la cantidad sale de los seriales
    assert linea.coincide is False
    assert _qty(A, eq.id, esc["truck"].id) == 2   # todavía no se tocó nada

    _como(A, "sup").post(f"/conteo-camioneta/{sc.id}/aprobar")
    A.db.session.expire_all()
    assert A.StockCount.query.one().status == A.STOCK_COUNT_APROBADO
    assert _qty(A, eq.id, esc["truck"].id) == 1
    assert A.ItemUnit.query.get(u2.id).status == A.UNIT_DESCARTADO
    assert A.ItemUnit.query.get(u1.id).status == A.UNIT_EN_STOCK
    assert A.Scrap.query.filter_by(item_id=eq.id).one().quantity == 1


def test_mismos_numeros_distintos_seriales_no_es_coincidencia(A, esc):
    """Dos cámaras cambiadas entre camionetas dan la misma cantidad.

    Si el puntaje mirara sólo la cantidad, eso pasaría como conteo perfecto y es
    exactamente la inconsistencia que hay que encontrar.
    """
    eq, units = _serializado(A, esc, "EQP-002", ["SN-A"])
    ajeno, ajenos_units = _serializado(A, esc, "EQP-003", ["SN-B"], location=esc["truck2"])
    # Mismo ítem, distinto serial: el técnico declara el que está en la otra camioneta.
    eq2, u_otro = _serializado(A, esc, "EQP-004", ["SN-C"], location=esc["truck2"])
    A.upsert_stock(eq2.id, esc["truck"].id, 1)
    u_propio = A.ItemUnit(item_id=eq2.id, serial="SN-D", status=A.UNIT_EN_STOCK,
                          location_id=esc["truck"].id)
    A.db.session.add(u_propio)
    A.db.session.commit()

    _enviar(_como(A, "tec"), esc, {esc["cable"]: 10, esc["ficha"]: 4},
            serials={eq: [units[0].id], eq2: [u_otro[0].id]})
    sc = A.StockCount.query.one()
    linea = [ln for ln in sc.lines if ln.item_id == eq2.id][0]
    assert linea.qty_contada == 1
    assert linea.qty_sistema == 1     # misma cantidad...
    assert linea.coincide is False    # ...pero no es el mismo serial


def test_serial_de_la_jaula_pasa_a_la_camioneta_al_aprobar(A, esc):
    eq, units = _serializado(A, esc, "EQP-005", ["SN-J"], location=esc["jaula"])
    _enviar(_como(A, "tec"), esc, {esc["cable"]: 10, esc["ficha"]: 4},
            serials={eq: [units[0].id]})
    sc = A.StockCount.query.one()
    _como(A, "sup").post(f"/conteo-camioneta/{sc.id}/aprobar")
    A.db.session.expire_all()
    assert _qty(A, eq.id, esc["truck"].id) == 1
    assert _qty(A, eq.id, esc["jaula"].id) == 0
    assert A.ItemUnit.query.get(units[0].id).location_id == esc["truck"].id


def test_serial_de_otra_camioneta_necesita_confirmacion_aparte(A, esc):
    """Puede ser real (se lo pasaron) o un serial mal tipeado: se pide tilde."""
    eq, units = _serializado(A, esc, "EQP-006", ["SN-X"], location=esc["truck2"])
    _enviar(_como(A, "tec"), esc, {esc["cable"]: 10, esc["ficha"]: 4},
            serials={eq: [units[0].id]})
    sc = A.StockCount.query.one()
    c = _como(A, "sup")

    c.post(f"/conteo-camioneta/{sc.id}/aprobar")
    A.db.session.expire_all()
    assert A.StockCount.query.one().status == A.STOCK_COUNT_PENDIENTE
    assert _qty(A, eq.id, esc["truck2"].id) == 1

    c.post(f"/conteo-camioneta/{sc.id}/aprobar", data={"confirmar_cruces": "1"})
    A.db.session.expire_all()
    assert A.StockCount.query.one().status == A.STOCK_COUNT_APROBADO
    assert _qty(A, eq.id, esc["truck2"].id) == 0
    assert _qty(A, eq.id, esc["truck"].id) == 1
    assert A.ItemUnit.query.get(units[0].id).location_id == esc["truck"].id


def test_unidad_sin_serial_cargado_bloquea_la_aprobacion(A, esc):
    """No se puede mover una unidad que el sistema no conoce.

    Queda como obligación de admin/supervisor cargar el serial antes de aprobar.
    """
    eq, units = _serializado(A, esc, "EQP-007", ["SN-1"])
    _enviar(_como(A, "tec"), esc, {esc["cable"]: 10, esc["ficha"]: 4},
            serials={eq: [units[0].id]}, sin_serial={eq: 2})
    sc = A.StockCount.query.one()
    linea = [ln for ln in sc.lines if ln.item_id == eq.id][0]
    assert linea.qty_contada == 3 and linea.qty_sin_serial == 2

    _como(A, "sup").post(f"/conteo-camioneta/{sc.id}/aprobar")
    A.db.session.expire_all()
    assert A.StockCount.query.one().status == A.STOCK_COUNT_PENDIENTE
    assert _qty(A, eq.id, esc["truck"].id) == 1     # no se tocó nada


def test_serializado_vacio_exige_tildar_no_tengo_ninguno(A, esc):
    """Con un selector no hay 'campo vacío': dejarlo sin tocar sería declarar 0.

    Que eso sea un acto consciente es lo mismo que pedir 0 en los no serializados.
    """
    eq, units = _serializado(A, esc, "EQP-008", ["SN-1"])
    _enviar(_como(A, "tec"), esc, {esc["cable"]: 10, esc["ficha"]: 4},
            serials={eq: []})
    assert A.StockCount.query.count() == 0

    _enviar(_como(A, "tec"), esc, {esc["cable"]: 10, esc["ficha"]: 4},
            serials={eq: []}, sin_ninguno=[eq])
    sc = A.StockCount.query.one()
    assert [ln for ln in sc.lines if ln.item_id == eq.id][0].qty_contada == 0


def test_no_se_puede_declarar_dos_veces_el_mismo_serial(A, esc):
    eq, units = _serializado(A, esc, "EQP-009", ["SN-1", "SN-2"])
    _enviar(_como(A, "tec"), esc, {esc["cable"]: 10, esc["ficha"]: 4},
            serials={eq: [units[0].id, units[0].id]})
    assert A.StockCount.query.count() == 0


def test_el_buscador_de_seriales_no_lista_los_de_otra_camioneta(A, esc):
    """Alcance decidido: los ajenos existen y se pueden declarar, pero sólo
    aparecen escribiendo el serial COMPLETO. No se le muestra a cada técnico el
    inventario de sus compañeros."""
    eq, _u = _serializado(A, esc, "EQP-010", ["SN-MIO"])
    _eq2, ajenos = _serializado(A, esc, "EQP-010B", ["SN-AJENO"], location=esc["truck2"])
    jaula_it, _j = _serializado(A, esc, "EQP-010C", ["SN-JAULA"], location=esc["jaula"])
    c = _como(A, "tec")

    # Sin texto: sólo lo suyo.
    rows = c.get(f"/api/conteo-camioneta/seriales?item_id={eq.id}"
                 f"&location_id={esc['truck'].id}").get_json()
    assert [r["serial"] for r in rows] == ["SN-MIO"]

    # La Jaula sí se lista.
    rows_j = c.get(f"/api/conteo-camioneta/seriales?item_id={jaula_it.id}"
                   f"&location_id={esc['truck'].id}").get_json()
    assert [r["serial"] for r in rows_j] == ["SN-JAULA"]

    # Búsqueda parcial NO trae el ajeno.
    parcial = c.get(f"/api/conteo-camioneta/seriales?item_id={_eq2.id}"
                    f"&location_id={esc['truck'].id}&q=SN-").get_json()
    assert parcial == []

    # Serial completo sí, y viene marcado como ajeno con su ubicación.
    exacto = c.get(f"/api/conteo-camioneta/seriales?item_id={_eq2.id}"
                   f"&location_id={esc['truck'].id}&q=SN-AJENO").get_json()
    assert len(exacto) == 1
    assert exacto[0]["ajeno"] is True
    assert exacto[0]["location"] == esc["truck2"].name


def test_serializado_con_stock_pero_sin_seriales_cargados(A, esc):
    """Stock viejo por cantidad, sin unidades. Existe en la base real.

    El técnico no tiene nada que elegir; tilda 'no tengo ninguno' y el ajuste se
    resuelve por cantidad, como cualquier otro ítem.
    """
    eq = make_item(A, code="EQP-011", name="Camara sin seriales")
    eq.serialized = True
    A.db.session.commit()
    A.upsert_stock(eq.id, esc["truck"].id, 3)
    A.db.session.commit()

    _enviar(_como(A, "tec"), esc, {esc["cable"]: 10, esc["ficha"]: 4},
            serials={eq: []}, sin_ninguno=[eq])
    sc = A.StockCount.query.one()
    _como(A, "sup").post(f"/conteo-camioneta/{sc.id}/aprobar")
    A.db.session.expire_all()
    assert A.StockCount.query.one().status == A.STOCK_COUNT_APROBADO
    assert _qty(A, eq.id, esc["truck"].id) == 0


def test_el_tecnico_no_ve_sistema_ni_diferencia_en_el_detalle(A, esc):
    """El detalle es la otra puerta por donde se puede filtrar el número del sistema.

    Si el técnico ve contra qué contó, el conteo de la semana que viene ya no
    mide nada. Ve sólo su propia columna y el puntaje.
    """
    A.upsert_stock(esc["cable"].id, esc["truck"].id, 127)  # 10 + 127 = 137
    A.db.session.commit()
    _enviar(_como(A, "tec"), esc, {esc["cable"]: 41, esc["ficha"]: 4})
    sc = A.StockCount.query.one()

    html_tec = _como(A, "tec").get(f"/conteo-camioneta/{sc.id}").get_data(as_text=True)
    assert "Conteo técnico" in html_tec
    assert "41" in html_tec          # lo que cargó él, sí
    assert "137" not in html_tec     # lo que dice el sistema, no
    assert "Diferencia" not in html_tec

    # El supervisor sí ve las tres columnas.
    html_sup = _como(A, "sup").get(f"/conteo-camioneta/{sc.id}").get_data(as_text=True)
    assert "137" in html_sup
    assert "Diferencia" in html_sup


def test_el_mensaje_de_campo_vacio_habla_de_conteo(A, esc):
    """El texto lo pone setCustomValidity desde el JS de la pantalla.

    Ese código se mudó del template a static/js/stock_count_new.js, así que se
    verifican las dos mitades: que la pantalla cargue ese archivo, y que el
    archivo tenga el mensaje.
    """
    html = _como(A, "tec").get(
        f"/conteo-camioneta/nuevo?location_id={esc['truck'].id}"
    ).get_data(as_text=True)
    assert "js/stock_count_new.js" in html
    with open("static/js/stock_count_new.js", encoding="utf-8") as fh:
        assert "Completá el conteo" in fh.read()


def test_pantallas_con_serializados_renderizan(A, esc):
    """El formulario y el detalle con ítems serializados, de punta a punta."""
    eq, units = _serializado(A, esc, "EQP-020", ["SN-AA", "SN-BB"])

    html = _como(A, "tec").get(
        f"/conteo-camioneta/nuevo?location_id={esc['truck'].id}"
    ).get_data(as_text=True)
    assert f'name="serial_unit_ids_{eq.id}"' in html
    assert "SN-AA" in html and "SN-BB" in html
    assert f'name="sin_ninguno_{eq.id}"' in html
    # Un serializado no lleva campo de cantidad: la cantidad son los seriales.
    assert f'name="contado_{eq.id}"' not in html

    _enviar(_como(A, "tec"), esc, {esc["cable"]: 10, esc["ficha"]: 4},
            serials={eq: [units[0].id]})
    sc = A.StockCount.query.one()

    # El supervisor ve el declarado y el que falta.
    html_sup = _como(A, "sup").get(f"/conteo-camioneta/{sc.id}").get_data(as_text=True)
    assert "SN-AA" in html_sup and "SN-BB" in html_sup and "falta" in html_sup
    # El técnico ve sólo lo que declaró él.
    html_tec = _como(A, "tec").get(f"/conteo-camioneta/{sc.id}").get_data(as_text=True)
    assert "SN-AA" in html_tec
    assert "SN-BB" not in html_tec


def test_unidad_sin_serial_se_resuelve_al_aprobar(A, esc):
    """La contracara del bloqueo: se cargan los seriales en la aprobación.

    Antes el cartel mandaba a Ítems → Seriales, donde NO se pueden cargar
    porque esa pantalla exige que el stock ya esté en la ubicación. El conteo
    quedaba imposible de aprobar: sólo se podía rechazar.
    """
    eq, units = _serializado(A, esc, "EQP-030", ["SN-1"])
    _enviar(_como(A, "tec"), esc, {esc["cable"]: 10, esc["ficha"]: 4},
            serials={eq: [units[0].id]}, sin_serial={eq: 2})
    sc = A.StockCount.query.one()
    linea = [ln for ln in sc.lines if ln.item_id == eq.id][0]
    c = _como(A, "sup")

    # Sin cargar los seriales sigue sin aprobarse.
    c.post(f"/conteo-camioneta/{sc.id}/aprobar")
    A.db.session.expire_all()
    assert A.StockCount.query.one().status == A.STOCK_COUNT_PENDIENTE
    assert _qty(A, eq.id, esc["truck"].id) == 1

    # Cargándolos, sí: se dan de alta y entran a la camioneta.
    c.post(f"/conteo-camioneta/{sc.id}/aprobar",
           data={f"nuevo_serial_{linea.id}": ["SN-NUEVO-1", "SN-NUEVO-2"]})
    A.db.session.expire_all()
    assert A.StockCount.query.one().status == A.STOCK_COUNT_APROBADO
    assert _qty(A, eq.id, esc["truck"].id) == 3
    for s in ("SN-NUEVO-1", "SN-NUEVO-2"):
        u = A.ItemUnit.query.filter_by(item_id=eq.id, serial=s).one()
        assert u.status == A.UNIT_EN_STOCK
        assert u.location_id == esc["truck"].id


def test_cargar_mal_los_seriales_no_aplica_nada(A, esc):
    """Todo o nada: si los seriales están mal, el conteo queda intacto."""
    eq, units = _serializado(A, esc, "EQP-031", ["SN-1"])
    _enviar(_como(A, "tec"), esc, {esc["cable"]: 7, esc["ficha"]: 4},
            serials={eq: [units[0].id]}, sin_serial={eq: 2})
    sc = A.StockCount.query.one()
    linea = [ln for ln in sc.lines if ln.item_id == eq.id][0]
    c = _como(A, "sup")

    # Faltan seriales (cargó 1 de 2).
    c.post(f"/conteo-camioneta/{sc.id}/aprobar",
           data={f"nuevo_serial_{linea.id}": ["SOLO-UNO"]})
    A.db.session.expire_all()
    assert A.StockCount.query.one().status == A.STOCK_COUNT_PENDIENTE
    assert A.ItemUnit.query.filter_by(serial="SOLO-UNO").count() == 0
    assert _qty(A, esc["cable"].id, esc["truck"].id) == 10   # ni el resto se tocó

    # Un serial que ya está en stock para ese ítem tampoco entra.
    c.post(f"/conteo-camioneta/{sc.id}/aprobar",
           data={f"nuevo_serial_{linea.id}": ["SN-1", "OTRO"]})
    A.db.session.expire_all()
    assert A.StockCount.query.one().status == A.STOCK_COUNT_PENDIENTE
    assert A.ItemUnit.query.filter_by(serial="OTRO").count() == 0


def test_el_detalle_ofrece_cargar_los_seriales_que_faltan(A, esc):
    eq, units = _serializado(A, esc, "EQP-032", ["SN-1"])
    _enviar(_como(A, "tec"), esc, {esc["cable"]: 10, esc["ficha"]: 4},
            serials={eq: [units[0].id]}, sin_serial={eq: 2})
    sc = A.StockCount.query.one()
    linea = [ln for ln in sc.lines if ln.item_id == eq.id][0]

    html = _como(A, "sup").get(f"/conteo-camioneta/{sc.id}").get_data(as_text=True)
    assert html.count(f'name="nuevo_serial_{linea.id}"') == 2
    # El técnico no ve nada de esto.
    html_tec = _como(A, "tec").get(f"/conteo-camioneta/{sc.id}").get_data(as_text=True)
    assert "nuevo_serial_" not in html_tec


# ---------------- observación de cada movimiento ----------------

def _obs(A, item_id, from_id=None, to_id=None):
    q = A.Movement.query.filter_by(item_id=item_id)
    if from_id is not None:
        q = q.filter_by(from_location_id=from_id)
    if to_id is not None:
        q = q.filter_by(to_location_id=to_id)
    return [m.observation for m in q.all()]


def test_cada_movimiento_explica_su_propio_motivo(A, esc):
    """El caso real de Ignacio: una cámara con tres movimientos distintos.

    Antes los tres decían lo mismo ("sistema 1 -> contado 2") y había que
    deducir qué pasó mirando origen y destino. Cada uno tiene que decir lo suyo.
    """
    eq, propios = _serializado(A, esc, "EQP-040", ["SN-QUEDA", "SN-FALTA"])
    # Un serial del mismo ítem, pero en la camioneta de otro técnico.
    A.upsert_stock(eq.id, esc["truck2"].id, 1)
    u_ajeno = A.ItemUnit(item_id=eq.id, serial="SN-AJENO", status=A.UNIT_EN_STOCK,
                         location_id=esc["truck2"].id)
    A.db.session.add(u_ajeno)
    A.db.session.commit()

    # Declara: el que ya tenía, el de la otra camioneta, y uno nuevo sin cargar.
    _enviar(_como(A, "tec"), esc, {esc["cable"]: 10, esc["ficha"]: 4},
            serials={eq: [propios[0].id, u_ajeno.id]}, sin_serial={eq: 1})
    sc = A.StockCount.query.one()
    linea = [ln for ln in sc.lines if ln.item_id == eq.id][0]
    _como(A, "sup").post(
        f"/conteo-camioneta/{sc.id}/aprobar",
        data={"confirmar_cruces": "1", f"nuevo_serial_{linea.id}": ["SN-NUEVO"]},
    )
    A.db.session.expire_all()
    assert A.StockCount.query.one().status == A.STOCK_COUNT_APROBADO

    baja = A.Location.query.filter_by(name=A.LOCATION_DESCARTES).first()
    prov = A.get_proveedor_location()

    # Vino de la camioneta del otro técnico.
    cruce = _obs(A, eq.id, from_id=esc["truck2"].id, to_id=esc["truck"].id)
    assert len(cruce) == 1
    assert "figuraba en Camioneta 2" in cruce[0]
    assert "SN-AJENO" in cruce[0]

    # El que el sistema tenía y no declaró: faltante a Descartes.
    falta = _obs(A, eq.id, from_id=esc["truck"].id, to_id=baja.id)
    assert len(falta) == 1
    assert "Faltante" in falta[0] and "no lo declaró" in falta[0]
    assert "SN-FALTA" in falta[0]

    # El alta del serial nuevo.
    alta = _obs(A, eq.id, from_id=prov.id, to_id=esc["truck"].id)
    assert len(alta) == 1
    assert "Serial nuevo" in alta[0] and "no existía en el sistema" in alta[0]
    assert "SN-NUEVO" in alta[0]

    # Los tres son distintos entre sí, que es todo el punto.
    assert len({cruce[0], falta[0], alta[0]}) == 3
    # Y todos siguen empezando por el número de conteo, para poder filtrarlos.
    for o in (cruce[0], falta[0], alta[0]):
        assert o.startswith(f"CONTEO {sc.number} ·")
        assert len(o) <= 255


def test_observacion_de_serial_que_venia_del_deposito(A, esc):
    eq, units = _serializado(A, esc, "EQP-041", ["SN-J"], location=esc["jaula"])
    _enviar(_como(A, "tec"), esc, {esc["cable"]: 10, esc["ficha"]: 4},
            serials={eq: [units[0].id]})
    sc = A.StockCount.query.one()
    _como(A, "sup").post(f"/conteo-camioneta/{sc.id}/aprobar")
    A.db.session.expire_all()
    obs = _obs(A, eq.id, from_id=esc["jaula"].id, to_id=esc["truck"].id)[0]
    assert "figuraba en el depósito" in obs


def test_observacion_de_sobrante_y_faltante_por_cantidad(A, esc):
    _enviar(_como(A, "tec"), esc, {esc["cable"]: 7, esc["ficha"]: 6})
    sc = A.StockCount.query.one()
    _como(A, "sup").post(f"/conteo-camioneta/{sc.id}/aprobar")
    A.db.session.expire_all()
    baja = A.Location.query.filter_by(name=A.LOCATION_DESCARTES).first()
    prov = A.get_proveedor_location()

    falt = _obs(A, esc["cable"].id, from_id=esc["truck"].id, to_id=baja.id)[0]
    assert "Faltante:" in falt and "contó 7" in falt and "tenía 10" in falt

    sobr = _obs(A, esc["ficha"].id, from_id=prov.id, to_id=esc["truck"].id)[0]
    assert "Sobrante:" in sobr and "contó 6" in sobr and "tenía 4" in sobr


def test_observacion_no_pasa_de_255_con_nombres_y_seriales_largos(A, esc):
    """Cota real: nombre completo largo, camioneta larga y serial largo."""
    esc["tec"].full_name = "Bruno Ezequiel Silva Rodríguez de la Cuadra"
    esc["truck2"].name = "Camioneta Berlingo YA (Peron - San vicente) - Zona Sur"
    A.db.session.commit()

    eq, _p = _serializado(A, esc, "EQP-042", ["SN-CORTO"])
    A.upsert_stock(eq.id, esc["truck2"].id, 1)
    u = A.ItemUnit(item_id=eq.id, serial="0012344234234234998877665544332211",
                   status=A.UNIT_EN_STOCK, location_id=esc["truck2"].id)
    A.db.session.add(u)
    A.db.session.commit()

    _enviar(_como(A, "tec"), esc, {esc["cable"]: 10, esc["ficha"]: 4},
            serials={eq: [u.id]})
    sc = A.StockCount.query.one()
    _como(A, "sup").post(f"/conteo-camioneta/{sc.id}/aprobar",
                         data={"confirmar_cruces": "1"})
    A.db.session.expire_all()
    for m in A.Movement.query.all():
        assert m.observation is None or len(m.observation) <= 255
