"""Pendientes de devolución SIN entrega, y anulación de pendientes.

El caso real: el técnico repara un equipo con un repuesto que YA tenía en su
camioneta y tiene que traer el que sacó. Como no se le entregó nada, no había
movimiento del que colgar un pendiente y el sistema no tenía cómo registrar
esa deuda. Ahora ADMIN/SUPERVISOR la generan desde Pendientes.

Y cualquier pendiente abierto (con o sin entrega) se puede ANULAR: deja de ser
deuda, no mueve stock y no se borra.

Las pruebas cubren sobre todo lo que NO tiene que pasar: que generar o anular
no toque stock, que el TÉCNICO no pueda hacerlo por POST directo, que un
anulado no se cierre ni se cuente como abierto, y que los pendientes de
siempre sigan cerrando igual.
"""
import sqlite3

import pytest
from conftest import make_user, make_item, make_category, make_location, login


@pytest.fixture()
def esc(A):
    A.app.config["WTF_CSRF_ENABLED"] = False
    cat = make_category(A, "Placas", "PLA")
    tec = make_user(A, "tec", "TECNICO", full_name="Tecnico Uno")
    tec2 = make_user(A, "tec2", "TECNICO", full_name="Tecnico Dos")
    make_user(A, "sup", "SUPERVISOR")
    make_user(A, "lec", "LECTOR")

    jaula = make_location(A, A.LOCATION_JAULA_TNG)
    truck = make_location(A, "Camioneta Uno", is_truck=True)
    truck2 = make_location(A, "Camioneta Dos", is_truck=True)
    make_location(A, A.LOCATION_RECUPERADO, is_external=True)
    make_location(A, A.LOCATION_EN_REPARACION)
    A.db.session.add(A.LocationResponsible(location_id=truck.id, user_id=tec.id))
    A.db.session.add(A.LocationResponsible(location_id=truck2.id, user_id=tec2.id))

    placa = make_item(A, code="PLA-001", name="Placa madre", category=cat)
    cam = make_item(A, code="PLA-900", name="Camara serializada", category=cat)
    cam.serialized = True
    A.upsert_stock(placa.id, jaula.id, 10)
    A.db.session.commit()
    return {"jaula": jaula, "truck": truck, "truck2": truck2, "placa": placa,
            "cam": cam, "tec": tec, "tec2": tec2}


def _cli(A, user="admin", password=None):
    c = A.app.test_client()
    login(c, user, password or ("admin123" if user == "admin" else "pass1234"))
    return c


def _crear(c, esc, qty=1, item=None, truck=None, tec=None, comment="Traer la placa en falla"):
    truck = truck or esc["truck"]
    tec = tec or esc["tec"]
    item = item or esc["placa"]
    return c.post("/pending-deliveries", data={
        "action": "create_return",
        "holder": f"{truck.id}:{tec.id}",
        "item_id": str(item.id),
        "qty": str(qty),
        "comment": comment,
    }, follow_redirects=True)


def _stock(A, item_id, loc_id):
    row = A.Stock.query.filter_by(item_id=item_id, location_id=loc_id).first()
    return row.quantity if row else 0


def _loc(A, nombre):
    return A.Location.query.filter_by(name=nombre).first()


def _cerrar(c, pr, **extra):
    data = {"pending_kind": "return", "pending_id": str(pr.id), "return_action": "return"}
    data.update(extra)
    return c.post("/pending-deliveries", data=data, follow_redirects=True)


def _anular(c, p, kind, reason="Cargado por error"):
    return c.post("/pending-deliveries", data={
        "action": "cancel", "pending_kind": kind, "pending_id": str(p.id),
        "cancel_reason": reason,
    }, follow_redirects=True)


def _pendiente_con_entrega(A, esc, qty=2, comment="entrega"):
    """Entrega Jaula -> camioneta con sus pendientes, por HTTP (como en la vida real)."""
    c = _cli(A)
    c.post("/movements", data={
        "item_id": str(esc["placa"].id), "qty": str(qty),
        "from_location_id": str(esc["jaula"].id), "to_location_id": str(esc["truck"].id),
        "generate_pending": "1", "pending_comment": comment,
    })
    return c


# ==========================================================================
# Alta
# ==========================================================================

def test_admin_genera_pendiente_sin_entrega_sin_tocar_stock(A, esc):
    movs = A.Movement.query.count()
    antes = (_stock(A, esc["placa"].id, esc["jaula"].id), _stock(A, esc["placa"].id, esc["truck"].id))

    r = _crear(_cli(A), esc, qty=2)
    assert "Pendiente de devolución generado" in r.get_data(as_text=True)

    prs = A.PendingReturn.query.all()
    assert len(prs) == 2                                   # uno por unidad
    assert all(p.return_qty == 1 for p in prs)
    assert all(p.responsible_to_id == esc["tec"].id for p in prs)
    assert all(p.location_id == esc["truck"].id for p in prs)
    assert all(p.responsible_from_id == 1 for p in prs)    # admin
    assert all(p.comment == "Traer la placa en falla" for p in prs)
    # Generar es solo la deuda: ni movimientos ni stock.
    assert A.Movement.query.count() == movs
    assert (_stock(A, esc["placa"].id, esc["jaula"].id), _stock(A, esc["placa"].id, esc["truck"].id)) == antes
    assert A.PendingDelivery.query.count() == 0


def test_supervisor_tambien_puede_generarlo(A, esc):
    _crear(_cli(A, "sup"), esc)
    assert A.PendingReturn.query.count() == 1


def test_tecnico_no_puede_generarlo_por_post_directo(A, esc):
    r = _crear(_cli(A, "tec"), esc)
    assert A.PendingReturn.query.count() == 0
    assert "No tenés permisos" in r.get_data(as_text=True)


def test_lector_no_puede_generarlo(A, esc):
    c = _cli(A, "lec")
    r = c.post("/pending-deliveries", data={
        "action": "create_return", "holder": f"{esc['truck'].id}:{esc['tec'].id}",
        "item_id": str(esc["placa"].id), "qty": "1", "comment": "x",
    })
    assert r.status_code in (302, 403)
    assert A.PendingReturn.query.count() == 0


def test_responsable_que_no_es_de_esa_ubicacion_se_rechaza(A, esc):
    """El select se puede adulterar: el backend valida la pareja ubicación/responsable."""
    r = _crear(_cli(A), esc, truck=esc["truck"], tec=esc["tec2"])
    assert A.PendingReturn.query.count() == 0
    assert "no es responsable" in r.get_data(as_text=True)


def test_ubicacion_externa_se_rechaza(A, esc):
    prov = _loc(A, A.LOCATION_RECUPERADO)
    A.db.session.add(A.LocationResponsible(location_id=prov.id, user_id=esc["tec"].id))
    A.db.session.commit()
    _crear(_cli(A), esc, truck=prov)
    assert A.PendingReturn.query.count() == 0


@pytest.mark.parametrize("campo, valor", [
    ("qty", "0"), ("qty", "101"), ("qty", "abc"), ("qty", "-1"),
    ("comment", ""), ("comment", "   "), ("item_id", ""), ("holder", ""),
])
def test_datos_invalidos_no_generan_nada(A, esc, campo, valor):
    data = {
        "action": "create_return", "holder": f"{esc['truck'].id}:{esc['tec'].id}",
        "item_id": str(esc["placa"].id), "qty": "1", "comment": "motivo",
    }
    data[campo] = valor
    _cli(A).post("/pending-deliveries", data=data)
    assert A.PendingReturn.query.count() == 0


def test_item_inactivo_se_rechaza(A, esc):
    esc["placa"].is_active = False
    A.db.session.commit()
    _crear(_cli(A), esc)
    assert A.PendingReturn.query.count() == 0


# ==========================================================================
# Lo que ve cada uno
# ==========================================================================

def test_tecnico_ve_los_suyos_y_no_los_ajenos(A, esc):
    c = _cli(A)
    _crear(c, esc, comment="MOTIVO-DEL-UNO")
    _crear(c, esc, truck=esc["truck2"], tec=esc["tec2"], comment="MOTIVO-DEL-DOS")

    html = _cli(A, "tec").get("/pending-deliveries").get_data(as_text=True)
    assert "MOTIVO-DEL-UNO" in html
    assert "MOTIVO-DEL-DOS" not in html          # filtro en la query, no en el template
    assert "Solo lectura" in html
    assert 'value="create_return"' not in html   # no ve el alta
    assert 'name="cancel_reason"' not in html    # ni el anular


def test_tecnico_no_ve_ajenos_aunque_filtre_por_otro(A, esc):
    _crear(_cli(A), esc, truck=esc["truck2"], tec=esc["tec2"], comment="MOTIVO-AJENO")
    html = _cli(A, "tec").get(
        f"/pending-deliveries?to_user_id={esc['tec2'].id}"
    ).get_data(as_text=True)
    assert "MOTIVO-AJENO" not in html


def test_admin_ve_el_alta_y_los_formularios(A, esc):
    _crear(_cli(A), esc, comment="MOTIVO-X")
    html = _cli(A).get("/pending-deliveries").get_data(as_text=True)
    assert 'value="create_return"' in html
    assert 'name="holder"' in html
    assert f'value="{esc["truck"].id}:{esc["tec"].id}"' in html
    assert "MOTIVO-X" in html
    assert 'name="pending_kind" value="return"' in html
    assert 'name="cancel_reason"' in html
    assert "js/pending_deliveries.js" in html


def test_contadores_suman_los_sin_entrega(A, esc):
    _crear(_cli(A), esc, qty=2)
    with A.app.test_request_context():
        assert A.count_open_pendings() == 2
        assert A.count_open_pendings(responsible_to_id=esc["tec"].id) == 2
        assert A.count_open_pendings(responsible_to_id=esc["tec2"].id) == 0
        filas = A.open_pendings_by_user()
        assert [(u.id, n) for u, n in filas] == [(esc["tec"].id, 2)]


def test_metricas_y_inicio_siguen_andando(A, esc):
    _pendiente_con_entrega(A, esc)
    c = _cli(A)
    _crear(c, esc)
    pr = A.PendingReturn.query.first()
    _anular(c, pr, "return")
    for url in ("/", "/metricas", "/metricas/reparaciones"):
        assert c.get(url).status_code == 200, url


# ==========================================================================
# Cierre
# ==========================================================================

def test_cierre_por_default_es_recuperado_del_equipo_y_no_descuenta(A, esc):
    """EL CASO REAL: la placa buena la usó de su camioneta; la que vuelve sale del equipo."""
    _crear(_cli(A), esc)
    pr = A.PendingReturn.query.first()
    jaula_antes = _stock(A, esc["placa"].id, esc["jaula"].id)

    r = _cerrar(_cli(A), pr)
    assert "Pendiente cerrado" in r.get_data(as_text=True)

    pr = A.PendingReturn.query.get(pr.id)
    assert pr.returned is True and pr.returned_at is not None
    assert pr.returned_by_user_id == esc["tec"].id
    assert _stock(A, esc["placa"].id, esc["truck"].id) == 0          # no descontó
    assert _stock(A, esc["placa"].id, esc["jaula"].id) == jaula_antes + 1
    m = pr.return_movement
    assert m is not None
    assert m.from_location_id == _loc(A, A.LOCATION_RECUPERADO).id
    assert m.to_location_id == esc["jaula"].id
    assert "Recuperado en campo" in m.observation
    assert "pendiente sin entrega" in m.observation


def test_cierre_desde_el_stock_del_tecnico_descuenta_la_camioneta(A, esc):
    A.upsert_stock(esc["placa"].id, esc["truck"].id, 1)
    A.db.session.commit()
    _crear(_cli(A), esc)
    pr = A.PendingReturn.query.first()

    _cerrar(_cli(A), pr, return_origin="stock")

    assert A.PendingReturn.query.get(pr.id).returned is True
    assert _stock(A, esc["placa"].id, esc["truck"].id) == 0
    assert A.PendingReturn.query.get(pr.id).return_movement.from_location_id == esc["truck"].id


def test_cierre_desde_stock_sin_stock_falla_sin_dejar_nada_a_medias(A, esc):
    _crear(_cli(A), esc)
    pr = A.PendingReturn.query.first()
    movs = A.Movement.query.count()

    r = _cerrar(_cli(A), pr, return_origin="stock")

    assert "No se pudo cerrar" in r.get_data(as_text=True)
    assert A.PendingReturn.query.get(pr.id).returned is False
    assert A.Movement.query.count() == movs


def test_cierre_a_reparacion(A, esc):
    _crear(_cli(A), esc)
    pr = A.PendingReturn.query.first()
    _cerrar(_cli(A), pr, return_action="repair")

    rep = A.Repair.query.one()
    assert rep.status == "EN_REPARACION" and rep.pending_id is None
    assert _stock(A, esc["placa"].id, _loc(A, A.LOCATION_EN_REPARACION).id) == 1
    assert A.PendingReturn.query.get(pr.id).returned is True


def test_cierre_a_descartes_exige_motivo_y_deja_scrap(A, esc):
    _crear(_cli(A), esc)
    pr = A.PendingReturn.query.first()
    c = _cli(A)

    _cerrar(c, pr, return_action="scrap")                   # sin motivo
    assert A.PendingReturn.query.get(pr.id).returned is False
    assert A.Scrap.query.count() == 0

    _cerrar(c, pr, return_action="scrap", scrap_reason="Roto")
    assert A.PendingReturn.query.get(pr.id).returned is True
    s = A.Scrap.query.one()
    assert s.reason == "Roto" and s.source == "PENDIENTE"


def test_cierre_serializado_desde_el_equipo_carga_el_serial(A, esc):
    _crear(_cli(A), esc, item=esc["cam"])
    pr = A.PendingReturn.query.first()

    _cerrar(_cli(A), pr, return_origin="campo", unit_serial="SN-DEL-EQUIPO")

    u = A.ItemUnit.query.filter_by(serial="SN-DEL-EQUIPO").one()
    assert u.status == A.UNIT_EN_STOCK and u.location_id == esc["jaula"].id
    assert _stock(A, esc["cam"].id, esc["jaula"].id) == 1
    assert A.PendingReturn.query.get(pr.id).returned is True


def test_segundo_cierre_no_genera_otro_movimiento(A, esc):
    _crear(_cli(A), esc)
    pr = A.PendingReturn.query.first()
    c = _cli(A)
    _cerrar(c, pr)
    movs = A.Movement.query.count()
    _cerrar(c, pr)
    assert A.Movement.query.count() == movs


def test_tecnico_no_puede_cerrar_por_post_directo(A, esc):
    _crear(_cli(A), esc)
    pr = A.PendingReturn.query.first()
    _cerrar(_cli(A, "tec"), pr)
    assert A.PendingReturn.query.get(pr.id).returned is False


# ==========================================================================
# Anulación (los dos tipos)
# ==========================================================================

def test_anular_sin_entrega_no_mueve_stock_ni_borra(A, esc):
    _crear(_cli(A), esc)
    pr = A.PendingReturn.query.first()
    movs = A.Movement.query.count()
    jaula = _stock(A, esc["placa"].id, esc["jaula"].id)

    r = _anular(_cli(A, "sup"), pr, "return", "Se resolvió por otro lado")
    assert "Pendiente anulado" in r.get_data(as_text=True)

    pr = A.PendingReturn.query.get(pr.id)
    assert pr is not None                                        # no se borra
    assert pr.cancelled_at is not None and pr.returned is False
    assert pr.cancel_reason == "Se resolvió por otro lado"
    assert pr.cancelled_by.username == "sup"
    assert A.Movement.query.count() == movs
    assert _stock(A, esc["placa"].id, esc["jaula"].id) == jaula
    with A.app.test_request_context():
        assert A.count_open_pendings() == 0


def test_anular_pendiente_con_entrega(A, esc):
    """Los pendientes de siempre también se pueden anular, de a uno."""
    _pendiente_con_entrega(A, esc, qty=2)
    p1, p2 = A.PendingDelivery.query.order_by(A.PendingDelivery.id).all()
    truck = _stock(A, esc["placa"].id, esc["truck"].id)
    movs = A.Movement.query.count()

    _anular(_cli(A), p1, "delivery")

    assert A.PendingDelivery.query.get(p1.id).cancelled_at is not None
    assert A.PendingDelivery.query.get(p2.id).cancelled_at is None
    assert _stock(A, esc["placa"].id, esc["truck"].id) == truck   # la mercadería sigue donde estaba
    assert A.Movement.query.count() == movs
    with A.app.test_request_context():
        assert A.count_open_pendings() == 1


def test_anular_exige_motivo(A, esc):
    _crear(_cli(A), esc)
    pr = A.PendingReturn.query.first()
    _anular(_cli(A), pr, "return", reason="  ")
    assert A.PendingReturn.query.get(pr.id).cancelled_at is None


def test_tecnico_no_puede_anular_por_post_directo(A, esc):
    _pendiente_con_entrega(A, esc, qty=1)
    _crear(_cli(A), esc)
    p = A.PendingDelivery.query.first()
    pr = A.PendingReturn.query.first()
    c = _cli(A, "tec")
    _anular(c, p, "delivery")
    _anular(c, pr, "return")
    assert A.PendingDelivery.query.get(p.id).cancelled_at is None
    assert A.PendingReturn.query.get(pr.id).cancelled_at is None


def test_un_anulado_no_se_puede_cerrar(A, esc):
    _pendiente_con_entrega(A, esc, qty=1)
    _crear(_cli(A), esc)
    p = A.PendingDelivery.query.first()
    pr = A.PendingReturn.query.first()
    c = _cli(A)
    _anular(c, p, "delivery")
    _anular(c, pr, "return")
    movs = A.Movement.query.count()

    c.post("/pending-deliveries", data={"pending_id": str(p.id), "return_action": "return"})
    _cerrar(c, pr)

    assert A.PendingDelivery.query.get(p.id).returned is False
    assert A.PendingReturn.query.get(pr.id).returned is False
    assert A.Movement.query.count() == movs


def test_un_devuelto_no_se_puede_anular(A, esc):
    _crear(_cli(A), esc)
    pr = A.PendingReturn.query.first()
    c = _cli(A)
    _cerrar(c, pr)
    r = _anular(c, pr, "return")
    assert "ya fue devuelto" in r.get_data(as_text=True)
    assert A.PendingReturn.query.get(pr.id).cancelled_at is None


def test_tipo_de_pendiente_invalido(A, esc):
    _crear(_cli(A), esc)
    pr = A.PendingReturn.query.first()
    _anular(_cli(A), pr, "otra-cosa")
    assert A.PendingReturn.query.get(pr.id).cancelled_at is None


def test_filtro_de_estado(A, esc):
    c = _cli(A)
    _crear(c, esc, comment="RET-ABIERTO")
    _crear(c, esc, comment="RET-ANULADO")
    _anular(c, A.PendingReturn.query.filter_by(comment="RET-ANULADO").one(), "return")

    html = c.get("/pending-deliveries?status=PENDIENTE").get_data(as_text=True)
    assert "RET-ABIERTO" in html and "RET-ANULADO" not in html
    html = c.get("/pending-deliveries?status=ANULADO").get_data(as_text=True)
    assert "RET-ANULADO" in html and "RET-ABIERTO" not in html
    assert "Anulado por" in html
    html = c.get("/pending-deliveries?status=DEVUELTO").get_data(as_text=True)
    assert "RET-ABIERTO" not in html and "RET-ANULADO" not in html


def test_filtro_anulado_en_pendientes_con_entrega(A, esc):
    _pendiente_con_entrega(A, esc, qty=1, comment="PD-ANULADO")
    _pendiente_con_entrega(A, esc, qty=1, comment="PD-ABIERTO")
    c = _cli(A)
    _anular(c, A.PendingDelivery.query.filter_by(comment="PD-ANULADO").one(), "delivery")

    html = c.get("/pending-deliveries?status=PENDIENTE").get_data(as_text=True)
    assert "PD-ABIERTO" in html and "PD-ANULADO" not in html
    html = c.get("/pending-deliveries?status=ANULADO").get_data(as_text=True)
    assert "PD-ANULADO" in html and "PD-ABIERTO" not in html


def test_paginacion_propia_del_listado_sin_entrega(A, esc):
    c = _cli(A)
    _crear(c, esc, comment="A")
    _crear(c, esc, comment="B")
    html = c.get("/pending-deliveries?limit=1").get_data(as_text=True)
    assert "rpage=2" in html


# ==========================================================================
# Lo que no tenía que cambiar / efectos en otras pantallas
# ==========================================================================

def test_revertir_la_entrega_no_borra_el_pendiente_anulado(A, esc):
    _pendiente_con_entrega(A, esc, qty=2)
    p1, p2 = A.PendingDelivery.query.order_by(A.PendingDelivery.id).all()
    c = _cli(A)
    _anular(c, p1, "delivery")
    m = A.Movement.query.first()

    c.post(f"/movements/{m.id}/revertir")

    assert A.Movement.query.get(m.id).reverted_at is not None
    assert A.PendingDelivery.query.get(p1.id) is not None     # anulado: queda como historial
    assert A.PendingDelivery.query.get(p2.id) is None         # abierto: se anula con el movimiento


def test_item_con_pendiente_sin_entrega_no_se_borra(A, esc):
    it = make_item(A, code="PLA-777", name="Sin historial")
    _crear(_cli(A), esc, item=it)
    _cli(A).post(f"/items/{it.id}/delete")
    assert A.Item.query.get(it.id) is not None


def test_cierre_con_entrega_sigue_igual(A, esc):
    """Regresión: el cierre de siempre descuenta de la camioneta y vuelve a la Jaula."""
    c = _pendiente_con_entrega(A, esc, qty=1)
    p = A.PendingDelivery.query.first()
    assert _stock(A, esc["placa"].id, esc["truck"].id) == 1

    r = c.post("/pending-deliveries", data={"pending_id": str(p.id), "return_action": "return"},
               follow_redirects=True)

    assert "Pendiente cerrado" in r.get_data(as_text=True)
    assert A.PendingDelivery.query.get(p.id).returned is True
    assert _stock(A, esc["placa"].id, esc["truck"].id) == 0
    assert _stock(A, esc["placa"].id, esc["jaula"].id) == 10
    obs = A.Movement.query.order_by(A.Movement.id.desc()).first().observation
    assert obs == f"Devolucion de pendiente #{p.id}"


def test_la_sync_de_esquema_actualiza_una_base_vieja(A):
    """Producción: al reiniciar, la base existente tiene que quedar al día sola.

    Se simula la base de antes de este cambio (pending_deliveries sin las
    columnas de anulación y sin la tabla pending_returns) y se corre el mismo
    camino que el arranque: ensure_sqlite_schema() + create_all().
    """
    A.db.session.remove()
    A.db.engine.dispose()
    viejas = ["id", "created_at", "movement_id", "responsible_from_id",
              "responsible_to_id", "item_id", "return_item_id", "return_qty",
              "comment", "returned", "returned_at", "returned_by_user_id"]
    con = sqlite3.connect(str(A.DB_PATH))
    con.execute("DROP TABLE pending_returns")
    con.execute("ALTER TABLE pending_deliveries RENAME TO _pd_nueva")
    con.execute(f"CREATE TABLE pending_deliveries AS SELECT {', '.join(viejas)} FROM _pd_nueva")
    con.execute("DROP TABLE _pd_nueva")
    con.commit()
    con.close()

    A.ensure_sqlite_schema()
    A.db.create_all()

    con = sqlite3.connect(str(A.DB_PATH))
    cols = {r[1] for r in con.execute("PRAGMA table_info(pending_deliveries)")}
    tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert {"cancelled_at", "cancelled_by_user_id", "cancel_reason"} <= cols
    assert set(viejas) <= cols                       # no se perdió ninguna columna
    assert "pending_returns" in tablas
