"""Regresión del endurecimiento de seguridad.

Cubre las correcciones aplicadas tras la revisión externa. Ninguna de estas
pruebas hace explotación: solo verifica configuración y contratos.
"""
import io
import json
import re
from pathlib import Path

import pytest
from conftest import make_user, login, csrf_from

BASE_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture()
def admin(A):
    A.app.config["WTF_CSRF_ENABLED"] = False
    c = A.app.test_client()
    login(c, "admin", "admin123")
    return A, c


# ------------------------------------------------------------ cabeceras


@pytest.mark.parametrize("header", [
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "Permissions-Policy",
    "Cross-Origin-Opener-Policy",
])
def test_cabeceras_defensivas_presentes(admin, header):
    A, c = admin
    assert header in c.get("/stock").headers


def test_csp_va_en_report_only_por_defecto(admin):
    """CSP en modo enforce sin validar rompería los scripts inline existentes."""
    A, c = admin
    h = c.get("/stock").headers
    assert "Content-Security-Policy-Report-Only" in h
    assert "Content-Security-Policy" not in h


def test_csp_incluye_directivas_clave(admin):
    A, c = admin
    csp = c.get("/stock").headers["Content-Security-Policy-Report-Only"]
    for directiva in ("default-src", "frame-ancestors", "form-action",
                      "base-uri", "object-src 'none'"):
        assert directiva in csp


def test_no_se_publica_el_stack(admin):
    A, c = admin
    server = c.get("/stock").headers.get("Server", "")
    assert "waitress" not in server.lower()
    assert "werkzeug" not in server.lower()
    assert "python" not in server.lower()


def test_hsts_no_se_manda_sobre_http(admin):
    """HSTS sobre HTTP es un error caro: dejaría el dominio inaccesible."""
    A, c = admin
    assert "Strict-Transport-Security" not in c.get("/stock").headers


def test_cookie_de_sesion_es_httponly_y_lax(A):
    assert A.app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert A.app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


def test_secure_cookie_es_configurable_por_entorno(A):
    """No se fuerza a True en código: con HTTP puro dejaría a todos afuera."""
    assert "SESSION_COOKIE_SECURE" in A.app.config


# ------------------------------------------------------------ sesión / login


def test_login_rota_la_sesion(A):
    """La sesión anónima previa no debe sobrevivir al login (fijación de sesión)."""
    A.app.config["WTF_CSRF_ENABLED"] = False
    c = A.app.test_client()

    with c.session_transaction() as s:
        s["valor_plantado"] = "no-deberia-sobrevivir"

    login(c, "admin", "admin123")

    with c.session_transaction() as s:
        assert "valor_plantado" not in s
        assert "_user_id" in s


def test_login_sigue_funcionando_con_csrf_activo(A):
    A.app.config["WTF_CSRF_ENABLED"] = True
    c = A.app.test_client()
    assert login(c, "admin", "admin123").status_code == 302
    # Y el primer POST posterior tiene que validar CSRF sin 400.
    tok = csrf_from(c, "/perfil")
    r = c.post("/perfil", data={
        "current_password": "admin123", "new_password": "otraclave1",
        "confirm_password": "otraclave1", "csrf_token": tok,
    })
    assert r.status_code in (200, 302)


def test_mensaje_de_login_no_permite_enumerar_usuarios(A):
    A.app.config["WTF_CSRF_ENABLED"] = False
    c = A.app.test_client()
    r1 = c.post("/login", data={"username": "admin", "password": "mal"},
                follow_redirects=True).get_data(as_text=True)
    r2 = c.post("/login", data={"username": "no_existe", "password": "mal"},
                follow_redirects=True).get_data(as_text=True)
    assert "incorrectos" in r1 and "incorrectos" in r2


# ------------------------------------------------------------ subida de archivos


def test_import_rechaza_extension_no_permitida(admin):
    A, c = admin
    data = {"file": (io.BytesIO(b"cualquier cosa"), "malicioso.exe")}
    r = c.post("/import/items", data=data, content_type="multipart/form-data",
               follow_redirects=True)
    assert r.status_code == 200
    assert "Formato no permitido" in r.get_data(as_text=True)


def test_import_acepta_csv(admin):
    A, c = admin
    contenido = b"codigo;nombre;categoria\n"
    data = {"file": (io.BytesIO(contenido), "items.csv")}
    r = c.post("/import/items", data=data, content_type="multipart/form-data",
               follow_redirects=True)
    assert r.status_code == 200
    assert "Formato no permitido" not in r.get_data(as_text=True)


def test_import_ignora_ruta_en_el_nombre(admin):
    """Path traversal en el filename: solo debe mirarse el nombre base."""
    A, c = admin
    data = {"file": (io.BytesIO(b"codigo;nombre;categoria\n"),
                     "../../../../windows/system32/items.csv")}
    r = c.post("/import/items", data=data, content_type="multipart/form-data",
               follow_redirects=True)
    assert r.status_code == 200
    assert "Formato no permitido" not in r.get_data(as_text=True)


def test_hay_tope_de_tamano_de_request(A):
    assert A.app.config.get("MAX_CONTENT_LENGTH"), "sin tope, una subida grande satura el proceso"
    assert A.app.config["MAX_CONTENT_LENGTH"] <= 64 * 1024 * 1024


# ------------------------------------------------------------ dependencias


def test_requirements_estan_pinneadas():
    """Sin pin, un rebuild puede cambiar versiones sin que nadie toque código."""
    texto = (BASE_DIR / "requirements.txt").read_text(encoding="utf-8")
    sueltas = []
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        if "==" not in linea:
            sueltas.append(linea)
    assert not sueltas, f"dependencias sin versión fija: {sueltas}"


# Únicos orígenes externos aceptados en los templates. Google Fonts queda como
# excepción documentada: el CSS que devuelve varía por navegador, así que no
# admite SRI. Todo lo demás (JS/CSS de terceros) tiene que pasar por
# vendor_asset(), que fija versión y permite vendorizar.
ORIGENES_EXTERNOS_PERMITIDOS = (
    "https://fonts.googleapis.com",
    "https://fonts.gstatic.com",
)


def test_templates_no_hardcodean_cdn():
    """Los assets de terceros deben pasar por vendor_asset() (versión + SRI)."""
    ofensores = []
    for path in (BASE_DIR / "templates").glob("*.html"):
        for url in re.findall(r'(?:src|href)="(https?://[^"]+)"',
                              path.read_text(encoding="utf-8")):
            if not url.startswith(ORIGENES_EXTERNOS_PERMITIDOS):
                ofensores.append(f"{path.name}: {url}")
    assert not ofensores, f"URLs externas hardcodeadas: {ofensores}"


def test_csp_permite_las_fuentes_que_usan_los_templates():
    """Si la CSP no contempla Google Fonts, en modo enforce rompería el estilo."""
    import app as A
    for origen in ORIGENES_EXTERNOS_PERMITIDOS:
        assert origen in A.CSP_POLICY


def test_manifiesto_de_assets_es_valido():
    manifiesto = json.loads(
        (BASE_DIR / "static" / "vendor" / "assets.json").read_text(encoding="utf-8")
    )
    assert manifiesto["assets"], "el manifiesto no puede estar vacío"
    for nombre, meta in manifiesto["assets"].items():
        assert meta.get("version"), f"{nombre} sin versión fijada"
        assert meta.get("url", "").startswith("https://"), f"{nombre} sin URL https"


def test_docker_no_expone_el_puerto_a_internet():
    compose = (BASE_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    assert '"127.0.0.1:5000:5000"' in compose, (
        "waitress no debe ser alcanzable directamente desde afuera"
    )


# ------------------------------------------------------------ autorización


def test_endpoint_de_busqueda_respeta_login(A):
    c = A.app.test_client()
    assert c.get("/api/items/search").status_code in (302, 401)


def test_lector_no_puede_importar(A):
    A.app.config["WTF_CSRF_ENABLED"] = False
    make_user(A, "lec", "LECTOR")
    c = A.app.test_client()
    login(c, "lec")
    data = {"file": (io.BytesIO(b"codigo;nombre;categoria\n"), "items.csv")}
    r = c.post("/import/items", data=data, content_type="multipart/form-data")
    assert r.status_code in (302, 403)
