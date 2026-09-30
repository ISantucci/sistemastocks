"""Solicitud de compra con cualquier ítem, no solo los en alerta (2026-09-30).

Antes la solicitud de compra solo aceptaba ítems que estaban en alerta (bajo
stock_min). Pedido de Ignacio: poder cargar cualquier ítem, por ejemplo los
repuestos que pidieron los técnicos y no hay en la Jaula. Los repuestos NO se
precargan todavía: se agregan a mano en "Otros ítems".

Lo que se prueba:
- la tabla de alertas funciona exactamente igual;
- "Otros ítems" acepta cualquier ítem activo y valida cada fila;
- un ítem no puede quedar dos veces (ni entre secciones ni entre filas);
- la fila vacía se ignora; los inactivos no entran;
- el TÉCNICO y el LECTOR siguen sin poder crear (POST directo).
"""
import pytest
from conftest import make_user, make_item, make_location, login


@pytest.fixture()
def esc(A):
    A.app.config["WTF_CSRF_ENABLED"] = False
    jaula = make_location(A, A.LOCATION_JAULA_TNG)
    alerta = make_item(A, code="CAB-001", name="Cable en alerta", stock_min=5)
    normal = make_item(A, code="CAB-002", name="Fuente sin alerta")
    otro = make_item(A, code="CAB-003", name="Conector")
    baja = make_item(A, code="CAB-004", name="Dado de baja", is_active=False)
    A.upsert_stock(alerta.id, jaula.id, 1)
    A.db.session.commit()
    make_user(A, "tec", "TECNICO")
    make_user(A, "lec", "LECTOR")
    make_user(A, "sup", "SUPERVISOR")
    return {"alerta": alerta, "normal": normal, "otro": otro, "baja": baja}


def _cli(A, user="admin"):
    c = A.app.test_client()
    login(c, user, "admin123" if user == "admin" else "pass1234")
    return c


def _lineas(A):
    pr = A.PurchaseRequest.query.order_by(A.PurchaseRequest.id.desc()).first()
    return sorted((l.item_id, l.qty) for l in pr.lines) if pr else None


def test_solo_otros_items_sin_nada_en_alerta(A, esc):
    r = _cli(A).post("/solicitudes-compra/new", data={
        "extra_item_id[]": [str(esc["normal"].id), str(esc["otro"].id)],
        "extra_qty[]": ["3", "7"],
    }, follow_redirects=True)
    assert "Solicitud de compra creada" in r.get_data(as_text=True)
    assert _lineas(A) == sorted([(esc["normal"].id, 3), (esc["otro"].id, 7)])


def test_alertas_y_otros_en_la_misma_solicitud(A, esc):
    _cli(A).post("/solicitudes-compra/new", data={
        "item_id": str(esc["alerta"].id), f"qty_{esc['alerta'].id}": "10",
        "extra_item_id[]": [str(esc["normal"].id)], "extra_qty[]": ["2"],
    })
    assert _lineas(A) == sorted([(esc["alerta"].id, 10), (esc["normal"].id, 2)])


def test_solo_alertas_funciona_como_siempre(A, esc):
    _cli(A).post("/solicitudes-compra/new", data={
        "item_id": str(esc["alerta"].id), f"qty_{esc['alerta'].id}": "4",
    })
    assert _lineas(A) == [(esc["alerta"].id, 4)]


def test_la_tabla_de_alertas_sigue_ignorando_lo_que_no_esta_en_alerta(A, esc):
    # Por la tabla de alertas (item_id + qty_<id>) sigue sin entrar otra cosa:
    # lo que no está en alerta va por "Otros ítems".
    r = _cli(A).post("/solicitudes-compra/new", data={
        "item_id": str(esc["normal"].id), f"qty_{esc['normal'].id}": "4",
    }, follow_redirects=True)
    assert "Seleccioná al menos un ítem" in r.get_data(as_text=True)
    assert A.PurchaseRequest.query.count() == 0


def test_fila_vacia_se_ignora(A, esc):
    _cli(A).post("/solicitudes-compra/new", data={
        "extra_item_id[]": [str(esc["normal"].id), ""], "extra_qty[]": ["2", "1"],
    })
    assert _lineas(A) == [(esc["normal"].id, 2)]


def test_todo_vacio_avisa(A, esc):
    r = _cli(A).post("/solicitudes-compra/new", data={
        "extra_item_id[]": [""], "extra_qty[]": ["1"],
    }, follow_redirects=True)
    assert "Seleccioná al menos un ítem" in r.get_data(as_text=True)
    assert A.PurchaseRequest.query.count() == 0


def test_item_repetido_entre_alertas_y_otros_se_rechaza(A, esc):
    r = _cli(A).post("/solicitudes-compra/new", data={
        "item_id": str(esc["alerta"].id), f"qty_{esc['alerta'].id}": "10",
        "extra_item_id[]": [str(esc["alerta"].id)], "extra_qty[]": ["2"],
    }, follow_redirects=True)
    assert "está cargado dos veces" in r.get_data(as_text=True)
    assert A.PurchaseRequest.query.count() == 0


def test_item_repetido_entre_filas_se_rechaza(A, esc):
    r = _cli(A).post("/solicitudes-compra/new", data={
        "extra_item_id[]": [str(esc["normal"].id), str(esc["normal"].id)],
        "extra_qty[]": ["1", "2"],
    }, follow_redirects=True)
    assert "está cargado dos veces" in r.get_data(as_text=True)
    assert A.PurchaseRequest.query.count() == 0


@pytest.mark.parametrize("item_key,qty,msg", [
    ("baja", "1", "no existe o está dado de baja"),
    ("normal", "0", "tiene que ser mayor a 0"),
    ("normal", "abc", "tiene que ser mayor a 0"),
])
def test_filas_invalidas_frenan_todo(A, esc, item_key, qty, msg):
    r = _cli(A).post("/solicitudes-compra/new", data={
        "item_id": str(esc["alerta"].id), f"qty_{esc['alerta'].id}": "10",
        "extra_item_id[]": [str(esc[item_key].id)], "extra_qty[]": [qty],
    }, follow_redirects=True)
    assert msg in r.get_data(as_text=True)
    assert A.PurchaseRequest.query.count() == 0


def test_item_inexistente(A, esc):
    r = _cli(A).post("/solicitudes-compra/new", data={
        "extra_item_id[]": ["99999"], "extra_qty[]": ["1"],
    }, follow_redirects=True)
    assert "no existe o está dado de baja" in r.get_data(as_text=True)
    assert A.PurchaseRequest.query.count() == 0


@pytest.mark.parametrize("user", ["tec", "lec"])
def test_tecnico_y_lector_no_crean_por_post(A, esc, user):
    _cli(A, user).post("/solicitudes-compra/new", data={
        "extra_item_id[]": [str(esc["normal"].id)], "extra_qty[]": ["1"],
    })
    assert A.PurchaseRequest.query.count() == 0


def test_supervisor_puede(A, esc):
    _cli(A, "sup").post("/solicitudes-compra/new", data={
        "extra_item_id[]": [str(esc["normal"].id)], "extra_qty[]": ["1"],
    })
    assert _lineas(A) == [(esc["normal"].id, 1)]


def test_el_modal_ofrece_otros_items_sin_los_de_alerta_ni_inactivos(A, esc):
    html = _cli(A).get("/solicitudes-compra").get_data(as_text=True)
    assert "Otros ítems" in html
    otros = html.split('id="pr-extra-line-template"')[1].split("</template>")[0]
    assert "CAB-002 - Fuente sin alerta" in otros
    assert "CAB-003 - Conector" in otros
    assert "CAB-001" not in otros        # ya está en la tabla de alertas
    assert "CAB-004" not in otros        # dado de baja
    assert "js/purchase_requests.js" in html
    assert "Guardar solicitud" in html


def test_sin_alertas_igual_se_puede_guardar(A, esc):
    A.upsert_stock(esc["alerta"].id, A.get_jaula_location().id, 10)   # sale de alerta
    A.db.session.commit()
    html = _cli(A).get("/solicitudes-compra").get_data(as_text=True)
    assert "No hay ítems en alerta en este momento" in html
    assert "Guardar solicitud" in html
    assert "Destinatarios del mail" in html


def test_lector_no_recibe_el_modal(A, esc):
    html = _cli(A, "lec").get("/solicitudes-compra").get_data(as_text=True)
    assert "Otros ítems" not in html and "PR_DATA" not in html
