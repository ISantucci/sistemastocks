"""Remitos: relación Desde/Hacia, movimientos duplicados y permisos por rol.

Remitos NO crea movimientos ni toca stock: agrupa movimientos que YA existen y
les da un número imprimible. Por eso el equivalente del bug de "cargar el mismo
ítem dos veces" acá es "meter el mismo movimiento dos veces en el remito", y el
equivalente de "mover a la misma ubicación" es "armar un remito de una relación
consigo misma".

Este archivo fija ese comportamiento, que hoy ya es correcto, para que no se
pierda: el backend descarta movimientos repetidos, los de otra relación y los
que ya están remitados.
"""
import pytest
from conftest import make_user, make_item, make_location, login

ROLES = {"ADMIN": ("admin", "admin123"), "SUPERVISOR": ("u_sup", "pass1234"),
         "TECNICO": ("u_tec", "pass1234"), "LECTOR": ("u_lec", "pass1234")}


@pytest.fixture()
def esc(A):
    A.app.config["WTF_CSRF_ENABLED"] = False
    jaula = make_location(A, A.LOCATION_JAULA_TNG)
    truck = make_location(A, "Berlingo YA", is_truck=True)
    otro = make_location(A, "Kangoo", is_truck=True)
    for rol, (u, _) in ROLES.items():
        if rol == "ADMIN":
            continue
        make_user(A, u, rol)
    resp = A.User.query.filter_by(username="u_sup").first()
    for loc in (jaula, truck, otro):
        A.db.session.add(A.LocationResponsible(location_id=loc.id, user_id=resp.id))
    cable = make_item(A, code="CAB-001", name="Rollo cable RG58U")
    A.upsert_stock(cable.id, jaula.id, 50)
    A.db.session.commit()
    return {"jaula": jaula, "truck": truck, "otro": otro, "cable": cable, "resp": resp}


def cli(A, rol):
    u, pw = ROLES[rol]
    c = A.app.test_client()
    login(c, u, pw)
    return c


def mover(A, esc, c, desde, hacia, qty=1):
    c.post("/movements", data={
        "item_id": str(esc["cable"].id), "qty": str(qty),
        "from_location_id": str(desde.id), "to_location_id": str(hacia.id),
    }, follow_redirects=True)
    return A.Movement.query.order_by(A.Movement.id.desc()).first()


def test_remito_new_misma_ubicacion(A, esc):
    c = cli(A, "ADMIN")
    m = mover(A, esc, c, esc["jaula"], esc["truck"])
    antes = A.Remito.query.count()
    c.post("/remitos/new", data={
        "from_location_id": str(esc["truck"].id),
        "to_location_id": str(esc["truck"].id),
        "movement_id": [str(m.id)],
        "responsible_from_id": str(esc["resp"].id),
        "responsible_to_id": str(esc["resp"].id),
    }, follow_redirects=True)
    assert A.Remito.query.count() == antes


def test_fragmento_ajax_avisa_misma_ubicacion(A, esc):
    c = cli(A, "ADMIN")
    html = c.get("/remitos/movimientos", query_string={
        "from_location_id": str(esc["truck"].id),
        "to_location_id": str(esc["truck"].id),
    }).get_data(as_text=True)
    assert "no pueden ser la misma ubicación" in html


def test_no_se_puede_colar_movimiento_de_otra_relacion(A, esc):
    """Un movimiento Jaula->Berlingo no puede entrar a un remito Jaula->Kangoo."""
    c = cli(A, "ADMIN")
    m = mover(A, esc, c, esc["jaula"], esc["truck"])
    antes = A.Remito.query.count()
    c.post("/remitos/new", data={
        "from_location_id": str(esc["jaula"].id),
        "to_location_id": str(esc["otro"].id),
        "movement_id": [str(m.id)],
        "responsible_from_id": str(esc["resp"].id),
        "responsible_to_id": str(esc["resp"].id),
    }, follow_redirects=True)
    assert A.Remito.query.count() == antes


def test_mismo_movimiento_repetido_en_el_post(A, esc):
    """El equivalente del bug de duplicados, pero acá el 'ítem' es un movimiento."""
    c = cli(A, "ADMIN")
    m = mover(A, esc, c, esc["jaula"], esc["truck"])
    c.post("/remitos/new", data={
        "from_location_id": str(esc["jaula"].id),
        "to_location_id": str(esc["truck"].id),
        "movement_id": [str(m.id), str(m.id), str(m.id)],
        "responsible_from_id": str(esc["resp"].id),
        "responsible_to_id": str(esc["resp"].id),
    }, follow_redirects=True)
    lineas = A.RemitoLine.query.count()
    assert lineas == 1


def test_movimiento_ya_remitado_no_se_repite(A, esc):
    c = cli(A, "ADMIN")
    m = mover(A, esc, c, esc["jaula"], esc["truck"])
    data = {
        "from_location_id": str(esc["jaula"].id),
        "to_location_id": str(esc["truck"].id),
        "movement_id": [str(m.id)],
        "responsible_from_id": str(esc["resp"].id),
        "responsible_to_id": str(esc["resp"].id),
    }
    c.post("/remitos/new", data=data, follow_redirects=True)
    n1 = A.Remito.query.count()
    c.post("/remitos/new", data=data, follow_redirects=True)
    assert A.Remito.query.count() == n1
    assert A.RemitoLine.query.count() == 1


@pytest.mark.parametrize("rol", ["ADMIN", "SUPERVISOR", "TECNICO", "LECTOR"])
def test_permisos_crear_remito(A, esc, rol):
    admin = cli(A, "ADMIN")
    m = mover(A, esc, admin, esc["jaula"], esc["truck"])
    c = cli(A, rol)
    antes = A.Remito.query.count()
    r = c.post("/remitos/new", data={
        "from_location_id": str(esc["jaula"].id),
        "to_location_id": str(esc["truck"].id),
        "movement_id": [str(m.id)],
        "responsible_from_id": str(esc["resp"].id),
        "responsible_to_id": str(esc["resp"].id),
    }, follow_redirects=True)
    creados = A.Remito.query.count() - antes
    ve = c.get("/remitos").status_code
    if rol in ("ADMIN", "SUPERVISOR"):
        assert creados == 1
    else:
        assert creados == 0


@pytest.mark.parametrize("rol", ["TECNICO", "LECTOR"])
def test_fragmentos_ajax_solo_para_quien_edita(A, esc, rol):
    c = cli(A, rol)
    for url in ("/remitos/movimientos", "/remitos/responsables"):
        r = c.get(url, query_string={"from_location_id": "1", "to_location_id": "2", "location_id": "1"})
        assert r.status_code in (302, 403), f"{rol} accede a {url}"


def test_ui_avisa_al_instante_si_la_relacion_es_la_misma(A, esc):
    """A diferencia de Movimientos, acá el usuario NO se entera recién al enviar.

    El modal pide los movimientos por AJAX apenas elegís las ubicaciones, y el
    fragmento devuelve el aviso, así que la lista nunca trae nada tildable.
    """
    c = cli(A, "ADMIN")
    html = c.get("/remitos/movimientos", query_string={
        "from_location_id": str(esc["truck"].id),
        "to_location_id": str(esc["truck"].id),
    }).get_data(as_text=True)
    assert "no pueden ser la misma ubicación" in html
    assert 'name="movement_id"' not in html   # nada para seleccionar


@pytest.mark.parametrize("rol,espera", [("ADMIN", True), ("SUPERVISOR", True),
                                        ("TECNICO", False), ("LECTOR", False)])
def test_exclusion_desde_hacia_llega_a_quien_edita(A, esc, rol, espera):
    """El modal de nuevo remito solo se le sirve a ADMIN/SUPERVISOR."""
    html = cli(A, rol).get("/remitos").get_data(as_text=True)
    assert ("initFromToExclusion" in html) is espera
    assert ('id="rm-to"' in html) is espera
