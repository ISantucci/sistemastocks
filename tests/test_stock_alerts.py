"""Fase 0.1 · Tarea 4/5: criterio y columnas de Alertas de Stock.

Semáforo con stock_min=10: rojo qty<=6, verde qty>=14, amarillo 7..13.
"""
import pytest
from conftest import make_item, make_location, login


def _setup(A, code="CAB-001", stock_min=10, qty=11, trackable=False,
           is_active=True, loc_name="Dep1"):
    it = make_item(A, code=code, name="Item " + code, trackable=trackable,
                   stock_min=stock_min, is_active=is_active)
    loc = make_location(A, loc_name)
    if qty is not None:
        A.upsert_stock(it.id, loc.id, qty)
        A.db.session.commit()
    return it, loc


def _alerts_body(client):
    return client.get("/stock-alerts").get_data(as_text=True)


def _in_alert_table(body, code):
    # La fila usa <td>CODE</td>; el dropdown usa "CODE - Nombre", así que
    # <td>CODE</td> identifica exclusivamente la fila de la tabla de alertas.
    return f"<td>{code}</td>" in body


# ---- criterio: rojo / amarillo aparecen, verde no ----

@pytest.mark.parametrize("qty,color,aparece", [
    (6, "stock-red", True),
    (11, "stock-yellow", True),
    (13, "stock-yellow", True),
    (14, None, False),
])
def test_criterio_semaforo(A, client, qty, color, aparece):
    it, loc = _setup(A, qty=qty)
    login(client, "admin", "admin123")
    body = _alerts_body(client)
    assert _in_alert_table(body, it.code) is aparece
    if aparece:
        assert f'{color}">{qty}</td>' in body


def test_rastreable_no_aparece(A, client):
    it, loc = _setup(A, code="EQP-001", trackable=True, stock_min=0, qty=1)
    login(client, "admin", "admin123")
    assert not _in_alert_table(_alerts_body(client), it.code)


def test_inactivo_no_aparece(A, client):
    it, loc = _setup(A, code="CAB-009", is_active=False, qty=1)
    login(client, "admin", "admin123")
    assert not _in_alert_table(_alerts_body(client), it.code)


def test_stock_min_cero_no_aparece(A, client):
    it, loc = _setup(A, code="CAB-000", stock_min=0, qty=0)
    login(client, "admin", "admin123")
    assert not _in_alert_table(_alerts_body(client), it.code)


# ---- paridad Stock vs Alertas (misma clasificación) ----

def test_paridad_stock_alertas(A, client):
    it, loc = _setup(A, qty=11)  # amarillo
    assert A.stock_level_class(it, 11) == "stock-yellow"
    assert A.is_alert_stock(it, 11) is True
    login(client, "admin", "admin123")
    assert 'stock-yellow">11</td>' in _alerts_body(client)


# ---- filtros ----

def test_filtro_por_categoria(A, client):
    cat_a = A.Category(name="CatA", prefix="CAA"); A.db.session.add(cat_a)
    cat_b = A.Category(name="CatB", prefix="CAB"); A.db.session.add(cat_b)
    A.db.session.commit()
    ia = make_item(A, code="CAA-001", name="A", stock_min=10, category=cat_a)
    ib = make_item(A, code="CAB-001", name="B", stock_min=10, category=cat_b)
    loc = make_location(A, "Dep1")
    A.upsert_stock(ia.id, loc.id, 6); A.upsert_stock(ib.id, loc.id, 6)
    A.db.session.commit()
    login(client, "admin", "admin123")
    body = client.get(f"/stock-alerts?category_id={cat_a.id}").get_data(as_text=True)
    assert _in_alert_table(body, "CAA-001") and not _in_alert_table(body, "CAB-001")


def test_filtro_por_ubicacion(A, client):
    it = make_item(A, code="CAB-001", name="Item", stock_min=10)
    d1 = make_location(A, "Dep1"); d2 = make_location(A, "Dep2")
    A.upsert_stock(it.id, d1.id, 6); A.upsert_stock(it.id, d2.id, 6)
    A.db.session.commit()
    login(client, "admin", "admin123")
    body = client.get(f"/stock-alerts?location_id={d1.id}").get_data(as_text=True)
    # Aparece la fila de Dep1 y no la de Dep2
    assert "Dep1" in body and "Dep2</td>" not in body


# ---- alert_items_distinct ----

def test_distinct_incluye_amarillo_por_encima_de_min(A, client):
    it, loc = _setup(A, qty=11)
    with A.app.app_context():
        dist = {e["item"].id: e for e in A.alert_items_distinct()}
    assert it.id in dist and dist[it.id]["worst"] == "stock-yellow"


def test_distinct_total_qty_suma_todas_las_ubicaciones(A, client):
    it = make_item(A, code="CAB-001", name="Item", stock_min=10)
    d1 = make_location(A, "Dep1"); d2 = make_location(A, "Dep2")
    A.upsert_stock(it.id, d1.id, 11)   # amarillo
    A.upsert_stock(it.id, d2.id, 20)   # verde
    A.db.session.commit()
    with A.app.app_context():
        e = {x["item"].id: x for x in A.alert_items_distinct()}[it.id]
    # total_qty suma TODAS las ubicaciones visibles (11 + 20), no solo las en alerta
    assert e["total_qty"] == 31
    assert e["worst"] == "stock-yellow"


def test_distinct_worst_rojo_si_alguna_roja(A, client):
    it = make_item(A, code="CAB-001", name="Item", stock_min=10)
    d1 = make_location(A, "Dep1"); d2 = make_location(A, "Dep2")
    A.upsert_stock(it.id, d1.id, 6)    # rojo
    A.upsert_stock(it.id, d2.id, 11)   # amarillo
    A.db.session.commit()
    with A.app.app_context():
        e = {x["item"].id: x for x in A.alert_items_distinct()}[it.id]
    assert e["worst"] == "stock-red"


def test_columnas_finales_presentes(A, client):
    _setup(A, qty=11)
    login(client, "admin", "admin123")
    body = _alerts_body(client)
    for col in ("Ubicación", "Código", "Nombre", "Categoría", "Cantidad", "Link de referencia"):
        assert col in body
    # columnas eliminadas
    assert "Diferencia" not in body and "Stock mínimo" not in body
