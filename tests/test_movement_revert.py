"""Reversión de movimientos dentro de la ventana corta.

Lo que se protege acá:
  - que revertir NO borre historial (contra-movimiento, no DELETE);
  - que el stock quede exactamente como estaba antes;
  - y sobre todo que NO se pueda revertir cuando hay consecuencias colgando.

El último punto es la mitad del valor de estas pruebas: cada guarda tiene su
test, y todas se validan por POST directo, no por lo que muestre el HTML.
"""
from datetime import timedelta

from conftest import make_user, make_item, make_location, login


def _setup(A, role="ADMIN"):
    """Usuario logueado + ítem + dos ubicaciones internas con stock inicial."""
    make_user(A, "op", role)
    it = make_item(A, code="CAB-100", name="Cable UTP")
    jaula = make_location(A, "Jaula TNG")
    camioneta = make_location(A, "Camioneta 1", is_truck=True)
    A.upsert_stock(it.id, jaula.id, 10)
    A.db.session.commit()
    return it, jaula, camioneta


def _mover(client, it, desde, hacia, qty=3, **extra):
    data = {
        "item_id": str(it.id), "qty": str(qty),
        "from_location_id": str(desde.id), "to_location_id": str(hacia.id),
    }
    data.update(extra)
    return client.post("/movements", data=data, follow_redirects=True)


def _qty(A, item_id, loc_id):
    row = A.Stock.query.filter_by(item_id=item_id, location_id=loc_id).first()
    return (row.quantity or 0) if row else 0


# ------------------ camino feliz ------------------

def test_revertir_devuelve_el_stock_al_origen(A, client):
    it, jaula, camioneta = _setup(A)
    login(client, "op")
    _mover(client, it, jaula, camioneta, qty=3)
    assert _qty(A, it.id, jaula.id) == 7
    assert _qty(A, it.id, camioneta.id) == 3

    m = A.Movement.query.first()
    client.post(f"/movements/{m.id}/revertir", follow_redirects=True)

    assert _qty(A, it.id, jaula.id) == 10
    assert _qty(A, it.id, camioneta.id) == 0


def test_revertir_no_borra_el_movimiento_original(A, client):
    it, jaula, camioneta = _setup(A)
    login(client, "op")
    _mover(client, it, jaula, camioneta)
    m = A.Movement.query.first()
    mid, numero = m.id, m.number

    client.post(f"/movements/{mid}/revertir", follow_redirects=True)

    original = A.db.session.get(A.Movement, mid)
    assert original is not None, "el movimiento original no se puede borrar"
    assert original.number == numero
    assert original.reverted_at is not None
    assert original.reverted_by_user_id is not None


def test_revertir_genera_un_contra_movimiento_con_su_propio_numero(A, client):
    it, jaula, camioneta = _setup(A)
    login(client, "op")
    _mover(client, it, jaula, camioneta, qty=3)
    m = A.Movement.query.first()

    client.post(f"/movements/{m.id}/revertir", follow_redirects=True)

    contra = A.Movement.query.filter_by(reverses_movement_id=m.id).first()
    assert contra is not None
    assert contra.number and contra.number != m.number
    assert contra.from_location_id == m.to_location_id
    assert contra.to_location_id == m.from_location_id
    assert contra.qty == m.qty


def test_ingreso_revertido_sale_del_stock_y_no_genera_descarte(A, client):
    """Un ingreso mal cargado se anula con un contra-movimiento hacia Proveedor.

    NO se descarta: un descarte ensuciaría las métricas con algo que en
    realidad nunca entró.
    """
    make_user(A, "op", "ADMIN")
    it = make_item(A, code="CAB-200", name="Cable coaxil")
    jaula = make_location(A, "Jaula TNG")
    prov = make_location(A, "Proveedor", is_external=True)
    login(client, "op")

    _mover(client, it, prov, jaula, qty=5)
    assert _qty(A, it.id, jaula.id) == 5

    m = A.Movement.query.first()
    client.post(f"/movements/{m.id}/revertir", follow_redirects=True)

    assert _qty(A, it.id, jaula.id) == 0
    assert A.Scrap.query.count() == 0, "revertir un ingreso no debe generar un descarte"


def test_revertir_un_descarte_devuelve_el_stock_y_anula_el_scrap(A, client):
    it, jaula, _ = _setup(A)
    descartes = make_location(A, "Descartes")
    login(client, "op")

    _mover(client, it, jaula, descartes, qty=2, scrap_reason="Roto")
    assert A.Scrap.query.count() == 1
    assert _qty(A, it.id, jaula.id) == 8

    m = A.Movement.query.first()
    client.post(f"/movements/{m.id}/revertir", follow_redirects=True)

    assert _qty(A, it.id, jaula.id) == 10
    assert A.Scrap.query.count() == 0, "el descarte anulado no debe seguir contando en métricas"


def test_revertir_anula_el_pendiente_abierto(A, client):
    it, jaula, camioneta = _setup(A)
    tec = make_user(A, "tec", "TECNICO")
    A.db.session.add(A.LocationResponsible(location_id=camioneta.id, user_id=tec.id))
    A.db.session.commit()
    login(client, "op")

    _mover(client, it, jaula, camioneta, qty=2, generate_pending="1")
    assert A.PendingDelivery.query.count() == 2

    m = A.Movement.query.first()
    client.post(f"/movements/{m.id}/revertir", follow_redirects=True)

    assert A.PendingDelivery.query.count() == 0
    assert _qty(A, it.id, jaula.id) == 10


# ------------------ guardas: cuándo NO se puede ------------------

def test_no_se_revierte_dos_veces(A, client):
    it, jaula, camioneta = _setup(A)
    login(client, "op")
    _mover(client, it, jaula, camioneta, qty=3)
    m = A.Movement.query.first()

    client.post(f"/movements/{m.id}/revertir", follow_redirects=True)
    client.post(f"/movements/{m.id}/revertir", follow_redirects=True)

    assert _qty(A, it.id, jaula.id) == 10, "la segunda reversión no debe sumar stock de la nada"
    assert A.Movement.query.filter_by(reverses_movement_id=m.id).count() == 1


def test_no_se_revierte_una_reversion(A, client):
    it, jaula, camioneta = _setup(A)
    login(client, "op")
    _mover(client, it, jaula, camioneta, qty=3)
    m = A.Movement.query.first()
    client.post(f"/movements/{m.id}/revertir", follow_redirects=True)

    contra = A.Movement.query.filter_by(reverses_movement_id=m.id).first()
    assert "Es la reversión de otro movimiento." in A.movement_revert_blockers(contra)


def test_fuera_de_la_ventana_no_se_puede_revertir(A, client):
    it, jaula, camioneta = _setup(A)
    login(client, "op")
    _mover(client, it, jaula, camioneta, qty=3)

    m = A.Movement.query.first()
    m.created_at = A.now_ar() - timedelta(minutes=A.MOVEMENT_REVERT_MINUTES + 1)
    A.db.session.commit()

    client.post(f"/movements/{m.id}/revertir", follow_redirects=True)

    assert _qty(A, it.id, jaula.id) == 7, "un movimiento viejo no se revierte"
    assert A.db.session.get(A.Movement, m.id).reverted_at is None


def test_no_se_revierte_si_el_stock_ya_se_movio_del_destino(A, client):
    it, jaula, camioneta = _setup(A)
    otra = make_location(A, "Camioneta 2", is_truck=True)
    login(client, "op")

    _mover(client, it, jaula, camioneta, qty=3)
    m = A.Movement.query.first()
    _mover(client, it, camioneta, otra, qty=3)   # ya salió del destino

    client.post(f"/movements/{m.id}/revertir", follow_redirects=True)

    assert A.db.session.get(A.Movement, m.id).reverted_at is None
    assert _qty(A, it.id, otra.id) == 3, "no se le puede robar el stock a otra ubicación"


def test_no_se_revierte_si_el_pendiente_ya_fue_devuelto(A, client):
    it, jaula, camioneta = _setup(A)
    tec = make_user(A, "tec", "TECNICO")
    A.db.session.add(A.LocationResponsible(location_id=camioneta.id, user_id=tec.id))
    A.db.session.commit()
    login(client, "op")

    _mover(client, it, jaula, camioneta, qty=1, generate_pending="1")
    m = A.Movement.query.first()
    p = A.PendingDelivery.query.first()
    p.returned = True
    A.db.session.commit()

    assert "El pendiente generado ya fue devuelto." in A.movement_revert_blockers(m)


def test_no_se_revierte_si_ya_esta_en_un_remito(A, client):
    it, jaula, camioneta = _setup(A)
    login(client, "op")
    _mover(client, it, jaula, camioneta, qty=3)
    m = A.Movement.query.first()

    r = A.Remito(year=2026, seq=1, number="R-2026-0001",
                 from_location_id=jaula.id, to_location_id=camioneta.id,
                 created_by_user_id=A.User.query.filter_by(username="op").first().id)
    A.db.session.add(r)
    A.db.session.flush()
    A.db.session.add(A.RemitoLine(remito_id=r.id, movement_id=m.id))
    A.db.session.commit()

    assert "El movimiento ya está incluido en un remito." in A.movement_revert_blockers(m)

    client.post(f"/movements/{m.id}/revertir", follow_redirects=True)
    assert _qty(A, it.id, jaula.id) == 7


def test_serializado_no_se_revierte_automaticamente(A, client):
    """No hay vínculo ItemUnit -> Movement, así que adivinar sería inventar."""
    it, jaula, camioneta = _setup(A)
    login(client, "op")
    _mover(client, it, jaula, camioneta, qty=3)
    m = A.Movement.query.first()

    it.serialized = True
    A.db.session.commit()

    assert "El ítem es serializado: la reversión automática no está soportada." \
        in A.movement_revert_blockers(m)


# ------------------ permisos (backend, no UI) ------------------

def test_tecnico_no_puede_revertir_ni_por_post_directo(A, client):
    it, jaula, camioneta = _setup(A)
    make_user(A, "tec", "TECNICO")
    login(client, "op")
    _mover(client, it, jaula, camioneta, qty=3)
    m = A.Movement.query.first()

    client.get("/logout")
    login(client, "tec")
    r = client.post(f"/movements/{m.id}/revertir")

    assert r.status_code in (302, 403)
    assert A.db.session.get(A.Movement, m.id).reverted_at is None
    assert _qty(A, it.id, jaula.id) == 7


def test_lector_no_puede_revertir_ni_por_post_directo(A, client):
    it, jaula, camioneta = _setup(A)
    make_user(A, "lec", "LECTOR")
    login(client, "op")
    _mover(client, it, jaula, camioneta, qty=3)
    m = A.Movement.query.first()

    client.get("/logout")
    login(client, "lec")
    r = client.post(f"/movements/{m.id}/revertir")

    assert r.status_code in (302, 403)
    assert A.db.session.get(A.Movement, m.id).reverted_at is None


def test_supervisor_si_puede_revertir(A, client):
    it, jaula, camioneta = _setup(A, role="SUPERVISOR")
    login(client, "op")
    _mover(client, it, jaula, camioneta, qty=3)
    m = A.Movement.query.first()

    client.post(f"/movements/{m.id}/revertir", follow_redirects=True)
    assert _qty(A, it.id, jaula.id) == 10


def test_el_boton_no_aparece_para_el_tecnico(A, client):
    it, jaula, camioneta = _setup(A)
    tec = make_user(A, "tec", "TECNICO")
    A.db.session.add(A.LocationResponsible(location_id=camioneta.id, user_id=tec.id))
    A.db.session.commit()
    login(client, "op")
    _mover(client, it, jaula, camioneta, qty=3)

    client.get("/logout")
    login(client, "tec")
    html = client.get("/movements").get_data(as_text=True)
    assert "/revertir" not in html


def test_movimiento_inexistente_da_404(A, client):
    _setup(A)
    login(client, "op")
    assert client.post("/movements/99999/revertir").status_code == 404


def test_los_filtros_de_la_url_no_rompen_la_pantalla(A, client):
    """Un parámetro raro en la URL no debe tirar un 500 ni filtrar nada."""
    it, jaula, camioneta = _setup(A)
    login(client, "op")
    _mover(client, it, jaula, camioneta, qty=3)

    for qs in ["?movement_id=9", "?limit=abc", "?page=-1", "?sort_by=' or 1=1--", "?foo=bar"]:
        assert client.get("/movements" + qs).status_code == 200, qs


def test_revertir_conserva_los_filtros_del_listado(A, client):
    it, jaula, camioneta = _setup(A)
    login(client, "op")
    _mover(client, it, jaula, camioneta, qty=3)
    m = A.Movement.query.first()

    r = client.post(f"/movements/{m.id}/revertir?limit=50&sort_by=qty")
    assert r.status_code == 302
    assert "limit=50" in r.headers["Location"]
    assert "sort_by=qty" in r.headers["Location"]
