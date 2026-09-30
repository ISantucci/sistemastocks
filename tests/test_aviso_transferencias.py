"""Aviso de transferencia entre camionetas (2026-09-30).

Pedido de los técnicos tras la capacitación: cuando un técnico mueve algo de su
camioneta a la de otro, el que recibe ve un badge en "Stock" y, al entrar, un
popup: "<quién> te ha transferido los siguientes ítems:" con las cantidades
("el siguiente ítem" si es uno solo).

Decisiones de Ignacio:
- solo cuando el movimiento lo carga un TÉCNICO (no admin/supervisor);
- un aviso por movimiento, compartido por la camioneta: lo ven todos sus
  responsables y, cuando uno toca "Entendido", desaparece para todos.

Lo que se prueba, sobre todo lo que NO tiene que pasar: que el movimiento no
cambie, que no se avise de más (admin, camionetas propias, destino que no es
camioneta, movimiento fallido o revertido), que nadie marque como visto un
aviso ajeno, y que "Entendido" marque solo lo que se mostró.
"""
import re
import sqlite3

import pytest
from conftest import make_user, make_item, make_location, login


@pytest.fixture()
def esc(A):
    A.app.config["WTF_CSRF_ENABLED"] = False
    jaula = make_location(A, A.LOCATION_JAULA_TNG)
    t1 = make_location(A, "Camioneta Uno", is_truck=True)
    t2 = make_location(A, "Camioneta Dos", is_truck=True)
    t4 = make_location(A, "Camioneta Cuatro", is_truck=True)
    tec1 = make_user(A, "tec1", "TECNICO", full_name="Tecnico Uno")
    tec2 = make_user(A, "tec2", "TECNICO", full_name="Tecnico Dos")
    tec3 = make_user(A, "tec3", "TECNICO", full_name="Tecnico Tres")
    make_user(A, "tec4", "TECNICO", full_name="Tecnico Cuatro")
    make_user(A, "sup", "SUPERVISOR")
    make_user(A, "lec", "LECTOR")
    for loc, u in ((t1, tec1), (t4, tec1), (t2, tec2), (t2, tec3)):
        A.db.session.add(A.LocationResponsible(location_id=loc.id, user_id=u.id))
    cable = make_item(A, code="CAB-001", name="Cable UTP")
    domo = make_item(A, code="CAB-002", name="Domo")
    fibra = make_item(A, code="CAB-003", name="Fibra")
    fibra.unit = "metros"
    for it, q in ((cable, 10), (domo, 5), (fibra, 300)):
        A.upsert_stock(it.id, t1.id, q)
    A.db.session.commit()
    return {"jaula": jaula, "t1": t1, "t2": t2, "t4": t4, "tec1": tec1,
            "tec2": tec2, "tec3": tec3, "cable": cable, "domo": domo, "fibra": fibra}


def _cli(A, user):
    # Un cliente NUEVO por acción, a propósito: en los tests todas las requests
    # comparten el app context del fixture, y Flask-Login cachea el usuario en
    # `g`. Volver a usar un cliente después de loguear a otro correría la
    # request con el último usuario logueado. En producción no pasa (cada
    # request tiene su propio contexto).
    c = A.app.test_client()
    login(c, user, "admin123" if user == "admin" else "pass1234")
    return c


def _mover(A, user, item, qty, desde, hacia):
    c = _cli(A, user)
    return c.post("/movements", data={
        "item_id": str(item.id), "qty": str(qty),
        "from_location_id": str(desde.id), "to_location_id": str(hacia.id),
    }, follow_redirects=True)


def _txt(html):
    return re.sub(r"\s+", " ", html)


def _badge(A, user):
    html = _cli(A, user).get("/").get_data(as_text=True)
    m = re.search(r'title="(\d+) transferencia\(s\) recibida\(s\) sin ver"', html)
    return int(m.group(1)) if m else 0


def _popup(A, user):
    """Solo el HTML del popup (la pantalla de Stock tiene sus propios ítems)."""
    html = _cli(A, user).get("/stock").get_data(as_text=True)
    if 'id="modal-transfer-notice"' not in html:
        return ""
    return _txt(html.split('id="modal-transfer-notice"', 1)[1])


def _ids_del_popup(A, user):
    html = _cli(A, user).get("/stock").get_data(as_text=True)
    return re.findall(r'name="notice_id" value="(\d+)"', html)


def _stock(A, item_id, loc_id):
    row = A.Stock.query.filter_by(item_id=item_id, location_id=loc_id).first()
    return row.quantity if row else 0


# ==========================================================================
# Nace el aviso y lo ve quien recibe
# ==========================================================================

def test_transferencia_entre_tecnicos_genera_aviso(A, esc):
    r = _mover(A, "tec1", esc["cable"], 3, esc["t1"], esc["t2"])
    assert "registrado" in r.get_data(as_text=True)
    n = A.TransferNotice.query.one()
    m = A.Movement.query.one()
    assert (n.movement_id, n.location_id, n.seen_at) == (m.id, esc["t2"].id, None)
    # El movimiento es el de siempre.
    assert _stock(A, esc["cable"].id, esc["t1"].id) == 7
    assert _stock(A, esc["cable"].id, esc["t2"].id) == 3


def test_el_que_recibe_ve_badge_y_popup_en_singular(A, esc):
    _mover(A, "tec1", esc["cable"], 3, esc["t1"], esc["t2"])
    assert _badge(A, "tec2") == 1
    p = _popup(A, "tec2")
    assert "<strong>Tecnico Uno</strong> te ha transferido el siguiente ítem:" in p
    assert "CAB-001 - Cable UTP" in p
    assert re.search(r'CAB-001 - Cable UTP</td> <td class="num"[^>]*>3</td>', p)


def test_varios_items_en_plural_y_con_unidad(A, esc):
    _mover(A, "tec1", esc["cable"], 2, esc["t1"], esc["t2"])
    _mover(A, "tec1", esc["fibra"], 50, esc["t1"], esc["t2"])
    assert _badge(A, "tec2") == 2
    p = _popup(A, "tec2")
    assert "<strong>Tecnico Uno</strong> te ha transferido los siguientes ítems:" in p
    assert "CAB-003 - Fibra" in p and "50 metros" in p


def test_el_mismo_item_dos_veces_se_suma_en_una_linea(A, esc):
    _mover(A, "tec1", esc["cable"], 2, esc["t1"], esc["t2"])
    _mover(A, "tec1", esc["cable"], 3, esc["t1"], esc["t2"])
    p = _popup(A, "tec2")
    assert p.count("CAB-001 - Cable UTP") == 1
    assert re.search(r'CAB-001 - Cable UTP</td> <td class="num"[^>]*>5</td>', p)
    assert "el siguiente ítem" in p


def test_se_agrupa_por_quien_transfirio(A, esc):
    # tec3 comparte la Camioneta Dos; tec2 le manda algo a tec1.
    A.upsert_stock(esc["domo"].id, esc["t2"].id, 4)
    A.db.session.commit()
    _mover(A, "tec2", esc["domo"], 1, esc["t2"], esc["t1"])
    p = _popup(A, "tec1")
    assert "<strong>Tecnico Dos</strong> te ha transferido el siguiente ítem:" in p
    # Y a la Camioneta Dos le llega de tec1: tec3 ve el aviso de tec1, no el suyo.
    _mover(A, "tec1", esc["cable"], 1, esc["t1"], esc["t2"])
    p3 = _popup(A, "tec3")
    assert "Tecnico Uno" in p3 and "Tecnico Dos</strong> te ha" not in p3


def test_quien_envia_no_ve_nada(A, esc):
    _mover(A, "tec1", esc["cable"], 3, esc["t1"], esc["t2"])
    assert _badge(A, "tec1") == 0
    assert _popup(A, "tec1") == ""


def test_otro_tecnico_ajeno_no_ve_nada(A, esc):
    _mover(A, "tec1", esc["cable"], 3, esc["t1"], esc["t2"])
    assert _badge(A, "tec4") == 0
    assert _popup(A, "tec4") == ""


# ==========================================================================
# Camioneta compartida: un aviso para todos, uno lo cierra para todos
# ==========================================================================

def test_camioneta_compartida_lo_ven_los_dos(A, esc):
    _mover(A, "tec1", esc["cable"], 3, esc["t1"], esc["t2"])
    assert _badge(A, "tec2") == 1
    assert _badge(A, "tec3") == 1
    assert A.TransferNotice.query.count() == 1


def test_entendido_de_uno_lo_saca_para_los_dos(A, esc):
    _mover(A, "tec1", esc["cable"], 3, esc["t1"], esc["t2"])
    ids = _ids_del_popup(A, "tec2")
    r = _cli(A, "tec2").post("/stock/avisos-transferencia/visto", data={"notice_id": ids}, follow_redirects=True)
    assert r.status_code == 200
    n = A.TransferNotice.query.one()
    assert n.seen_at is not None and n.seen_by_user_id == esc["tec2"].id
    assert _badge(A, "tec2") == 0 and _badge(A, "tec3") == 0
    assert _popup(A, "tec3") == ""


def test_cerrar_sin_entendido_no_lo_marca(A, esc):
    _mover(A, "tec1", esc["cable"], 3, esc["t1"], esc["t2"])
    _popup(A, "tec2")
    _popup(A, "tec2")
    assert A.TransferNotice.query.one().seen_at is None
    assert _badge(A, "tec2") == 1


def test_entendido_marca_solo_lo_que_se_mostro(A, esc):
    _mover(A, "tec1", esc["cable"], 1, esc["t1"], esc["t2"])
    ids = _ids_del_popup(A, "tec2")          # tec2 ve el primero...
    _mover(A, "tec1", esc["domo"], 1, esc["t1"], esc["t2"])   # ...y llega otro
    _cli(A, "tec2").post("/stock/avisos-transferencia/visto", data={"notice_id": ids})
    assert _badge(A, "tec2") == 1
    p = _popup(A, "tec2")
    assert "CAB-002 - Domo" in p and "CAB-001" not in p


def test_el_segundo_entendido_no_pisa_al_primero(A, esc):
    _mover(A, "tec1", esc["cable"], 1, esc["t1"], esc["t2"])
    ids = _ids_del_popup(A, "tec2")
    _cli(A, "tec2").post("/stock/avisos-transferencia/visto", data={"notice_id": ids})
    visto = A.TransferNotice.query.one().seen_at
    _cli(A, "tec3").post("/stock/avisos-transferencia/visto", data={"notice_id": ids})
    A.db.session.expire_all()
    n = A.TransferNotice.query.one()
    assert n.seen_by_user_id == esc["tec2"].id and n.seen_at == visto


# ==========================================================================
# Nadie marca un aviso ajeno (validado en backend, no en el popup)
# ==========================================================================

def test_tecnico_ajeno_no_puede_marcar_por_post(A, esc):
    _mover(A, "tec1", esc["cable"], 3, esc["t1"], esc["t2"])
    nid = str(A.TransferNotice.query.one().id)
    _cli(A, "tec4").post("/stock/avisos-transferencia/visto", data={"notice_id": [nid]})
    # Ni siquiera el que lo envió.
    _cli(A, "tec1").post("/stock/avisos-transferencia/visto", data={"notice_id": [nid]})
    A.db.session.expire_all()
    assert A.TransferNotice.query.one().seen_at is None


@pytest.mark.parametrize("user", ["admin", "sup", "lec"])
def test_otros_roles_no_pueden_marcar(A, esc, user):
    _mover(A, "tec1", esc["cable"], 3, esc["t1"], esc["t2"])
    nid = str(A.TransferNotice.query.one().id)
    _cli(A, user).post("/stock/avisos-transferencia/visto", data={"notice_id": [nid]})
    A.db.session.expire_all()
    assert A.TransferNotice.query.one().seen_at is None


# ==========================================================================
# Cuándo NO hay aviso
# ==========================================================================

def test_admin_moviendo_entre_camionetas_no_avisa(A, esc):
    _mover(A, "admin", esc["cable"], 3, esc["t1"], esc["t2"])
    assert A.Movement.query.count() == 1
    assert A.TransferNotice.query.count() == 0
    assert _badge(A, "tec2") == 0


def test_entre_camionetas_propias_no_avisa(A, esc):
    _mover(A, "tec1", esc["cable"], 3, esc["t1"], esc["t4"])
    assert A.Movement.query.count() == 1
    assert A.TransferNotice.query.count() == 0


def test_destino_que_no_es_camioneta_no_avisa(A, esc):
    A.db.session.add(A.LocationResponsible(location_id=esc["jaula"].id, user_id=esc["tec2"].id))
    A.db.session.commit()
    _mover(A, "tec1", esc["cable"], 3, esc["t1"], esc["jaula"])
    assert A.TransferNotice.query.count() == 0


def test_movimiento_fallido_no_deja_aviso(A, esc):
    _mover(A, "tec1", esc["cable"], 999, esc["t1"], esc["t2"])
    assert A.Movement.query.count() == 0
    assert A.TransferNotice.query.count() == 0


def test_movimiento_revertido_no_se_muestra(A, esc):
    _mover(A, "tec1", esc["cable"], 3, esc["t1"], esc["t2"])
    m = A.Movement.query.one()
    _cli(A, "admin").post(f"/movements/{m.id}/revertir", follow_redirects=True)
    assert A.Movement.query.get(m.id).reverted_at is not None
    assert _badge(A, "tec2") == 0
    assert _popup(A, "tec2") == ""
    # El aviso no se borra: queda como historial.
    assert A.TransferNotice.query.count() == 1


def test_admin_y_lector_no_tienen_popup_ni_badge(A, esc):
    _mover(A, "tec1", esc["cable"], 3, esc["t1"], esc["t2"])
    for u in ("admin", "lec"):
        html = _cli(A, u).get("/stock").get_data(as_text=True)
        assert 'id="modal-transfer-notice"' not in html
        assert "transferencia(s) recibida(s)" not in html


# ==========================================================================
# Base: tabla nueva, la crea el arranque; limpieza admin la vacía
# ==========================================================================

def test_la_sync_de_esquema_crea_la_tabla_en_una_base_vieja(A, esc):
    _mover(A, "admin", esc["cable"], 1, esc["t1"], esc["t2"])
    A.db.session.remove()
    A.db.engine.dispose()
    con = sqlite3.connect(str(A.DB_PATH))
    con.execute("DROP TABLE transfer_notices")
    con.commit()
    con.close()

    A.ensure_sqlite_schema()
    A.db.create_all()

    con = sqlite3.connect(str(A.DB_PATH))
    tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    movs = con.execute("SELECT COUNT(*) FROM movements").fetchone()[0]
    con.close()
    assert "transfer_notices" in tablas
    assert movs == 1                                  # no se tocó nada existente


def test_clear_stock_vacia_los_avisos(A, esc):
    _mover(A, "tec1", esc["cable"], 3, esc["t1"], esc["t2"])
    assert A.TransferNotice.query.count() == 1
    _cli(A, "admin").post("/admin/clear-stock", data={"confirm_text": "BORRAR-STOCK"})
    assert A.Movement.query.count() == 0
    assert A.TransferNotice.query.count() == 0
