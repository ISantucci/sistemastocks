"""Carga de seriales en tanda (POST /items/<id>/units/bulk).

Cubre las reglas que hacen que la tanda sea segura:
  - todo o nada: si una linea falla, NO se guarda ninguna
  - unicidad por item, case-insensitive, en cualquier estado
  - cupo por ubicacion (no se etiqueta mas de lo que hay en stock)
  - filtro anti-codigo-equivocado (EAN / part number) y su escape manual
  - permisos: solo ADMIN/SUPERVISOR
"""
import pytest

from conftest import make_item, make_location, make_category, make_user, login


# ------------------------------------------------------------ fixtures


@pytest.fixture()
def setup(A, client):
    """Item serializado con 20 en stock en Jaula TNG, y un admin logueado.

    Depende de `client` a proposito: esa fixture es la que apaga el CSRF para
    las pruebas funcionales (test_bulk_exige_csrf lo vuelve a encender).
    """
    cat = make_category(A, "Equipos", "EQP")
    it = make_item(A, code="EQP-001", name="Camara PTZ", category=cat)
    it.serialized = True
    A.db.session.commit()

    jaula = make_location(A, A.LOCATION_JAULA_TNG)
    prov = make_location(A, A.LOCATION_PROVEEDOR, is_external=True)

    A.db.session.add(A.Stock(item_id=it.id, location_id=jaula.id, quantity=20))
    A.db.session.commit()

    c = A.app.test_client()
    login(c, "admin", "admin123")
    return A, c, it, jaula, prov


def post_bulk(c, item_id, **data):
    return c.post(f"/items/{item_id}/units/bulk", data=data, follow_redirects=True)


def serials(A, item_id):
    return sorted(u.serial for u in A.ItemUnit.query.filter_by(item_id=item_id).all())


# ------------------------------------------------------------ camino feliz


def test_carga_una_tanda_completa(setup):
    A, c, it, jaula, _ = setup
    lote = "\n".join(f"BE1052CPAJ064E{i}" for i in range(1, 6))
    post_bulk(c, it.id, location_id=jaula.id, serials=lote)

    assert len(serials(A, it.id)) == 5
    for u in A.ItemUnit.query.filter_by(item_id=it.id).all():
        assert u.status == A.UNIT_EN_STOCK
        assert u.location_id == jaula.id


def test_acepta_coma_y_punto_y_coma_como_separador(setup):
    A, c, it, jaula, _ = setup
    post_bulk(c, it.id, location_id=jaula.id,
              serials="BE1052CPAJ064E1, BE1052CPAJ064E2; BE1052CPAJ064E3")
    assert len(serials(A, it.id)) == 3


def test_la_nota_se_aplica_a_toda_la_tanda(setup):
    A, c, it, jaula, _ = setup
    post_bulk(c, it.id, location_id=jaula.id,
              serials="BE1052CPAJ064E1\nBE1052CPAJ064E2", notes="remito 0001-12345")
    assert all(u.notes == "remito 0001-12345"
               for u in A.ItemUnit.query.filter_by(item_id=it.id).all())


def test_no_toca_el_stock(setup):
    """Etiquetar es asociar un serial a stock que YA esta ahi, no moverlo."""
    A, c, it, jaula, _ = setup
    post_bulk(c, it.id, location_id=jaula.id, serials="BE1052CPAJ064E1\nBE1052CPAJ064E2")
    st = A.Stock.query.filter_by(item_id=it.id, location_id=jaula.id).first()
    assert st.quantity == 20
    assert A.Movement.query.count() == 0


# ------------------------------------------------------------ todo o nada


def test_un_serial_malo_no_guarda_ninguno(setup):
    A, c, it, jaula, _ = setup
    r = post_bulk(c, it.id, location_id=jaula.id,
                  serials="BE1052CPAJ064E1\n6923172586230\nBE1052CPAJ064E3")
    assert serials(A, it.id) == []
    assert "no parece un número de serie" in r.get_data(as_text=True)


def test_repetido_dentro_de_la_tanda_rechaza_todo(setup):
    A, c, it, jaula, _ = setup
    r = post_bulk(c, it.id, location_id=jaula.id,
                  serials="BE1052CPAJ064E1\nBE1052CPAJ064E2\nbe1052cpaj064e1")
    assert serials(A, it.id) == []
    assert "repetidos dentro de la tanda" in r.get_data(as_text=True)


# ------------------------------------------------------------ unicidad


def test_serial_ya_existente_rechaza_la_tanda(setup):
    A, c, it, jaula, _ = setup
    A.db.session.add(A.ItemUnit(item_id=it.id, serial="BE1052CPAJ064E1",
                                status=A.UNIT_EN_STOCK, location_id=jaula.id))
    A.db.session.commit()

    r = post_bulk(c, it.id, location_id=jaula.id,
                  serials="BE1052CPAJ064E1\nBE1052CPAJ064E9")
    assert len(serials(A, it.id)) == 1  # solo el preexistente
    assert "Ya están cargados" in r.get_data(as_text=True)


def test_la_unicidad_es_case_insensitive(setup):
    A, c, it, jaula, _ = setup
    A.db.session.add(A.ItemUnit(item_id=it.id, serial="BE1052CPAJ064E1",
                                status=A.UNIT_EN_STOCK, location_id=jaula.id))
    A.db.session.commit()

    post_bulk(c, it.id, location_id=jaula.id, serials="be1052cpaj064e1")
    assert len(serials(A, it.id)) == 1


def test_no_se_puede_reusar_un_serial_que_ya_salio(setup):
    """Una unidad ENTREGADA sigue ocupando su serial: es historico."""
    A, c, it, jaula, _ = setup
    A.db.session.add(A.ItemUnit(item_id=it.id, serial="BE1052CPAJ064E1",
                                status=A.UNIT_ENTREGADO, location_id=None))
    A.db.session.commit()

    post_bulk(c, it.id, location_id=jaula.id, serials="BE1052CPAJ064E1")
    assert len(serials(A, it.id)) == 1


def test_el_mismo_serial_en_otro_item_si_se_permite(setup):
    """La unicidad es POR ITEM, no global (regla vigente del modelo)."""
    A, c, it, jaula, _ = setup
    otro = make_item(A, code="EQP-002", name="Grabador")
    otro.serialized = True
    A.db.session.add(A.Stock(item_id=otro.id, location_id=jaula.id, quantity=5))
    A.db.session.commit()

    post_bulk(c, it.id, location_id=jaula.id, serials="BE1052CPAJ064E1")
    post_bulk(c, otro.id, location_id=jaula.id, serials="BE1052CPAJ064E1")
    assert len(serials(A, it.id)) == 1
    assert len(serials(A, otro.id)) == 1


# ------------------------------------------------------------ cupo


def test_no_se_puede_pasar_del_cupo(setup):
    A, c, it, jaula, _ = setup
    lote = "\n".join(f"BE1052CPAJ064E{i:03d}" for i in range(21))  # 21 > 20
    r = post_bulk(c, it.id, location_id=jaula.id, serials=lote)
    assert serials(A, it.id) == []
    assert "No hay cupo" in r.get_data(as_text=True)


def test_el_cupo_cuenta_los_ya_etiquetados(setup):
    A, c, it, jaula, _ = setup
    st = A.Stock.query.filter_by(item_id=it.id, location_id=jaula.id).first()
    st.quantity = 3
    A.db.session.add(A.ItemUnit(item_id=it.id, serial="BE1052CPAJ064E1",
                                status=A.UNIT_EN_STOCK, location_id=jaula.id))
    A.db.session.commit()

    r = post_bulk(c, it.id, location_id=jaula.id,
                  serials="BE1052CPAJ064E2\nBE1052CPAJ064E3\nBE1052CPAJ064E4")
    assert len(serials(A, it.id)) == 1
    assert "No hay cupo" in r.get_data(as_text=True)


def test_ubicacion_externa_rechazada(setup):
    A, c, it, jaula, prov = setup
    r = post_bulk(c, it.id, location_id=prov.id, serials="BE1052CPAJ064E1")
    assert serials(A, it.id) == []
    assert "Ubicación inválida" in r.get_data(as_text=True)


# ------------------------------------------------------------ filtro de formato


@pytest.mark.parametrize("codigo,pista", [
    ("6923172586230", "todo numeros"),      # EAN-13 de la caja
    ("1.0.01.07.14838", "part number"),     # P/N
    ("AB12", "corto"),                      # ruido de lectura
])
def test_filtro_descarta_codigos_que_no_son_serial(setup, codigo, pista):
    A, c, it, jaula, _ = setup
    post_bulk(c, it.id, location_id=jaula.id, serials=codigo)
    assert serials(A, it.id) == []


def test_el_filtro_se_puede_forzar(setup):
    """Para el dia que entre una marca con serial numerico."""
    A, c, it, jaula, _ = setup
    post_bulk(c, it.id, location_id=jaula.id, serials="6923172586230", force_format="on")
    assert serials(A, it.id) == ["6923172586230"]


# ------------------------------------------------------------ guardas varias


def test_item_no_serializado_rechazado(A, client):
    cat = make_category(A, "Cables", "CAB")
    it = make_item(A, code="CAB-001", name="Cable", category=cat)
    jaula = make_location(A, A.LOCATION_JAULA_TNG)
    A.db.session.add(A.Stock(item_id=it.id, location_id=jaula.id, quantity=10))
    A.db.session.commit()

    c = A.app.test_client()
    login(c, "admin", "admin123")
    r = post_bulk(c, it.id, location_id=jaula.id, serials="ABC123456")
    assert A.ItemUnit.query.count() == 0
    assert "no es serializado" in r.get_data(as_text=True)


def test_tanda_vacia_no_rompe(setup):
    A, c, it, jaula, _ = setup
    r = post_bulk(c, it.id, location_id=jaula.id, serials="   \n\n  ")
    assert serials(A, it.id) == []
    assert "No cargaste ningún serial" in r.get_data(as_text=True)


def test_tope_por_tanda(setup):
    A, c, it, jaula, _ = setup
    lote = "\n".join(f"SERIAL{i:05d}" for i in range(A.UNIT_BULK_MAX + 1))
    r = post_bulk(c, it.id, location_id=jaula.id, serials=lote)
    assert serials(A, it.id) == []
    assert "demasiados seriales" in r.get_data(as_text=True).lower()


# ------------------------------------------------------------ permisos


@pytest.mark.parametrize("rol", ["TECNICO", "LECTOR"])
def test_roles_sin_permiso_no_pueden_cargar(setup, rol):
    A, _, it, jaula, _ = setup
    make_user(A, "otro", rol)
    c2 = A.app.test_client()
    login(c2, "otro", "pass1234")

    c2.post(f"/items/{it.id}/units/bulk",
            data={"location_id": jaula.id, "serials": "BE1052CPAJ064E1"},
            follow_redirects=True)
    assert serials(A, it.id) == []


def test_supervisor_si_puede(setup):
    A, _, it, jaula, _ = setup
    make_user(A, "sup", "SUPERVISOR")
    c2 = A.app.test_client()
    login(c2, "sup", "pass1234")

    post_bulk(c2, it.id, location_id=jaula.id, serials="BE1052CPAJ064E1")
    assert serials(A, it.id) == ["BE1052CPAJ064E1"]


def test_bulk_exige_csrf(setup):
    A, _, it, jaula, _ = setup
    A.app.config["WTF_CSRF_ENABLED"] = True
    try:
        c2 = A.app.test_client()
        login(c2, "admin", "admin123")
        r = c2.post(f"/items/{it.id}/units/bulk",
                    data={"location_id": jaula.id, "serials": "BE1052CPAJ064E1"})
        assert r.status_code in (400, 403)
        assert serials(A, it.id) == []
    finally:
        A.app.config["WTF_CSRF_ENABLED"] = False


# ------------------------------------------------------------ contexto desde Stock


def test_abriendo_desde_stock_la_ubicacion_viene_elegida(setup):
    """/stock manda ?loc=<id>: el form no tiene que volver a preguntarla."""
    A, c, it, jaula, _ = setup
    h = c.get(f"/items/{it.id}/units?loc={jaula.id}").get_data(as_text=True)
    assert f'value="{jaula.id}" selected' in h
    assert '<option value="" selected disabled>' not in h


def test_sin_loc_no_preselecciona_nada(setup):
    A, c, it, jaula, _ = setup
    h = c.get(f"/items/{it.id}/units").get_data(as_text=True)
    assert '<option value="" selected disabled>' in h


def test_ubicacion_sin_cupo_no_se_preselecciona(setup):
    """Si no hay lugar, queda el placeholder: el navegador no debe elegir otra."""
    A, c, it, jaula, _ = setup
    st = A.Stock.query.filter_by(item_id=it.id, location_id=jaula.id).first()
    st.quantity = 0
    A.db.session.commit()

    h = c.get(f"/items/{it.id}/units?loc={jaula.id}").get_data(as_text=True)
    assert f'value="{jaula.id}" selected' not in h
    assert '<option value="" selected disabled>' in h


def test_al_guardar_no_se_pierde_el_contexto(setup):
    """Tras cargar una tanda desde Stock, la vuelta conserva ?loc= (y ?embed=)."""
    A, c, it, jaula, _ = setup
    r = c.post(f"/items/{it.id}/units/bulk",
               data={"location_id": jaula.id, "serials": "BE1052CPAJ064E1",
                     "loc": jaula.id, "embed": "1"},
               follow_redirects=False)
    assert r.status_code == 302
    assert f"loc={jaula.id}" in r.headers["Location"]
    assert "embed=1" in r.headers["Location"]


def test_la_vuelta_sigue_igual_sin_contexto(setup):
    """Sin loc ni embed, el redirect es el de siempre (no cambia nada)."""
    A, c, it, jaula, _ = setup
    r = c.post(f"/items/{it.id}/units/bulk",
               data={"location_id": jaula.id, "serials": "BE1052CPAJ064E1"},
               follow_redirects=False)
    assert r.headers["Location"].endswith(f"/items/{it.id}/units")
