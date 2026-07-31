"""Regresión de stock: upsert_stock (no se modifica su semántica)."""
import pytest
from conftest import make_item, make_location


def _qty(A, item, loc):
    row = A.Stock.query.filter_by(item_id=item.id, location_id=loc.id).first()
    return row.quantity if row else None


def test_alta_de_stock(A):
    it = make_item(A)
    loc = make_location(A, "Dep1")
    A.upsert_stock(it.id, loc.id, 5)
    A.db.session.commit()
    assert _qty(A, it, loc) == 5


def test_resta(A):
    it = make_item(A)
    loc = make_location(A, "Dep1")
    A.upsert_stock(it.id, loc.id, 5)
    A.upsert_stock(it.id, loc.id, -3)
    A.db.session.commit()
    assert _qty(A, it, loc) == 2


def test_bloqueo_stock_negativo(A):
    it = make_item(A)
    loc = make_location(A, "Dep1")
    A.upsert_stock(it.id, loc.id, 2)
    with pytest.raises(ValueError):
        A.upsert_stock(it.id, loc.id, -5)
    A.db.session.rollback()


def test_rastreable_maximo_uno_global(A):
    it = make_item(A, code="EQP-001", trackable=True)
    a = make_location(A, "LocA")
    b = make_location(A, "LocB")
    A.upsert_stock(it.id, a.id, 1)
    A.db.session.commit()
    with pytest.raises(ValueError):
        A.upsert_stock(it.id, b.id, 1)
    A.db.session.rollback()


def test_rastreable_eliminado_en_cero(A):
    it = make_item(A, code="EQP-002", trackable=True)
    a = make_location(A, "LocA")
    A.upsert_stock(it.id, a.id, 1)
    A.db.session.commit()
    A.upsert_stock(it.id, a.id, -1)
    A.db.session.commit()
    # rastreable en cero => fila eliminada
    assert A.Stock.query.filter_by(item_id=it.id, location_id=a.id).first() is None


def test_no_rastreable_conserva_fila_en_cero(A):
    it = make_item(A, code="CAB-010", trackable=False)
    a = make_location(A, "LocA")
    A.upsert_stock(it.id, a.id, 3)
    A.upsert_stock(it.id, a.id, -3)
    A.db.session.commit()
    row = A.Stock.query.filter_by(item_id=it.id, location_id=a.id).first()
    assert row is not None and row.quantity == 0


def test_rollback_ante_error(A):
    it = make_item(A)
    loc = make_location(A, "Dep1")
    A.upsert_stock(it.id, loc.id, 2)
    A.db.session.commit()
    # Intento inválido dentro de una "transacción": restar de más
    try:
        A.upsert_stock(it.id, loc.id, -10)
        A.db.session.commit()
    except ValueError:
        A.db.session.rollback()
    # El stock previo se conserva intacto
    assert _qty(A, it, loc) == 2
