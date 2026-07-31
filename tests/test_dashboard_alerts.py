"""Fase 0.1.1: contador de alertas del dashboard (misma fuente que Alertas)."""
import re
import pytest
from conftest import make_item, make_location, make_user, login


def _alert_card_value(body):
    m = re.search(r'Ítems en alerta</span>\s*<span class="value">(\d+)</span>', body)
    return int(m.group(1)) if m else None


def _home(client):
    return client.get("/").get_data(as_text=True)


# ---- Caso 1: amarillo por encima del mínimo ----

def test_caso1_amarillo_cuenta_y_textos(A, client):
    it = make_item(A, code="CAB-001", name="Item", stock_min=10)
    loc = make_location(A, "Dep1")
    A.upsert_stock(it.id, loc.id, 11)
    A.db.session.commit()

    assert A.stock_level_class(it, 11) == "stock-yellow"
    login(client, "admin", "admin123")

    # aparece en /stock-alerts
    alerts_body = client.get("/stock-alerts").get_data(as_text=True)
    assert f"<td>{it.code}</td>" in alerts_body

    body = _home(client)
    assert _alert_card_value(body) == 1
    assert "Ítems en alerta" in body
    assert "nivel rojo o amarillo" in body


# ---- Caso 2: verde no cuenta ----

def test_caso2_verde_no_cuenta(A, client):
    it = make_item(A, code="CAB-002", name="Item", stock_min=10)
    loc = make_location(A, "Dep1")
    A.upsert_stock(it.id, loc.id, 14)
    A.db.session.commit()

    assert A.stock_level_class(it, 14) == "stock-green"
    login(client, "admin", "admin123")

    alerts_body = client.get("/stock-alerts").get_data(as_text=True)
    assert f"<td>{it.code}</td>" not in alerts_body

    assert _alert_card_value(_home(client)) == 0


# ---- Caso 3: mismo item amarillo en dos ubicaciones = 1 item distinto ----

def test_caso3_item_en_dos_ubicaciones_cuenta_uno(A, client):
    it = make_item(A, code="CAB-003", name="Item", stock_min=10)
    d1 = make_location(A, "Dep1")
    d2 = make_location(A, "Dep2")
    A.upsert_stock(it.id, d1.id, 11)
    A.upsert_stock(it.id, d2.id, 11)
    A.db.session.commit()

    login(client, "admin", "admin123")
    # la pantalla puede mostrar dos filas...
    alerts_body = client.get("/stock-alerts").get_data(as_text=True)
    assert alerts_body.count(f"<td>{it.code}</td>") == 2
    # ...pero el dashboard cuenta un solo item distinto
    assert _alert_card_value(_home(client)) == 1


# ---- Caso 4: usuario con ubicaciones responsables ----

def test_caso4_respeta_ubicaciones_responsables(A, client):
    sup = make_user(A, "sup", "SUPERVISOR")
    d1 = make_location(A, "Dep1")
    d2 = make_location(A, "Dep2")
    # sup es responsable SOLO de Dep1
    A.db.session.add(A.LocationResponsible(location_id=d1.id, user_id=sup.id))

    ia = make_item(A, code="CAA-001", name="A", stock_min=10)
    ib = make_item(A, code="CAB-001", name="B", stock_min=10)
    A.upsert_stock(ia.id, d1.id, 11)   # visible para sup (Dep1)
    A.upsert_stock(ib.id, d2.id, 11)   # NO visible para sup (Dep2)
    A.db.session.commit()

    # ADMIN (sin responsables) ve los 2
    login(client, "admin", "admin123")
    assert _alert_card_value(_home(client)) == 2

    # SUPERVISOR responsable de Dep1 ve 1, coincidiendo con alert_items_distinct
    login(client, "sup", "pass1234")
    assert _alert_card_value(_home(client)) == 1
