"""Endurecimiento previo al lanzamiento a todos los técnicos.

Cinco cosas que en un sistema con dos usuarios no se notan y con quince sí:

1. `/movements` fallaba ABIERTO: un técnico sin ubicación asignada veía el
   historial completo de la empresa. Ese estado es el de todo usuario recién
   creado, o sea el de todos los técnicos el día del alta.
2. El alta de usuarios exigía 8 caracteres de contraseña y `/perfil` aceptaba 4.
   Como todos pasan por `/perfil` a cambiar su clave, el mínimo real era 4.
3. El límite de intentos de login iba por IP: varios técnicos desde la misma red
   se bloqueaban entre ellos sin haberse equivocado.
4. No había backup automático: la única copia era un botón que alguien tenía que
   acordarse de apretar.
5. El motivo de descarte al cerrar un pendiente caía a "Otro" en silencio.
"""
import pytest
from conftest import make_user, make_item, make_location, login, csrf_from


@pytest.fixture()
def esc(A):
    A.app.config["WTF_CSRF_ENABLED"] = False
    tec = make_user(A, "tec", "TECNICO")
    dep = make_location(A, "Deposito")
    truck = make_location(A, "Camioneta", is_truck=True)
    it = make_item(A, code="CAB-001", name="Cable")
    otro = make_item(A, code="CAB-777", name="Cable ajeno")
    A.upsert_stock(it.id, dep.id, 10)
    A.upsert_stock(otro.id, dep.id, 5)
    A.db.session.commit()
    return {"tec": tec, "dep": dep, "truck": truck, "item": it, "otro": otro}


def _admin(A):
    c = A.app.test_client()
    login(c, "admin", "admin123")
    return c


def _mov(A, item, desde, hacia, qty=1):
    y, seq, number = A.next_movement_number()
    m = A.Movement(item_id=item.id, qty=qty, from_location_id=desde.id,
                   to_location_id=hacia.id, user_id=1, observation="x",
                   year=y, seq=seq, number=number)
    A.db.session.add(m)
    A.db.session.commit()
    return m


# ==========================================================================
# A-01 · /movements no puede fallar abierto
# ==========================================================================

def test_tecnico_sin_ubicacion_no_ve_movimientos_ajenos(A, esc):
    """El caso del usuario recién creado: antes veía TODO el historial."""
    _mov(A, esc["item"], esc["dep"], esc["truck"])
    _mov(A, esc["otro"], esc["dep"], esc["truck"])
    # El técnico NO tiene LocationResponsible: no responde por ninguna ubicación.

    c = A.app.test_client()
    login(c, "tec")
    html = c.get("/movements").get_data(as_text=True)

    assert "CAB-001" not in html
    assert "CAB-777" not in html
    assert "ubicación asignada" in html   # se avisa, no parece un error


def test_tecnico_con_ubicacion_sigue_viendo_lo_suyo(A, esc):
    """Regresión: el acote de siempre no cambió."""
    A.db.session.add(A.LocationResponsible(location_id=esc["truck"].id,
                                           user_id=esc["tec"].id))
    A.db.session.commit()
    _mov(A, esc["item"], esc["dep"], esc["truck"])       # pasa por su camioneta
    _mov(A, esc["otro"], esc["dep"], esc["dep"])          # no lo toca

    c = A.app.test_client()
    login(c, "tec")
    html = c.get("/movements").get_data(as_text=True)

    assert "CAB-001" in html
    assert "CAB-777" not in html


def test_el_admin_sigue_viendo_todo(A, esc):
    _mov(A, esc["item"], esc["dep"], esc["truck"])
    _mov(A, esc["otro"], esc["dep"], esc["truck"])
    html = _admin(A).get("/movements").get_data(as_text=True)
    assert "CAB-001" in html and "CAB-777" in html


# ==========================================================================
# Contraseña: un solo mínimo en todo el sistema
# ==========================================================================

def test_perfil_rechaza_una_clave_mas_corta_que_el_minimo(A, esc):
    corta = "a" * (A.MIN_PASSWORD_LEN - 1)
    c = A.app.test_client()
    login(c, "tec")
    c.post("/perfil", data={"current_password": "pass1234", "new_password": corta,
                            "confirm_password": corta}, follow_redirects=True)

    A.db.session.expire_all()
    u = A.User.query.filter_by(username="tec").first()
    assert not u.check_password(corta)
    assert u.check_password("pass1234")     # sigue la anterior


def test_perfil_acepta_una_clave_del_largo_minimo(A, esc):
    ok = "a" * A.MIN_PASSWORD_LEN
    c = A.app.test_client()
    login(c, "tec")
    c.post("/perfil", data={"current_password": "pass1234", "new_password": ok,
                            "confirm_password": ok}, follow_redirects=True)

    A.db.session.expire_all()
    assert A.User.query.filter_by(username="tec").first().check_password(ok)


def test_el_minimo_de_perfil_y_el_del_alta_son_el_mismo(A):
    """Si vuelven a divergir, el mínimo real del sistema es el más bajo."""
    import inspect
    fuente = inspect.getsource(A.perfil)
    assert "MIN_PASSWORD_LEN" in fuente
    assert "< 4" not in fuente


# ==========================================================================
# Límite de login: por cuenta, no por red
# ==========================================================================

def test_dos_tecnicos_de_la_misma_red_no_comparten_el_limite(A):
    with A.app.test_request_context("/login", method="POST", data={"username": "tec1"}):
        k1 = A._login_rate_key()
    with A.app.test_request_context("/login", method="POST", data={"username": "tec2"}):
        k2 = A._login_rate_key()
    assert k1 != k2


def test_el_mismo_usuario_sigue_compartiendo_el_limite(A):
    """Fuerza bruta contra una cuenta: la clave tiene que ser la misma."""
    with A.app.test_request_context("/login", method="POST", data={"username": "tec"}):
        k1 = A._login_rate_key()
    with A.app.test_request_context("/login", method="POST", data={"username": "TEC"}):
        k2 = A._login_rate_key()   # no se esquiva cambiando mayúsculas
    assert k1 == k2


def test_el_login_sigue_funcionando(A, esc):
    c = A.app.test_client()
    assert login(c, "tec").status_code == 302


# ==========================================================================
# Backup diario automático
# ==========================================================================

def _reset_backup(A):
    A._backup_diario_hoy["fecha"] = None
    if A._BACKUP_MARK.exists():
        A._BACKUP_MARK.unlink()


def test_hace_un_backup_del_dia(A):
    _reset_backup(A)
    antes = len(list(A.BACKUP_DIR.glob("stocks_diario_*.db")))
    A._daily_backup_if_due()
    assert len(list(A.BACKUP_DIR.glob("stocks_diario_*.db"))) == antes + 1


def test_no_duplica_el_backup_del_mismo_dia(A):
    _reset_backup(A)
    A._daily_backup_if_due()
    n = len(list(A.BACKUP_DIR.glob("stocks_diario_*.db")))
    A._daily_backup_if_due()                       # misma request-day, en memoria
    A._backup_diario_hoy["fecha"] = None           # como si la app se reiniciara
    A._daily_backup_if_due()                       # ahora lo frena la marca en disco
    assert len(list(A.BACKUP_DIR.glob("stocks_diario_*.db"))) == n


def test_un_backup_fallado_no_rompe_la_request(A, esc, monkeypatch):
    _reset_backup(A)
    def explota(_label):
        raise IOError("disco lleno")
    monkeypatch.setattr(A, "_backup_db", explota)

    c = A.app.test_client()
    assert login(c, "tec").status_code == 302      # la app sigue respondiendo


def test_la_purga_solo_toca_los_backups_diarios(A):
    """Los manuales y los previos a una operación destructiva no se borran solos."""
    _reset_backup(A)
    A.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    # BACKUP_DIR es de toda la corrida: se parte de un estado conocido.
    for f in A.BACKUP_DIR.glob("stocks_diario_*.db"):
        f.unlink()
    manual = A.BACKUP_DIR / "stocks_manual_2020-01-01_000000.db"
    previo = A.BACKUP_DIR / "stocks_pre_clear-stock_2020-01-01_000000.db"
    manual.write_text("x")
    previo.write_text("x")
    for i in range(5):
        (A.BACKUP_DIR / f"stocks_diario_2020-01-0{i + 1}_000000.db").write_text("x")

    borrados = A._prune_daily_backups(2)

    assert borrados == 3
    assert len(list(A.BACKUP_DIR.glob("stocks_diario_2020-*.db"))) == 2
    assert manual.exists() and previo.exists()


def test_la_purga_conserva_los_mas_nuevos(A):
    _reset_backup(A)
    A.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    for f in A.BACKUP_DIR.glob("stocks_diario_*.db"):
        f.unlink()
    for d in ("2021-01-01", "2021-01-02", "2021-01-03"):
        (A.BACKUP_DIR / f"stocks_diario_{d}_000000.db").write_text("x")

    A._prune_daily_backups(1)
    quedan = [f.name for f in A.BACKUP_DIR.glob("stocks_diario_*.db")]
    assert quedan == ["stocks_diario_2021-01-03_000000.db"]


# ==========================================================================
# A-15 · motivo de descarte obligatorio al cerrar un pendiente
# ==========================================================================

def test_cerrar_un_pendiente_a_descartes_exige_motivo(A, esc):
    A.db.session.add(A.LocationResponsible(location_id=esc["truck"].id,
                                           user_id=esc["tec"].id))
    A.db.session.commit()
    c = _admin(A)
    c.post("/movements", data={
        "item_id": str(esc["item"].id), "qty": "1",
        "from_location_id": str(esc["dep"].id), "to_location_id": str(esc["truck"].id),
        "generate_pending": "1",
    })
    p = A.PendingDelivery.query.first()
    assert p is not None

    r = c.post("/pending-deliveries", data={
        "pending_id": str(p.id), "return_action": "scrap",
    }, follow_redirects=True)

    assert A.PendingDelivery.query.get(p.id).returned is False
    assert A.Scrap.query.count() == 0
    assert "motivo" in r.get_data(as_text=True).lower()


def test_con_motivo_el_descarte_se_registra(A, esc):
    A.db.session.add(A.LocationResponsible(location_id=esc["truck"].id,
                                           user_id=esc["tec"].id))
    A.db.session.commit()
    c = _admin(A)
    c.post("/movements", data={
        "item_id": str(esc["item"].id), "qty": "1",
        "from_location_id": str(esc["dep"].id), "to_location_id": str(esc["truck"].id),
        "generate_pending": "1",
    })
    p = A.PendingDelivery.query.first()

    c.post("/pending-deliveries", data={
        "pending_id": str(p.id), "return_action": "scrap", "scrap_reason": "Roto",
    })
    assert A.PendingDelivery.query.get(p.id).returned is True
    assert A.Scrap.query.filter_by(reason="Roto").count() == 1


# ==========================================================================
# A-06 · "En falla" se borraba en CADA arranque, con las FK apagadas
# ==========================================================================

def test_en_falla_con_historial_no_se_borra_al_arrancar(A, esc):
    """SQLite no valida FK si no se activa el PRAGMA: el DELETE siempre salía
    bien y dejaba movimientos apuntando a una ubicación inexistente."""
    falla = A.Location(name="En falla")
    A.db.session.add(falla)
    A.db.session.commit()
    _mov(A, esc["item"], esc["dep"], falla)

    A.seed_defaults()

    A.db.session.expire_all()
    assert A.Location.query.filter_by(name="En falla").first() is not None
    m = A.Movement.query.filter_by(to_location_id=falla.id).first()
    assert m is not None and m.to_location is not None   # no quedó huérfano


def test_en_falla_sin_referencias_si_se_borra(A, esc):
    """La limpieza original sigue funcionando cuando de verdad no la usa nadie."""
    A.db.session.add(A.Location(name="En falla"))
    A.db.session.commit()

    A.seed_defaults()

    A.db.session.expire_all()
    assert A.Location.query.filter_by(name="En falla").first() is None


def test_el_conteo_de_referencias_mira_todas_las_tablas(A, esc):
    """Si mañana una tabla nueva apunta a locations, tiene que contarla sola."""
    loc = A.Location(name="Ubicacion suelta")
    A.db.session.add(loc)
    A.db.session.commit()
    assert A._location_reference_count(loc.id) == 0

    A.upsert_stock(esc["item"].id, loc.id, 1)     # ahora la referencia Stock
    A.db.session.commit()
    assert A._location_reference_count(loc.id) >= 1


# ==========================================================================
# A-12 · ?limit=50000 saltaba el tope de paginación
# ==========================================================================

def test_el_limit_de_movimientos_topea_en_per_page_max(A):
    with A.app.test_request_context("/movements?limit=50000"):
        assert A._movements_filters_from_request()["limit"] == A.PER_PAGE_MAX


def test_un_limit_razonable_se_respeta(A):
    with A.app.test_request_context("/movements?limit=50"):
        assert A._movements_filters_from_request()["limit"] == 50


def test_sin_limit_queda_el_default_de_siempre(A):
    with A.app.test_request_context("/movements"):
        assert A._movements_filters_from_request()["limit"] == A.PER_PAGE_DEFAULT
