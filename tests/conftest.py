"""Configuración de pruebas Fase 0.

IMPORTANTE: se configura el entorno hacia una base/carpetas TEMPORALES ANTES
de importar app.py. Las pruebas NUNCA tocan data/stocks.db ni producción.
"""
import os
import sys
import tempfile

# --- Entorno de testing (se setea antes de importar app) ---
_TMP = tempfile.mkdtemp(prefix="stocks_tests_")
os.environ["STOCKS_DB_PATH"] = os.path.join(_TMP, "data", "stocks.db")
os.environ["STOCKS_BACKUP_DIR"] = os.path.join(_TMP, "backups")
os.environ["STOCKS_LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ["FLASK_SECRET_KEY"] = "testing-key-fixed"
os.environ["BOOTSTRAP_ADMIN_USERNAME"] = "admin"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "admin123"
os.environ["APP_DEBUG"] = "false"
os.environ["RATELIMIT_ENABLED"] = "false"  # el rate limiting no debe interferir en tests
os.environ.pop("ENABLE_RESET_DB", None)

# app.py está un nivel arriba de tests/
_APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

import re  # noqa: E402
import pytest  # noqa: E402
import app as appmod  # noqa: E402


@pytest.fixture()
def A():
    """Módulo app con base limpia por test (aislamiento total)."""
    with appmod.app.app_context():
        appmod.db.drop_all()
        appmod.db.create_all()
        appmod.seed_defaults()  # admin + ubicaciones Descartes/Utilizado/Proveedor
        yield appmod
        appmod.db.session.remove()


@pytest.fixture()
def client(A):
    # Por defecto CSRF desactivado para pruebas funcionales; test_csrf lo reactiva.
    A.app.config["WTF_CSRF_ENABLED"] = False
    return A.app.test_client()


# ------------------ helpers reutilizables ------------------

def make_user(A, username, role, password="pass1234", full_name=None):
    u = A.User(username=username, full_name=full_name or username.title(), role=role)
    u.set_password(password)
    A.db.session.add(u)
    A.db.session.commit()
    return u


def make_category(A, name="Cables", prefix="CAB"):
    c = A.Category.query.filter_by(name=name).first()
    if not c:
        c = A.Category(name=name, prefix=prefix)
        A.db.session.add(c)
        A.db.session.commit()
    return c


def make_item(A, code="CAB-001", name="Cable", trackable=False, stock_min=0,
              is_active=True, category=None):
    cat = category or make_category(A)
    it = A.Item(code=code, name=name, trackable=trackable, stock_min=stock_min,
                is_active=is_active, category_id=cat.id)
    A.db.session.add(it)
    A.db.session.commit()
    return it


def make_location(A, name, is_external=False, is_truck=False):
    loc = A.Location.query.filter_by(name=name).first()
    if not loc:
        loc = A.Location(name=name, is_external=is_external, is_truck=is_truck)
        A.db.session.add(loc)
        A.db.session.commit()
    return loc


def login(client, username, password="pass1234", A=None):
    """Login por HTTP. Obtiene el token si CSRF está activo."""
    html = client.get("/login").get_data(as_text=True)
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    data = {"username": username, "password": password}
    if m:
        data["csrf_token"] = m.group(1)
    return client.post("/login", data=data, follow_redirects=False)


def csrf_from(client, path):
    html = client.get(path).get_data(as_text=True)
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return m.group(1) if m else None
