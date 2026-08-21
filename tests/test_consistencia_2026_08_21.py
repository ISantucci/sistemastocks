"""Correcciones de consistencia del 2026-08-21.

Cubre los parches aplicados tras la auditoría:
  A) el faltante de un conteo genera `Scrap` (antes se perdía del historial)
     y la pantalla /descartes lo muestra.
  B) el responsable de un pendiente ya no se elige con `.first()`: con varios
     responsables hay que elegir, y se registra quién devuelve.
  C) el backend sigue rechazando devolver más de lo entregado (el tope nuevo
     del formulario es ayuda visual, no reemplaza esta validación).
  E) una cantidad contada inválida corta el conteo con aviso, no se ignora.
  F) el ajuste de stock ya no pide motivo.
  G) un pendiente viejo con `return_qty` NULL devuelve una cantidad coherente
     y queda completado al cerrarse.
"""
import pytest

from conftest import make_user, make_item, make_location, login


@pytest.fixture()
def esc(A):
    A.app.config["WTF_CSRF_ENABLED"] = False
    r1 = make_user(A, "resp1", "SUPERVISOR", full_name="Ana Responsable")
    r2 = make_user(A, "resp2", "SUPERVISOR", full_name="Beto Responsable")
    jaula = make_location(A, "Jaula TNG")
    truck = make_location(A, "Camioneta 1", is_truck=True)
    it = make_item(A, code="CAB-500", name="Cable")
    A.upsert_stock(it.id, jaula.id, 20)
    A.db.session.commit()
    return {"jaula": jaula, "truck": truck, "item": it, "r1": r1, "r2": r2}


def _admin(A):
    c = A.app.test_client()
    login(c, "admin", "admin123")
    return c


def _resp(A, esc, *users):
    for u in users:
        A.db.session.add(A.LocationResponsible(location_id=esc["truck"].id, user_id=u.id))
    A.db.session.commit()


# ---------------------------------------------------------------- A: conteo -> scrap

def test_faltante_de_conteo_genera_scrap(A, esc):
    c = _admin(A)
    data = {
        "action": "apply",
        "location_id": str(esc["jaula"].id),
        "motivo": "conteo mensual",
        f"contado_{esc['item'].id}": "15",  # sistema 20 -> faltan 5
    }
    c.post("/conteo", data=data, follow_redirects=True)

    scraps = A.Scrap.query.all()
    assert len(scraps) == 1
    assert scraps[0].quantity == 5
    assert scraps[0].source == "CONTEO"
    assert "conteo mensual" in (scraps[0].reason or "")


def test_sobrante_de_conteo_no_genera_scrap(A, esc):
    c = _admin(A)
    c.post("/conteo", data={
        "action": "apply",
        "location_id": str(esc["jaula"].id),
        "motivo": "conteo",
        f"contado_{esc['item'].id}": "25",
    }, follow_redirects=True)
    assert A.Scrap.query.count() == 0


def test_pantalla_de_basura_lista_el_faltante(A, esc):
    c = _admin(A)
    c.post("/conteo", data={
        "action": "apply",
        "location_id": str(esc["jaula"].id),
        "motivo": "conteo",
        f"contado_{esc['item'].id}": "18",
    }, follow_redirects=True)

    html = c.get("/descartes").get_data(as_text=True)
    assert "CAB-500" in html
    assert "Faltante de conteo" in html


def test_basura_es_solo_lectura_y_pide_rol(A, esc):
    make_user(A, "tec", "TECNICO")
    c = A.app.test_client()
    login(c, "tec")
    assert c.get("/descartes").status_code in (302, 403)
    assert c.post("/descartes").status_code in (302, 403, 405)


# ---------------------------------------------------------------- E: conteo inválido

def test_cantidad_contada_invalida_corta_el_conteo(A, esc):
    c = _admin(A)
    otro = make_item(A, code="CAB-501", name="Otro cable")
    A.upsert_stock(otro.id, esc["jaula"].id, 4)
    A.db.session.commit()

    html = c.post("/conteo", data={
        "action": "apply",
        "location_id": str(esc["jaula"].id),
        "motivo": "conteo",
        f"contado_{esc['item'].id}": "abc",   # inválido
        f"contado_{otro.id}": "9",            # válido: no debe aplicarse
    }, follow_redirects=True).get_data(as_text=True)

    assert "no son un número entero" in html
    assert "CAB-500" in html
    # Nada se tocó: ni el ítem inválido ni el válido de la misma carga.
    assert A.Stock.query.filter_by(item_id=esc["item"].id, location_id=esc["jaula"].id).first().quantity == 20
    assert A.Stock.query.filter_by(item_id=otro.id, location_id=esc["jaula"].id).first().quantity == 4


def test_cantidad_contada_negativa_tambien_corta(A, esc):
    c = _admin(A)
    html = c.post("/conteo", data={
        "action": "preview",
        "location_id": str(esc["jaula"].id),
        "motivo": "conteo",
        f"contado_{esc['item'].id}": "-3",
    }, follow_redirects=True).get_data(as_text=True)
    assert "no son un número entero" in html


# ---------------------------------------------------------------- B: responsables

def test_un_solo_responsable_no_hace_falta_elegir(A, esc):
    _resp(A, esc, esc["r1"])
    c = _admin(A)
    c.post("/movements", data={
        "item_id": str(esc["item"].id), "qty": "2",
        "from_location_id": str(esc["jaula"].id),
        "to_location_id": str(esc["truck"].id),
        "generate_pending": "1",
    }, follow_redirects=True)
    pend = A.PendingDelivery.query.all()
    assert len(pend) == 2
    assert {p.responsible_to_id for p in pend} == {esc["r1"].id}


def test_con_dos_responsables_hay_que_elegir(A, esc):
    _resp(A, esc, esc["r1"], esc["r2"])
    c = _admin(A)
    html = c.post("/movements", data={
        "item_id": str(esc["item"].id), "qty": "1",
        "from_location_id": str(esc["jaula"].id),
        "to_location_id": str(esc["truck"].id),
        "generate_pending": "1",
    }, follow_redirects=True).get_data(as_text=True)

    assert "más de un responsable" in html
    assert A.PendingDelivery.query.count() == 0
    assert A.Movement.query.count() == 0  # todo o nada: tampoco se movió stock


def test_el_responsable_elegido_es_el_que_queda(A, esc):
    _resp(A, esc, esc["r1"], esc["r2"])
    c = _admin(A)
    c.post("/movements", data={
        "item_id": str(esc["item"].id), "qty": "1",
        "from_location_id": str(esc["jaula"].id),
        "to_location_id": str(esc["truck"].id),
        "generate_pending": "1",
        "pending_responsible_id": str(esc["r2"].id),
    }, follow_redirects=True)
    pend = A.PendingDelivery.query.one()
    assert pend.responsible_to_id == esc["r2"].id


def test_no_se_puede_elegir_un_responsable_ajeno(A, esc):
    _resp(A, esc, esc["r1"])
    ajeno = make_user(A, "ajeno", "SUPERVISOR")
    c = _admin(A)
    html = c.post("/movements", data={
        "item_id": str(esc["item"].id), "qty": "1",
        "from_location_id": str(esc["jaula"].id),
        "to_location_id": str(esc["truck"].id),
        "generate_pending": "1",
        "pending_responsible_id": str(ajeno.id),
    }, follow_redirects=True).get_data(as_text=True)
    assert "no es responsable" in html
    assert A.PendingDelivery.query.count() == 0


# ---------------------------------------------------------------- B: quién devuelve

def _pendiente(A, esc, qty=1):
    _resp(A, esc, esc["r1"], esc["r2"])
    c = _admin(A)
    c.post("/movements", data={
        "item_id": str(esc["item"].id), "qty": str(qty),
        "from_location_id": str(esc["jaula"].id),
        "to_location_id": str(esc["truck"].id),
        "generate_pending": "1",
        "pending_responsible_id": str(esc["r1"].id),
    }, follow_redirects=True)
    return c, A.PendingDelivery.query.first()


def test_devolucion_registra_quien_devolvio(A, esc):
    c, p = _pendiente(A, esc)
    c.post("/pending-deliveries", data={
        "pending_id": str(p.id),
        "return_action": "return",
        "returned_by_user_id": str(esc["r2"].id),
    }, follow_redirects=True)

    p = A.PendingDelivery.query.get(p.id)
    assert p.returned is True
    assert p.returned_by_user_id == esc["r2"].id
    assert p.returned_at is not None


def test_por_defecto_devuelve_el_responsable(A, esc):
    c, p = _pendiente(A, esc)
    c.post("/pending-deliveries", data={
        "pending_id": str(p.id),
        "return_action": "return",
    }, follow_redirects=True)
    assert A.PendingDelivery.query.get(p.id).returned_by_user_id == esc["r1"].id


def test_no_puede_devolver_alguien_sin_relacion(A, esc):
    ajeno = make_user(A, "ajeno2", "SUPERVISOR")
    c, p = _pendiente(A, esc)
    c.post("/pending-deliveries", data={
        "pending_id": str(p.id),
        "return_action": "return",
        "returned_by_user_id": str(ajeno.id),
    }, follow_redirects=True)
    assert A.PendingDelivery.query.get(p.id).returned is False


# ---------------------------------------------------------------- C y G: cantidades

def test_no_se_puede_pedir_devolver_mas_de_lo_entregado(A, esc):
    _resp(A, esc, esc["r1"])
    c = _admin(A)
    html = c.post("/movements", data={
        "item_id": str(esc["item"].id), "qty": "2",
        "from_location_id": str(esc["jaula"].id),
        "to_location_id": str(esc["truck"].id),
        "generate_pending": "1",
        "pending_return_qty": "3",
    }, follow_redirects=True).get_data(as_text=True)
    assert "no puede superar" in html
    assert A.PendingDelivery.query.count() == 0


def test_pendiente_viejo_sin_return_qty_devuelve_una_cantidad_coherente(A, esc):
    """Simula una fila anterior a la columna `return_qty` (quedó en NULL)."""
    _resp(A, esc, esc["r1"])
    c = _admin(A)
    c.post("/movements", data={
        "item_id": str(esc["item"].id), "qty": "3",
        "from_location_id": str(esc["jaula"].id),
        "to_location_id": str(esc["truck"].id),
        "generate_pending": "1",
    }, follow_redirects=True)

    # Se deja UN solo pendiente y sin return_qty, como las filas viejas.
    pend = A.PendingDelivery.query.all()
    for extra in pend[1:]:
        A.db.session.delete(extra)
    p = pend[0]
    p.return_qty = None
    A.db.session.commit()

    assert A.pending_return_units(p) == 3  # el pendiente representa el movimiento

    c.post("/pending-deliveries", data={
        "pending_id": str(p.id),
        "return_action": "return",
    }, follow_redirects=True)

    p = A.PendingDelivery.query.get(p.id)
    assert p.returned is True
    assert p.return_qty == 3  # queda completado, ya no es ambiguo
    # Volvió a la Jaula la cantidad correcta.
    assert A.Stock.query.filter_by(
        item_id=esc["item"].id, location_id=esc["jaula"].id
    ).first().quantity == 20


# ---------------------------------------------------------------- F: ajuste sin motivo

def test_ajuste_de_stock_no_pide_motivo(A, esc):
    c = _admin(A)
    c.post("/admin/adjust-stock", data={
        "item_id": str(esc["item"].id),
        "location_id": str(esc["jaula"].id),
        "action": "SUMAR",
        "qty": "5",
    }, follow_redirects=True)
    assert A.Stock.query.filter_by(
        item_id=esc["item"].id, location_id=esc["jaula"].id
    ).first().quantity == 25


def test_el_formulario_de_ajuste_ya_no_muestra_motivo(A, esc):
    c = _admin(A)
    html = c.get("/admin/adjust-stock").get_data(as_text=True)
    assert 'name="reason"' not in html
    assert "no pide motivo" in html
