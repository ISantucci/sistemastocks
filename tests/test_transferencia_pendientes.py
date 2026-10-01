"""Transferencia de pendientes entre personas (2026-10-01).

Pedido tras la capacitación: que los técnicos se puedan pasar pendientes "de la
misma manera que se transfieren los ítems, con el sistema de notificación y
todo". Decisiones de Ignacio:

- la transferencia SOLO mueve el pendiente: cambia a nombre de quién está; no
  mueve stock ni cambia la ubicación;
- se transfiere a una PERSONA (un técnico), no a una ubicación;
- la pueden hacer todos: el técnico con los suyos, admin/supervisor con
  cualquiera.

Quien recibe ve un badge "+N" en Pendientes y, al entrar, un popup con lo que
le pasaron; "Entendido" lo marca. Es un aviso, no una aceptación.

Ojo con los clientes de prueba: Flask-Login cachea el usuario en `g` y todas
las requests comparten el app context del fixture. Por eso cada acción usa un
cliente recién logueado (_como) justo antes de usarlo.
"""
import pytest
from conftest import make_user, make_item, make_category, make_location, login


@pytest.fixture()
def esc(A):
    A.app.config["WTF_CSRF_ENABLED"] = False
    cat = make_category(A, "Placas", "PLA")
    juan = make_user(A, "juan", "TECNICO", full_name="Juan Perez")
    ana = make_user(A, "ana", "TECNICO", full_name="Ana Gomez")
    pedro = make_user(A, "pedro", "TECNICO", full_name="Pedro Diaz")
    make_user(A, "sup", "SUPERVISOR")
    make_user(A, "lec", "LECTOR")
    jaula = make_location(A, A.LOCATION_JAULA_TNG)
    make_location(A, A.LOCATION_RECUPERADO, is_external=True)
    c1 = make_location(A, "Camioneta Juan", is_truck=True)
    c2 = make_location(A, "Camioneta Ana", is_truck=True)
    A.db.session.add(A.LocationResponsible(location_id=c1.id, user_id=juan.id))
    A.db.session.add(A.LocationResponsible(location_id=c2.id, user_id=ana.id))
    placa = make_item(A, code="PLA-001", name="Placa madre", category=cat)
    fuente = make_item(A, code="PLA-002", name="Fuente", category=cat)
    A.db.session.commit()
    return {"juan": juan, "ana": ana, "pedro": pedro, "jaula": jaula,
            "c1": c1, "c2": c2, "placa": placa, "fuente": fuente}


def _como(A, username, password="pass1234"):
    c = A.app.test_client()
    login(c, username, password)
    return c


def _entrega(A, esc, item, to="juan", qty=1):
    """Entrega Jaula -> camioneta de Juan con un pendiente, armada por ORM."""
    y, seq, number = A.next_movement_number()
    m = A.Movement(item_id=item.id, qty=qty,
                   from_location_id=esc["jaula"].id, to_location_id=esc["c1"].id,
                   user_id=1, observation="entrega", year=y, seq=seq, number=number)
    A.db.session.add(m)
    A.db.session.flush()
    p = A.PendingDelivery(movement_id=m.id, responsible_from_id=1,
                          responsible_to_id=esc[to].id, item_id=item.id, return_qty=1,
                          comment="traer la fallada")
    A.db.session.add(p)
    A.upsert_stock(item.id, esc["c1"].id, qty)
    A.db.session.commit()
    return p


def _sin_entrega(A, esc, item, to="juan"):
    r = A.PendingReturn(location_id=esc["c1"].id, responsible_from_id=1,
                        responsible_to_id=esc[to].id, item_id=item.id, return_qty=1,
                        comment="usó la de su camioneta")
    A.db.session.add(r)
    A.db.session.commit()
    return r


def _transferir(c, to_user, *refs):
    return c.post("/pending-deliveries/transferir", data={
        "to_user_id": str(to_user.id), "pending_ref": list(refs),
    })


def _resp(A, model, pid):
    return model.query.get(pid).responsible_to_id


# ==========================================================================
# Lo que hace (y lo que NO hace)
# ==========================================================================

def test_el_tecnico_transfiere_su_pendiente_a_otro_tecnico(A, esc):
    p = _entrega(A, esc, esc["placa"])
    movs = A.Movement.query.count()
    stock = A.Stock.query.filter_by(item_id=esc["placa"].id, location_id=esc["c1"].id).one().quantity

    _transferir(_como(A, "juan"), esc["ana"], f"delivery:{p.id}")

    assert _resp(A, A.PendingDelivery, p.id) == esc["ana"].id
    t = A.PendingTransfer.query.one()
    assert (t.pending_kind, t.pending_id) == ("delivery", p.id)
    assert t.from_user_id == esc["juan"].id and t.to_user_id == esc["ana"].id
    assert t.created_by_user_id == esc["juan"].id and t.seen_at is None
    # SOLO se mueve el pendiente.
    assert A.Movement.query.count() == movs
    assert A.Stock.query.filter_by(item_id=esc["placa"].id,
                                   location_id=esc["c1"].id).one().quantity == stock
    assert A.PendingDelivery.query.get(p.id).movement.to_location_id == esc["c1"].id
    assert A.PendingDelivery.query.get(p.id).responsible_from_id == 1   # quién lo generó no cambia


def test_varios_de_los_dos_tipos_en_una_sola_transferencia(A, esc):
    p1 = _entrega(A, esc, esc["placa"])
    p2 = _entrega(A, esc, esc["fuente"])
    r = _sin_entrega(A, esc, esc["placa"])
    _transferir(_como(A, "juan"), esc["ana"],
                f"delivery:{p1.id}", f"delivery:{p2.id}", f"return:{r.id}")
    assert _resp(A, A.PendingDelivery, p1.id) == esc["ana"].id
    assert _resp(A, A.PendingDelivery, p2.id) == esc["ana"].id
    assert _resp(A, A.PendingReturn, r.id) == esc["ana"].id
    assert A.PendingReturn.query.get(r.id).location_id == esc["c1"].id   # la ubicación no cambia
    assert A.PendingTransfer.query.count() == 3


def test_admin_y_supervisor_transfieren_cualquiera(A, esc):
    p1 = _entrega(A, esc, esc["placa"])
    p2 = _entrega(A, esc, esc["fuente"])
    _transferir(_como(A, "admin", "admin123"), esc["ana"], f"delivery:{p1.id}")
    _transferir(_como(A, "sup"), esc["pedro"], f"delivery:{p2.id}")
    assert _resp(A, A.PendingDelivery, p1.id) == esc["ana"].id
    assert _resp(A, A.PendingDelivery, p2.id) == esc["pedro"].id
    t = A.PendingTransfer.query.filter_by(pending_id=p1.id).one()
    assert t.from_user_id == esc["juan"].id and t.created_by_user_id == 1


# ==========================================================================
# Permisos y validaciones (todo o nada)
# ==========================================================================

def test_el_tecnico_no_puede_transferir_uno_ajeno_ni_mezclado(A, esc):
    propio = _entrega(A, esc, esc["placa"])
    ajeno = _entrega(A, esc, esc["fuente"], to="pedro")
    _transferir(_como(A, "juan"), esc["ana"], f"delivery:{propio.id}", f"delivery:{ajeno.id}")
    assert _resp(A, A.PendingDelivery, propio.id) == esc["juan"].id   # tampoco el propio
    assert _resp(A, A.PendingDelivery, ajeno.id) == esc["pedro"].id
    assert A.PendingTransfer.query.count() == 0


def test_solo_a_tecnicos(A, esc):
    p = _entrega(A, esc, esc["placa"])
    sup = A.User.query.filter_by(username="sup").one()
    lec = A.User.query.filter_by(username="lec").one()
    admin = A.User.query.get(1)
    for dest in (sup, lec, admin):
        _transferir(_como(A, "juan"), dest, f"delivery:{p.id}")
        assert _resp(A, A.PendingDelivery, p.id) == esc["juan"].id
    _como(A, "juan").post("/pending-deliveries/transferir", data={
        "to_user_id": "9999", "pending_ref": [f"delivery:{p.id}"]})
    assert _resp(A, A.PendingDelivery, p.id) == esc["juan"].id
    assert A.PendingTransfer.query.count() == 0


def test_no_se_transfiere_un_devuelto_ni_un_anulado(A, esc):
    dev = _entrega(A, esc, esc["placa"])
    anu = _entrega(A, esc, esc["fuente"])
    dev.returned = True
    anu.cancelled_at = A.now_ar()
    anu.cancel_reason = "error"
    A.db.session.commit()
    for p in (dev, anu):
        _transferir(_como(A, "admin", "admin123"), esc["ana"], f"delivery:{p.id}")
        assert _resp(A, A.PendingDelivery, p.id) == esc["juan"].id
    assert A.PendingTransfer.query.count() == 0


def test_no_se_transfiere_a_quien_ya_lo_tiene(A, esc):
    p = _entrega(A, esc, esc["placa"])
    _transferir(_como(A, "admin", "admin123"), esc["juan"], f"delivery:{p.id}")
    assert A.PendingTransfer.query.count() == 0


def test_lector_no_puede(A, esc):
    p = _entrega(A, esc, esc["placa"])
    r = _transferir(_como(A, "lec"), esc["ana"], f"delivery:{p.id}")
    assert r.status_code in (302, 403)
    assert _resp(A, A.PendingDelivery, p.id) == esc["juan"].id


def test_sin_marcar_nada_o_con_basura_no_hace_nada(A, esc):
    p = _entrega(A, esc, esc["placa"])
    _transferir(_como(A, "juan"), esc["ana"])
    _transferir(_como(A, "juan"), esc["ana"], "delivery:abc", "otro:1", f"delivery:{p.id + 99}")
    assert _resp(A, A.PendingDelivery, p.id) == esc["juan"].id
    assert A.PendingTransfer.query.count() == 0


# ==========================================================================
# Aviso: badge, popup, Entendido
# ==========================================================================

def test_quien_recibe_ve_badge_y_popup_y_quien_envia_no(A, esc):
    p = _entrega(A, esc, esc["placa"])
    _transferir(_como(A, "juan"), esc["ana"], f"delivery:{p.id}")

    html = _como(A, "ana").get("/pending-deliveries").get_data(as_text=True)
    assert 'id="modal-pending-transfer"' in html
    assert "Juan Perez" in html and "te ha transferido el siguiente pendiente" in html
    assert "PLA-001 - Placa madre" in html
    assert "nav-badge-new" in html and ">+1<" in html

    html = _como(A, "juan").get("/pending-deliveries").get_data(as_text=True)
    assert 'id="modal-pending-transfer"' not in html
    assert "nav-badge-new" not in html
    assert "traer la fallada" not in html     # ya no está a su nombre: no lo ve


def test_el_popup_dice_de_quien_era_si_lo_transfirio_admin(A, esc):
    p = _entrega(A, esc, esc["placa"])
    _transferir(_como(A, "admin", "admin123"), esc["ana"], f"delivery:{p.id}")
    html = _como(A, "ana").get("/pending-deliveries").get_data(as_text=True)
    assert "a nombre de Juan Perez" in html


def test_entendido_lo_marca_y_desaparece(A, esc):
    p = _entrega(A, esc, esc["placa"])
    _transferir(_como(A, "juan"), esc["ana"], f"delivery:{p.id}")
    t = A.PendingTransfer.query.one()

    # Ver la pantalla no lo marca (la X tampoco: es solo cerrar el modal).
    _como(A, "ana").get("/pending-deliveries")
    assert A.PendingTransfer.query.get(t.id).seen_at is None

    _como(A, "ana").post("/pending-deliveries/avisos-transferencia/visto",
                         data={"notice_id": [str(t.id)]})
    t = A.PendingTransfer.query.get(t.id)
    assert t.seen_at is not None and t.seen_by_user_id == esc["ana"].id
    html = _como(A, "ana").get("/pending-deliveries").get_data(as_text=True)
    assert 'id="modal-pending-transfer"' not in html and "nav-badge-new" not in html


def test_entendido_con_un_aviso_ajeno_no_hace_nada(A, esc):
    p = _entrega(A, esc, esc["placa"])
    _transferir(_como(A, "juan"), esc["ana"], f"delivery:{p.id}")
    t = A.PendingTransfer.query.one()
    _como(A, "pedro").post("/pending-deliveries/avisos-transferencia/visto",
                           data={"notice_id": [str(t.id)]})
    _como(A, "admin", "admin123").post("/pending-deliveries/avisos-transferencia/visto",
                                       data={"notice_id": [str(t.id)]})
    assert A.PendingTransfer.query.get(t.id).seen_at is None


def test_si_lo_vuelve_a_pasar_el_aviso_es_del_nuevo(A, esc):
    p = _entrega(A, esc, esc["placa"])
    _transferir(_como(A, "juan"), esc["ana"], f"delivery:{p.id}")
    _transferir(_como(A, "ana"), esc["pedro"], f"delivery:{p.id}")
    assert _resp(A, A.PendingDelivery, p.id) == esc["pedro"].id
    with A.app.test_request_context():
        assert A.open_pending_transfer_notices(esc["ana"]) == []
        assert len(A.open_pending_transfer_notices(esc["pedro"])) == 1


def test_el_aviso_no_aparece_si_el_pendiente_ya_se_cerro_o_anulo(A, esc):
    p = _entrega(A, esc, esc["placa"])
    _transferir(_como(A, "juan"), esc["ana"], f"delivery:{p.id}")
    p = A.PendingDelivery.query.get(p.id)
    p.cancelled_at = A.now_ar()
    p.cancel_reason = "error"
    A.db.session.commit()
    with A.app.test_request_context():
        assert A.open_pending_transfer_notices(esc["ana"]) == []


def test_el_badge_de_pendientes_sigue_a_la_persona(A, esc):
    p = _entrega(A, esc, esc["placa"])
    _entrega(A, esc, esc["fuente"])
    _transferir(_como(A, "juan"), esc["ana"], f"delivery:{p.id}")
    assert A.count_open_pendings(responsible_to_id=esc["juan"].id) == 1
    assert A.count_open_pendings(responsible_to_id=esc["ana"].id) == 1


# ==========================================================================
# Después de transferir
# ==========================================================================

def test_el_cierre_sigue_igual_y_por_default_devuelve_el_nuevo(A, esc):
    """Solo se movió el pendiente: "del stock del técnico" sigue descontando de
    la ubicación de la entrega (la camioneta de Juan)."""
    p = _entrega(A, esc, esc["placa"])
    _transferir(_como(A, "juan"), esc["ana"], f"delivery:{p.id}")
    _como(A, "admin", "admin123").post("/pending-deliveries", data={
        "pending_id": str(p.id), "return_action": "return", "return_origin": "stock",
    })
    p = A.PendingDelivery.query.get(p.id)
    assert p.returned and p.returned_by_user_id == esc["ana"].id
    assert A.Stock.query.filter_by(item_id=esc["placa"].id,
                                   location_id=esc["c1"].id).one().quantity == 0


def test_revertir_la_entrega_se_lleva_el_historial_de_transferencias(A, esc):
    A.app.config["WTF_CSRF_ENABLED"] = False
    A.upsert_stock(esc["placa"].id, esc["jaula"].id, 1)
    A.db.session.commit()
    _como(A, "admin", "admin123").post("/movements", data={
        "item_id": str(esc["placa"].id), "qty": "1",
        "from_location_id": str(esc["jaula"].id), "to_location_id": str(esc["c1"].id),
        "generate_pending": "1", "pending_comment": "x",
    })
    p = A.PendingDelivery.query.one()
    _transferir(_como(A, "admin", "admin123"), esc["ana"], f"delivery:{p.id}")
    assert A.PendingTransfer.query.count() == 1
    _como(A, "admin", "admin123").post(f"/movements/{p.movement_id}/revertir",
                                       data={"reason": "error de carga"})
    assert A.PendingDelivery.query.count() == 0
    assert A.PendingTransfer.query.count() == 0


# ==========================================================================
# Pantalla
# ==========================================================================

def test_el_tecnico_ve_casillas_solo_en_lo_suyo_abierto_y_no_se_ofrece_a_si_mismo(A, esc):
    p = _entrega(A, esc, esc["placa"])
    dev = _entrega(A, esc, esc["fuente"])
    dev.returned = True
    A.db.session.commit()
    html = _como(A, "juan").get("/pending-deliveries").get_data(as_text=True)
    assert 'id="pending-transfer-form"' in html
    assert f'value="delivery:{p.id}"' in html
    assert f'value="delivery:{dev.id}"' not in html
    form = html[html.index('id="pending-transfer-form"'):]
    form = form[:form.index("</form>")]
    assert "Ana Gomez" in form and "Pedro Diaz" in form
    assert "Juan Perez" not in form


def test_admin_ve_casillas_en_todo_lo_abierto_y_la_marca_de_transferido(A, esc):
    p1 = _entrega(A, esc, esc["placa"])
    p2 = _entrega(A, esc, esc["fuente"], to="pedro")
    _transferir(_como(A, "admin", "admin123"), esc["ana"], f"delivery:{p1.id}")
    html = _como(A, "admin", "admin123").get("/pending-deliveries").get_data(as_text=True)
    assert f'value="delivery:{p1.id}"' in html and f'value="delivery:{p2.id}"' in html
    assert "de Juan Perez" in html                       # la marca de la transferencia
    assert 'id="modal-pending-transfer"' not in html     # el popup es de quien recibe


def test_sin_nada_para_transferir_no_hay_barra(A, esc):
    html = _como(A, "juan").get("/pending-deliveries").get_data(as_text=True)
    assert 'id="pending-transfer-form"' not in html


def test_la_tabla_nueva_la_crea_el_arranque(A):
    from sqlalchemy import inspect
    insp = inspect(A.db.engine)
    assert "pending_transfers" in insp.get_table_names()
    assert "repair_units" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("pending_transfers")}
    assert {"pending_kind", "pending_id", "from_user_id", "to_user_id",
            "created_by_user_id", "seen_at", "seen_by_user_id"} <= cols
