from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
import os
import secrets
import sqlite3
import math

# Hora local Argentina (UTC-3, sin horario de verano). Offset fijo para no
# depender de la zona horaria del sistema/contenedor ni de librerias externas.
AR_TZ = timezone(timedelta(hours=-3))


def now_ar():
    """Fecha/hora actual en hora argentina, naive (para guardar y mostrar)."""
    return datetime.now(AR_TZ).replace(tzinfo=None)

from flask import Flask, render_template, request, redirect, url_for, flash, Response, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from flask import session
from flask import Response
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import csv
import io
import re
import unicodedata
from io import TextIOWrapper
from sqlalchemy.exc import IntegrityError
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError


# ------------------ APP / DB ------------------



app = Flask(__name__)

_env_secret = os.environ.get("FLASK_SECRET_KEY", "").strip()
if _env_secret:
    app.secret_key = _env_secret
else:
    app.secret_key = secrets.token_hex(32)
    print(
        "[WARN] FLASK_SECRET_KEY no definida. "
        "Usando key efimera (las sesiones se invalidan en cada reinicio)."
    )

BASE_DIR = Path(__file__).resolve().parent

DB_PATH = Path(os.environ.get("STOCKS_DB_PATH", str(BASE_DIR / "data/stocks.db"))).resolve()
BACKUP_DIR = Path(os.environ.get("STOCKS_BACKUP_DIR", str(BASE_DIR / "backups"))).resolve()
LOG_DIR = Path(os.environ.get("STOCKS_LOG_DIR", str(BASE_DIR / "logs"))).resolve()

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH.as_posix()}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# ------------------ CSRF (Flask-WTF) ------------------
# Proteccion CSRF global para todos los POST. Los GET/HEAD/OPTIONS quedan
# exentos automaticamente. Cada formulario POST debe incluir el token via
# {{ csrf_token() }}. No se desactiva CSRF para "resolver" errores.
csrf = CSRFProtect(app)


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    # Mensaje comprensible, sin traceback ni exponer el token.
    flash(
        "La sesion del formulario vencio o el envio no es valido. "
        "Volve a cargar la pagina e intenta nuevamente.",
        "error",
    )
    return (
        render_template("csrf_error.html"),
        400,
    )


# ------------------ SEGURIDAD: cookies de sesion + headers ------------------
# Endurecimiento de la cookie de sesion. SESSION_COOKIE_SECURE es opt-in por
# entorno para NO romper el acceso por HTTP en la EC2 si todavia no hay TLS
# delante (default false = comportamiento actual). Poner en "true" cuando la
# app quede detras de HTTPS.
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=(
        os.environ.get("SESSION_COOKIE_SECURE", "false").strip().lower() == "true"
    ),
)


@app.after_request
def _security_headers(resp):
    # Cabeceras de seguridad basicas. No se agrega CSP estricta a proposito:
    # los templates usan estilos/scripts inline y una CSP estricta los romperia.
    # X-Frame-Options=SAMEORIGIN (no DENY) para no romper las vistas embebidas
    # del mismo dominio (parametro ?embed=1).
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return resp


# ------------------ RATE LIMITING (flask-limiter) ------------------
# Limite por usuario autenticado (o por IP si es anonimo). Storage en memoria:
# alcanza porque produccion corre 1 solo proceso (waitress). Se puede desactivar
# por entorno con RATELIMIT_ENABLED=false (los tests lo desactivan).
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address


def _rate_key():
    try:
        if current_user.is_authenticated:
            return f"user:{current_user.id}"
    except Exception:
        pass
    return get_remote_address()


limiter = Limiter(
    key_func=_rate_key,
    default_limits=["120 per minute"],
    storage_uri="memory://",
    enabled=(os.environ.get("RATELIMIT_ENABLED", "true").strip().lower() == "true"),
)
limiter.init_app(app)


@limiter.request_filter
def _rate_exempt_static():
    # No limitar los assets estaticos (una sola pagina pide varios archivos).
    return request.endpoint == "static"


# Concurrencia SQLite: WAL permite lecturas concurrentes con una escritura,
# y busy_timeout hace que una escritura espere (en ms) en vez de fallar con
# "database is locked" cuando hay varios usuarios a la vez.
# El listener se aplica a cada nueva conexion del engine.
from sqlalchemy import event as _sa_event
from sqlalchemy.engine import Engine as _SAEngine


@_sa_event.listens_for(_SAEngine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record):
    try:
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()
    except Exception as exc:  # best-effort: nunca debe impedir conectar
        print(f"[WARN] No se pudieron aplicar PRAGMAs SQLite: {exc}")


# ------------------ AUTH ------------------

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


# ------------------ CONSTANTS ------------------

# Roles canonicos del sistema. Si agregas uno aca, revisa tambien role_required
# en los handlers y la UI de alta/edicion de usuarios.
ROLE_CHOICES = ["ADMIN", "SUPERVISOR", "TECNICO", "LECTOR"]

# Nombres de ubicaciones especiales. Deben existir en la tabla locations con
# estos nombres EXACTOS y con is_external=True. Cambiar estas constantes
# sin renombrar en la BD rompe admin_adjust_stock silenciosamente.
LOCATION_PROVEEDOR = "Proveedor"
LOCATION_DESCARTES = "Descartes"
# Jaula central: no es "camioneta" (is_truck=False) pero debe poder usarse como
# ORIGEN en Descartes y Utilizados. Se incluye explicitamente en esos selectores.
LOCATION_JAULA_TNG = "Jaula TNG"
# Mesa fisica de reparacion. Ubicacion INTERNA (is_external=False): el stock
# que esta "en reparacion" se contabiliza aca hasta que se repara (vuelve a
# Jaula TNG) o se descarta (va a Descartes + Scrap).
LOCATION_EN_REPARACION = "En reparación"
# Origen externo para el INGRESO de un item recuperado del campo cuando en un
# pendiente vuelve OTRO item distinto al entregado (is_external=True: no descuenta).
LOCATION_RECUPERADO = "Recuperado"

# Textos de confirmacion para operaciones destructivas admin.
# El usuario debe tipear el texto exacto en la UI para que el backend
# acepte la operacion. No es seguridad criptografica, pero cierra el hueco
# de POST directo (curl) y reduce drasticamente el riesgo de click accidental.
CONFIRM_CLEAR_STOCK = "BORRAR-STOCK"
CONFIRM_CLEAR_ITEMS = "BORRAR-ITEMS"
CONFIRM_RESET_DB = "RESET-DB"


def role_required(*allowed_roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if current_user.role not in allowed_roles:
                flash("No tenés permisos para acceder a esa sección.", "error")
                return redirect(url_for("home"))
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# Roles que un SUPERVISOR NO puede gestionar ni asignar (solo ADMIN).
PRIVILEGED_ROLES = ("ADMIN", "SUPERVISOR")


def can_manage_target(target_user) -> bool:
    """¿El usuario actual puede editar/cambiar clave del usuario target?

    ADMIN: cualquiera. SUPERVISOR: solo usuarios no privilegiados
    (ni ADMIN ni otros SUPERVISOR). Cualquier otro rol: no.
    """
    if not current_user.is_authenticated:
        return False
    if current_user.role == "ADMIN":
        return True
    if current_user.role == "SUPERVISOR":
        return target_user.role not in PRIVILEGED_ROLES
    return False


def current_user_responsible_location_ids():
    """IDs de ubicaciones de las que el usuario actual es responsable.

    Conjunto VACIO = el usuario no tiene ubicaciones asignadas, por lo tanto NO
    se aplica ninguna restriccion (ve todo, comportamiento previo). Si tiene una
    o mas, las alertas/solicitudes se limitan a esas ubicaciones.
    """
    try:
        if not current_user.is_authenticated:
            return set()
        return {
            lr.location_id
            for lr in LocationResponsible.query.filter_by(user_id=current_user.id).all()
        }
    except Exception:
        return set()


def assignable_roles_for_current():
    """Roles que el usuario actual puede asignar al crear/editar usuarios."""
    if current_user.is_authenticated and current_user.role == "ADMIN":
        return list(ROLE_CHOICES)
    if current_user.is_authenticated and current_user.role == "SUPERVISOR":
        return ["TECNICO", "LECTOR"]
    return []


# ------------------ CÓDIGOS DE ITEM / VALIDACIONES ------------------

# Prefijo de código por categoría. Clave = nombre de categoría normalizado
# (minúsculas, sin acentos). Son las 12 categorías actuales del catálogo.
# Si se crea una categoría nueva que NO esté acá, el alta de items de esa
# categoría queda bloqueada hasta que se le asigne prefijo (ver next_item_code).
CATEGORY_PREFIXES = {
    "cables": "CAB",
    "conectores": "CON",
    "electronica": "ELE",
    "equipos": "EQP",
    "herramientas": "HER",
    "impresion": "IMP",
    "insumos": "INS",
    "material electrico": "MAT",
    "redes": "RED",
    "seguridad": "SEG",
    "senalizacion": "SIG",
    "soportes y estructuras": "EST",
}

# Longitud mínima de contraseña (antes era 4).
MIN_PASSWORD_LEN = 8


def normalize_text(s: str) -> str:
    """Minúsculas, sin acentos y espacios colapsados. Para comparar nombres."""
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.split())


def prefix_for_category(category):
    """Prefijo de código de una categoría, o None si no tiene asignado.
    Fuente de verdad: la columna category.prefix. Como respaldo (por si la
    migración aún no corrió) usa el mapa fijo de las 12 categorías conocidas."""
    if not category:
        return None
    p = (getattr(category, "prefix", None) or "").strip().upper()
    if p:
        return p
    return CATEGORY_PREFIXES.get(normalize_text(category.name))


def normalize_prefix(raw: str) -> str:
    """Prefijo en mayúsculas, sin espacios. Para validar/guardar."""
    return (raw or "").strip().upper()


def prefix_taken(prefix: str, exclude_id=None) -> bool:
    """¿Otro categoría ya usa ese prefijo? (case-insensitive)."""
    target = normalize_prefix(prefix)
    q = Category.query
    if exclude_id is not None:
        q = q.filter(Category.id != exclude_id)
    return any(normalize_prefix(cat.prefix or "") == target for cat in q.all())


def next_item_code(prefix: str) -> str:
    """Siguiente código correlativo para un prefijo: PREFIJO-NNN (mín. 3 dígitos).
    Usa el máximo número existente (activos e inactivos) + 1, para no reutilizar
    códigos dados de baja."""
    like = f"{prefix}-%"
    rx = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    mx = 0
    for (code,) in db.session.query(Item.code).filter(Item.code.like(like)).all():
        m = rx.match(code or "")
        if m:
            mx = max(mx, int(m.group(1)))
    n = mx + 1
    width = max(3, len(str(n)))
    return f"{prefix}-{n:0{width}d}"


def item_name_exists(name: str, exclude_id=None) -> bool:
    """¿Existe ya un item ACTIVO con ese nombre? Comparación global, sin importar
    mayúsculas ni acentos. Los items dados de baja (is_active=False) no cuentan,
    así no bloquean el alta/edición ni impiden reutilizar el nombre."""
    target = normalize_text(name)
    q = Item.query.filter(Item.is_active == True)
    if exclude_id is not None:
        q = q.filter(Item.id != exclude_id)
    return any(normalize_text(it.name) == target for it in q.all())


def _name_taken(model, name, exclude_id=None) -> bool:
    """Uniqueness genérica case-insensitive/sin acentos para name de un modelo."""
    target = normalize_text(name)
    q = model.query
    if exclude_id is not None:
        q = q.filter(model.id != exclude_id)
    return any(normalize_text(getattr(o, "name", "")) == target for o in q.all())


def username_taken(username, exclude_id=None) -> bool:
    """Username duplicado sin importar mayúsculas."""
    target = (username or "").strip().lower()
    q = User.query
    if exclude_id is not None:
        q = q.filter(User.id != exclude_id)
    return any((u.username or "").strip().lower() == target for u in q.all())


# ------------------ MODELOS ------------------

class Location(db.Model):
    __tablename__ = "locations"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(255), nullable=True)

    # Si querés, podés marcar externas (Proveedor / Baja)
    is_external = db.Column(db.Boolean, default=False, nullable=False)
    is_truck = db.Column(db.Boolean, default=False, nullable=False)


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)

    username = db.Column(db.String(80), unique=True, nullable=False)
    full_name = db.Column(db.String(120), nullable=False, default="")
    password_hash = db.Column(db.String(255), nullable=False)

    role = db.Column(db.String(20), nullable=False)  # ADMIN/SUPERVISOR/TECNICO/LECTOR

    # Email de contacto (aditivo, opcional). Se usa como destinatario de las
    # solicitudes de compra (ver PurchaseRequestRecipient).
    email = db.Column(db.String(255), nullable=True)

    # Flask-Login interface
    @property
    def is_authenticated(self):  # Flask-Login lo maneja, pero OK
        return True

    @property
    def is_active(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def get_id(self):
        return str(self.id)

    def set_password(self, raw_password: str):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)


class Supplier(db.Model):
    """Carta de proveedor. Entidad propia (separada de usuarios) para no ensuciar
    la tabla de usuarios. Se usa en la seccion Ingresos/Egresos.
    """
    __tablename__ = "suppliers"
    id = db.Column(db.Integer, primary_key=True)

    contact_name = db.Column(db.String(120), nullable=False)          # Nombre (contacto)
    business_name = db.Column(db.String(160), nullable=True)          # Comercio / nombre de fantasia
    cuit = db.Column(db.String(20), nullable=True)                    # CUIT
    legal_name = db.Column(db.String(160), nullable=True)            # Razon social
    email = db.Column(db.String(255), nullable=True)
    phone = db.Column(db.String(40), nullable=True)

    is_active = db.Column(db.Boolean, default=True, nullable=False)   # baja logica
    created_at = db.Column(db.DateTime, default=now_ar, nullable=False)


class LocationResponsible(db.Model):
    __tablename__ = "location_responsibles"
    id = db.Column(db.Integer, primary_key=True)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    location = db.relationship("Location", backref="responsibles")
    user = db.relationship("User")

    __table_args__ = (db.UniqueConstraint("location_id", "user_id", name="uq_loc_user"),)


class Category(db.Model):
    __tablename__ = "categories"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    description = db.Column(db.String(255), nullable=True)
    # Prefijo de código de items de esta categoría (3 letras, ej. "CAB").
    prefix = db.Column(db.String(8), nullable=True)


class Item(db.Model):
    __tablename__ = "items"
    id = db.Column(db.Integer, primary_key=True)

    code = db.Column(db.String(50), unique=True, nullable=False)  # ELT-012
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.String(255), nullable=True)

    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False)
    category = db.relationship("Category", backref="items")

    trackable = db.Column(db.Boolean, default=False, nullable=False)

    # Stock de seguridad (mínimo). Aplica solo a NO rastreables.
    # Para rastreables no tiene sentido (máx 1 por ubicación), así que se ignora.
    stock_min = db.Column(db.Integer, default=0, nullable=False)

    # Baja lógica (no borramos historial / stock). Si is_active=False no aparece
    # en formularios, pero se mantiene en listados e históricos.
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    # Link de referencia de compra (opcional). Ver ensure_sqlite_schema para la
    # migración idempotente sobre bases existentes.
    reference_link = db.Column(db.String(500), nullable=True)

    # Forma de contabilización de la cantidad: 'unidad' (default, comportamiento
    # histórico) o 'metros'. Solo afecta cómo se muestra/rotula la cantidad; no
    # cambia la lógica de stock (sigue siendo un entero).
    unit = db.Column(db.String(16), default="unidad", nullable=False)

    # Ítem serializado: además del stock por cantidad (comportamiento histórico),
    # se llevan unidades individuales con número de serie en la tabla item_units.
    # Es OPT-IN e independiente de 'trackable'. Un ítem NO serializado se comporta
    # exactamente igual que antes (esta columna queda en 0/False).
    serialized = db.Column(db.Boolean, default=False, nullable=False)


# Estados de una unidad serializada.
UNIT_EN_STOCK = "EN_STOCK"      # presente en una ubicación interna
UNIT_ENTREGADO = "ENTREGADO"    # salió del sistema (entregado / externo)
UNIT_DESCARTADO = "DESCARTADO"  # fue a Descartes


class ItemUnit(db.Model):
    """Unidad física individual de un ítem serializado (un serial = una fila).

    Convive con Stock (cantidad agregada): la disponibilidad se sigue leyendo del
    stock por cantidad. Estas filas agregan trazabilidad por serial, sin cambiar
    la lógica de stock existente para ítems no serializados.
    """
    __tablename__ = "item_units"
    id = db.Column(db.Integer, primary_key=True)

    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False, index=True)
    serial = db.Column(db.String(120), nullable=False)

    status = db.Column(db.String(16), nullable=False, default=UNIT_EN_STOCK)
    # Ubicación actual (solo si status = EN_STOCK). NULL cuando salió del sistema.
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), nullable=True)

    notes = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=now_ar, nullable=False)

    item = db.relationship("Item", backref="units")
    location = db.relationship("Location")

    # El serial es único dentro de un mismo ítem (no global: dos modelos distintos
    # podrían, en teoría, compartir formato de serie).
    __table_args__ = (db.UniqueConstraint("item_id", "serial", name="uq_unit_item_serial"),)


class Stock(db.Model):
    __tablename__ = "stock"
    id = db.Column(db.Integer, primary_key=True)

    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), nullable=False)
    quantity = db.Column(db.Integer, default=0, nullable=False)

    item = db.relationship("Item")
    location = db.relationship("Location")

    __table_args__ = (db.UniqueConstraint("item_id", "location_id", name="uq_stock_item_location"),)


class Movement(db.Model):
    __tablename__ = "movements"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=now_ar, nullable=False)

    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False)
    qty = db.Column(db.Integer, nullable=False)

    # AHORA: OBLIGATORIOS, como pediste
    from_location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), nullable=False)
    to_location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), nullable=False)

    # Responsable obligatorio: usuario logueado
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    observation = db.Column(db.String(255), nullable=True)

    year = db.Column(db.Integer, nullable=True)
    seq = db.Column(db.Integer, nullable=True)
    number = db.Column(db.String(32), unique=True, nullable=True)

    # Proveedor asociado (solo en movimientos generados por Ingresos/Egresos).
    # NULL en los movimientos internos normales (jaula/camionetas). Sirve para
    # separarlos del listado de Movimientos sin tocar el historico.
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=True)

    item = db.relationship("Item")
    supplier = db.relationship("Supplier")
    from_location = db.relationship("Location", foreign_keys=[from_location_id])
    to_location = db.relationship("Location", foreign_keys=[to_location_id])
    user = db.relationship("User")

class PendingDelivery(db.Model):
    __tablename__ = "pending_deliveries"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=now_ar)

    movement_id = db.Column(db.Integer, db.ForeignKey("movements.id"), nullable=False)

    responsible_from_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    responsible_to_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False)

    # Devolucion esperada (aditivo). Si son NULL se comportan como antes:
    #   return_item_id NULL -> vuelve el mismo item entregado (item_id)
    #   return_qty     NULL -> vuelve la misma cantidad del movimiento
    # Permite: (a) que vuelva OTRO item (ej. entregas domo, deben traer Onvif)
    # y (b) devolucion parcial (entregas 2, deben traer 1).
    return_item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=True)
    return_qty = db.Column(db.Integer, nullable=True)

    comment = db.Column(db.Text)
    returned = db.Column(db.Boolean, default=False, nullable=False)

    movement = db.relationship("Movement")
    responsible_from = db.relationship("User", foreign_keys=[responsible_from_id])
    responsible_to = db.relationship("User", foreign_keys=[responsible_to_id])
    item = db.relationship("Item", foreign_keys=[item_id])
    return_item = db.relationship("Item", foreign_keys=[return_item_id])

# ------------------ REMITOS ------------------

class Remito(db.Model):
    __tablename__ = "remitos"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=now_ar, nullable=False)

    # Numeración simple por año (R-YYYY-0001)
    year = db.Column(db.Integer, nullable=False)
    seq = db.Column(db.Integer, nullable=False)

    number = db.Column(db.String(32), unique=True, nullable=False)

    status = db.Column(db.String(16), nullable=False, default="BORRADOR")  # BORRADOR / CONFIRMADO

    # Pendiente de impresion. Solo lo activan los remitos auto-generados por
    # Ingresos/Egresos, para el badge de "no te olvides de imprimir". Los remitos
    # normales quedan en False (default), asi no disparan alerta.
    print_pending = db.Column(db.Boolean, default=False, nullable=False)

    from_location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), nullable=False)
    to_location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), nullable=False)

    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    observation = db.Column(db.String(255), nullable=True)

    # Responsables elegidos al crear el remito (a cargo de cada ubicación).
    # Nullable: si el origen/destino es externo (proveedor) queda vacío para
    # completar a mano después de imprimir.
    responsible_from_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    responsible_to_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    from_location = db.relationship("Location", foreign_keys=[from_location_id])
    to_location = db.relationship("Location", foreign_keys=[to_location_id])
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])
    responsible_from = db.relationship("User", foreign_keys=[responsible_from_id])
    responsible_to = db.relationship("User", foreign_keys=[responsible_to_id])

    __table_args__ = (
        db.UniqueConstraint("year", "seq", name="uq_remito_year_seq"),
    )


class RemitoLine(db.Model):
    __tablename__ = "remito_lines"
    id = db.Column(db.Integer, primary_key=True)
    remito_id = db.Column(db.Integer, db.ForeignKey("remitos.id"), nullable=False)
    movement_id = db.Column(db.Integer, db.ForeignKey("movements.id"), nullable=False, unique=True)

    remito = db.relationship("Remito", backref=db.backref("lines", cascade="all, delete-orphan"))
    movement = db.relationship("Movement")

class Scrap(db.Model):
    __tablename__ = "scrap"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=now_ar, nullable=False)

    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(255), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    item = db.relationship("Item")
    location = db.relationship("Location")
    user = db.relationship("User")


class Repair(db.Model):
    """Registro de reparaciones. Aditivo: no altera tablas existentes.

    Ciclo: un item vuelve de un pendiente y en vez de ir directo a Descartes
    se manda a reparacion (EN_REPARACION). Desde /reparaciones se resuelve:
      - REPARADO  -> vuelve a stock (Jaula TNG)
      - DESCARTADO -> va a Descartes y se genera un Scrap (con motivo)
    Permite reportar cuantas se reparan/descartan por mes.
    """
    __tablename__ = "repairs"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=now_ar, nullable=False)

    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)

    # EN_REPARACION -> REPARADO / DESCARTADO
    status = db.Column(db.String(16), nullable=False, default="EN_REPARACION")

    resolved_at = db.Column(db.DateTime, nullable=True)
    result_reason = db.Column(db.String(255), nullable=True)  # motivo si se descarta

    # Trazabilidad de origen (opcional, no rompe si es NULL)
    pending_id = db.Column(db.Integer, db.ForeignKey("pending_deliveries.id"), nullable=True)
    source_location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), nullable=True)

    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    resolved_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    item = db.relationship("Item")
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])
    resolved_by = db.relationship("User", foreign_keys=[resolved_by_user_id])
    source_location = db.relationship("Location", foreign_keys=[source_location_id])


class PurchaseRequest(db.Model):
    """Cabecera de una solicitud de compra (estilo remito).

    Aditivo: no toca stock ni movimientos. La marca "solicitado" que se ve
    en Alertas de Stock se DERIVA de estas solicitudes (status REALIZADA),
    no se guarda un flag redundante en stock.
    """
    __tablename__ = "purchase_requests"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=now_ar, nullable=False)

    year = db.Column(db.Integer, nullable=False)
    seq = db.Column(db.Integer, nullable=False)
    number = db.Column(db.String(32), unique=True, nullable=False)  # SC-2026-0001

    # PENDIENTE (recien creada) / REALIZADA (ya enviada a mano)
    status = db.Column(db.String(16), nullable=False, default="PENDIENTE")

    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    observation = db.Column(db.String(255), nullable=True)
    sent_at = db.Column(db.DateTime, nullable=True)  # para el envio automatico futuro

    created_by = db.relationship("User")
    lines = db.relationship(
        "PurchaseRequestLine",
        backref="request",
        cascade="all, delete-orphan",
        order_by="PurchaseRequestLine.id",
    )
    # Destinatarios seleccionados al crear la solicitud (aditivo). El mail se
    # resuelve en vivo desde el usuario; guardamos el vinculo para trazabilidad
    # de a quien se le mando.
    recipients = db.relationship(
        "PurchaseRequestRecipient",
        backref="request",
        cascade="all, delete-orphan",
    )


class PurchaseRequestLine(db.Model):
    __tablename__ = "purchase_request_lines"
    id = db.Column(db.Integer, primary_key=True)
    purchase_request_id = db.Column(
        db.Integer, db.ForeignKey("purchase_requests.id"), nullable=False
    )
    item_id = db.Column(db.Integer, db.ForeignKey("items.id"), nullable=False)
    qty = db.Column(db.Integer, nullable=False)
    # Preparado para futuro: especificaciones por item (aditivo, sin migracion disruptiva).
    spec = db.Column(db.String(255), nullable=True)

    item = db.relationship("Item")


class PurchaseRequestRecipient(db.Model):
    """Destinatario de una solicitud de compra (usuario con email).

    Aditivo: no toca stock ni la lógica existente. Reemplaza la lista de mails
    hardcodeada por una selección de usuarios en el momento de crear la
    solicitud. El email se lee del usuario vinculado, no se copia acá.
    """
    __tablename__ = "purchase_request_recipients"
    id = db.Column(db.Integer, primary_key=True)
    purchase_request_id = db.Column(
        db.Integer, db.ForeignKey("purchase_requests.id"), nullable=False
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    user = db.relationship("User")

    __table_args__ = (
        db.UniqueConstraint(
            "purchase_request_id", "user_id", name="uq_pr_recipient"
        ),
    )


# ------------------ LOGIN MANAGER ------------------

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ------------------ HELPERS ------------------

def seed_defaults():

    # Usuario admin por defecto (solo si no existe ninguno).
    # Ya no hay password hardcodeada: se leen BOOTSTRAP_ADMIN_USERNAME y
    # BOOTSTRAP_ADMIN_PASSWORD del entorno. Si faltan, la app NO crea
    # ningun usuario automatico y avisa por consola.
    if User.query.count() == 0:
        bootstrap_user = os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "").strip()
        bootstrap_pass = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "").strip()
        if bootstrap_user and bootstrap_pass:
            admin = User(
                username=bootstrap_user,
                full_name="Administrador",
                role="ADMIN",
            )
            admin.set_password(bootstrap_pass)
            db.session.add(admin)
            db.session.commit()
            print(f"[INFO] Usuario admin '{bootstrap_user}' creado desde BOOTSTRAP_ADMIN_*.")
        else:
            # Seguridad: no se crea ningun usuario por defecto (nada de admin/admin).
            # Para el primer arranque, definir BOOTSTRAP_ADMIN_USERNAME y
            # BOOTSTRAP_ADMIN_PASSWORD en el entorno.
            print(
                "[WARN] No hay usuarios y faltan BOOTSTRAP_ADMIN_USERNAME/"
                "BOOTSTRAP_ADMIN_PASSWORD. No se creo ningun usuario. "
                "Defini esas variables y reinicia para crear el admin inicial."
            )

    default_locations = [
        ("Descartes", "Items dañados/descartes",   False, False),
        ("Utilizado", "Consumibles utilizados",    True,  False),
        ("Proveedor", "Origen de repuestos",       True,  False),
        (LOCATION_EN_REPARACION, "Items en reparación (mesa/proveedor)", False, False),
        (LOCATION_RECUPERADO, "Origen de ingresos recuperados del campo", True, False),
    ]
    for name, desc, is_ext, is_truck in default_locations:
        if not Location.query.filter_by(name=name).first():
            db.session.add(Location(name=name, description=desc, is_external=is_ext, is_truck=is_truck))
    db.session.commit()

    # Eliminar ubicacion "En falla" si existe y no tiene referencias
    en_falla = Location.query.filter_by(name="En falla").first()
    if en_falla:
        try:
            db.session.delete(en_falla)
            db.session.commit()
            print("[INFO] Ubicacion 'En falla' eliminada.")
        except Exception:
            db.session.rollback()
            print("[WARN] No se pudo eliminar 'En falla': tiene referencias activas.")


def upsert_stock(item_id: int, location_id: int, delta: int) -> None:
    """Suma/resta stock en una ubicacion.
    - Crea fila si no existe y el delta es positivo.
    - No permite stock negativo.
    - Si el item es rastreable, globalmente solo puede existir 1 unidad.
    - Si un rastreable queda en 0, elimina la fila para no ensuciar el stock.
    - Si NO es rastreable y queda en 0, conserva la fila.
    """
    it = Item.query.get(int(item_id))
    is_trackable = bool(it.trackable) if it else False

    row = Stock.query.filter_by(item_id=item_id, location_id=location_id).first()

    if row is None and delta < 0:
        raise ValueError("Stock insuficiente en la ubicacion de origen")

    if row is None:
        row = Stock(item_id=item_id, location_id=location_id, quantity=0)
        db.session.add(row)
        db.session.flush()

    current_qty = row.quantity or 0
    new_qty = current_qty + delta

    if new_qty < 0:
        raise ValueError("Stock insuficiente en la ubicacion de origen")

    if is_trackable:
        total_global = (
            db.session.query(func.coalesce(func.sum(Stock.quantity), 0))
            .filter(Stock.item_id == item_id)
            .scalar()
        ) or 0

        projected_total = total_global - current_qty + new_qty

        if projected_total > 1:
            raise ValueError("Item rastreable: globalmente solo puede existir 1 unidad en todo el sistema")

    if is_trackable and new_qty == 0:
        db.session.delete(row)
    else:
        row.quantity = new_qty


# ------------------ HELPERS: UNIDADES SERIALIZADAS ------------------

def units_in_stock_query(item_id: int, location_id: int | None = None):
    """Unidades EN_STOCK de un ítem (opcionalmente en una ubicación)."""
    q = ItemUnit.query.filter_by(item_id=item_id, status=UNIT_EN_STOCK)
    if location_id is not None:
        q = q.filter_by(location_id=location_id)
    return q


def count_units_in_stock(item_id: int, location_id: int) -> int:
    return units_in_stock_query(item_id, location_id).count()


def resolve_serial_units_out(item_id: int, from_id: int, qty: int, form, field: str = "unit_id"):
    """Regla auto/elegir para sacar unidades serializadas de una ubicación interna.

    Devuelve (units, error_msg):
      - si hay más seriales que la cantidad (avail > qty): hay que elegir cuáles
        (se leen del form). Si no eligió exactamente qty, error.
      - si hay 1 o tantos como la cantidad (avail <= qty): se usan todos (auto).
      - si no hay seriales cargados (avail = 0): units=[] (se mueve por cantidad).
    """
    from_units = (units_in_stock_query(item_id, from_id)
                  .order_by(ItemUnit.created_at, ItemUnit.id).all())
    avail = len(from_units)
    if avail > qty:
        ids = {int(x) for x in form.getlist(field) if x.isdigit()}
        chosen = [u for u in from_units if u.id in ids]
        if len(chosen) != qty:
            return None, (f"Hay {avail} seriales en el origen y movés {qty}: "
                          f"elegí exactamente {qty}.")
        return chosen, None
    return from_units, None


def apply_serial_units_out(units, to_id: int):
    """Aplica el destino a cada unidad que sale y devuelve sus seriales (para obs).

    - a Descartes -> DESCARTADO
    - a ubicación externa (Utilizado, Proveedor, etc.) -> ENTREGADO
    - a ubicación interna (otra camioneta, depósito) -> se reubica, sigue EN_STOCK
    """
    to_loc = Location.query.get(to_id)
    is_descartes = bool(to_loc and to_loc.name == LOCATION_DESCARTES)
    to_ext = bool(to_loc and to_loc.is_external)
    for u in units:
        if is_descartes:
            u.status = UNIT_DESCARTADO
            u.location_id = None
        elif to_ext:
            u.status = UNIT_ENTREGADO
            u.location_id = None
        else:
            u.location_id = to_id
    return [u.serial for u in units]


def serial_obs(observation, serials):
    """Agrega los seriales movidos a la observación (para log y remito)."""
    if not serials:
        return observation
    sn = "S/N: " + ", ".join(serials)
    return (f"{observation} · {sn}" if observation else sn)[:255]


def build_units_map(location_ids=None):
    """Mapa de seriales EN_STOCK para el selector de egreso serializado.

    Estructura: { item_id: { loc_id: [[unit_id, serial], ...] } }
    location_ids: si se pasa, limita a esas ubicaciones (ej. camionetas del técnico).
    Devuelve (units_map, serialized_item_ids).
    """
    serialized_item_ids = [it.id for it in Item.query.filter_by(serialized=True).all()]
    units_map = {}
    if serialized_item_ids:
        uq = (ItemUnit.query
              .filter(ItemUnit.status == UNIT_EN_STOCK,
                      ItemUnit.item_id.in_(serialized_item_ids),
                      ItemUnit.location_id.isnot(None)))
        if location_ids is not None:
            ids = list(location_ids) or [-1]
            uq = uq.filter(ItemUnit.location_id.in_(ids))
        for u in uq.order_by(ItemUnit.serial).all():
            units_map.setdefault(u.item_id, {}).setdefault(u.location_id, []).append([u.id, u.serial])
    return units_map, serialized_item_ids


def next_movement_number():
    y = now_ar().year
    last = Movement.query.filter_by(year=y).order_by(Movement.seq.desc()).first()
    seq = (last.seq or 0) + 1 if last else 1
    return y, seq, f"MOV-{seq:03d}"


def next_remito_number(year: int | None = None):
    """Siguiente numero de remito para el anio dado (por defecto, el actual).

    Formato: R-YYYY-0001, secuencia independiente por anio. Devuelve
    (year, seq, number). El parametro `year` es opcional y sirve para pruebas;
    el uso normal (sin argumentos) no cambia.

    Nota: bajo alta concurrencia dos requests podrian calcular el mismo seq
    antes de commitear. La constraint UNIQUE(year, seq) y UNIQUE(number) lo
    frenan a nivel DB (el segundo commit falla). No se resuelve aqui una
    coordinacion distribuida compleja; queda documentado como riesgo menor.
    """
    y = year if year is not None else now_ar().year
    last = Remito.query.filter_by(year=y).order_by(Remito.seq.desc()).first()
    seq = (last.seq or 0) + 1 if last else 1
    return y, seq, f"R-{y}-{seq:04d}"


def next_purchase_request_number():
    y = now_ar().year
    last = (
        PurchaseRequest.query.filter_by(year=y)
        .order_by(PurchaseRequest.seq.desc())
        .first()
    )
    seq = (last.seq or 0) + 1 if last else 1
    return y, seq, f"SC-{y}-{seq:04d}"


_BOOL_TRUE = {"1", "true", "verdadero", "yes", "y", "si", "sí", "s"}
_BOOL_FALSE = {"0", "false", "falso", "no", "n"}


def parse_bool_cell(value: str, default: bool):
    """Interpreta una celda booleana del CSV de importacion.

    Acepta: 1/0, true/false, yes/no, si/no, sí/no (case-insensitive).
    Vacio -> `default`. Valor no reconocido -> None (el caller lo trata como
    fila con error, sin adivinar).
    """
    v = (value or "").strip().lower()
    if v == "":
        return default
    if v in _BOOL_TRUE:
        return True
    if v in _BOOL_FALSE:
        return False
    return None


def code_prefix(code: str) -> str:
    """Devuelve prefijo antes del primer '-'. Ej: ELT-012 -> ELT"""
    if not code:
        return ""
    return code.split("-", 1)[0].strip().upper()


def _movements_filters_from_request() -> dict:
    """Normaliza filtros de movimientos a un formato único.

    Soporta nombres viejos y nuevos de templates para evitar romper UI.
    """
    item_filter = (request.args.get("item_id") or request.args.get("item_filter") or "").strip()
    user_filter = (request.args.get("user_id") or request.args.get("user_filter") or "").strip()
    date_from = (request.args.get("date_from") or request.args.get("from_date") or "").strip()
    date_to = (request.args.get("date_to") or request.args.get("to_date") or "").strip()

    limit_raw = (request.args.get("limit") or "").strip()
    limit = 200
    if limit_raw.isdigit():
        limit = max(1, min(int(limit_raw), 50000))

    return {
        "item_filter": item_filter,
        "user_filter": user_filter,
        "date_from": date_from,
        "date_to": date_to,
        "limit": limit,
        "limit_raw": limit_raw,
    }


def _build_movements_query(filters: dict):
    """Devuelve query SQLAlchemy con los filtros aplicados.

    Excluye los movimientos de Ingresos/Egresos (los que tienen supplier_id):
    esos se ven en su propia sección, no en el listado/exportación de Movimientos.
    Los movimientos internos historicos (supplier_id NULL) no se tocan.
    """
    q = Movement.query.join(Item).join(User).filter(Movement.supplier_id.is_(None))

    if filters["item_filter"].isdigit():
        q = q.filter(Movement.item_id == int(filters["item_filter"]))
    if filters["user_filter"].isdigit():
        q = q.filter(Movement.user_id == int(filters["user_filter"]))
    if filters["date_from"]:
        q = q.filter(Movement.created_at >= datetime.fromisoformat(filters["date_from"]))
    if filters["date_to"]:
        q = q.filter(Movement.created_at <= datetime.fromisoformat(filters["date_to"] + "T23:59:59"))

    return q



# ------------------ MIGRACIONES SQLITE (best-effort) ------------------

def ensure_sqlite_schema() -> None:
    """Best-effort schema migration for SQLite.

    Objetivo: que el sistema no explote si ya existe un stocks.db viejo.
    - Si la DB no existe, no hace nada (create_all la crea).
    - Si existe, agrega columnas faltantes con ALTER TABLE.

    Nota: SQLite no permite alterar constraints facilmente (NOT NULL, FK, etc.).
    Para un entorno local simple, esto alcanza.
    """
    if not DB_PATH.exists():
        return

    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()

        def table_exists(name: str) -> bool:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
            return cur.fetchone() is not None

        def columns(table: str) -> set[str]:
            cur.execute(f"PRAGMA table_info({table})")
            return {r[1] for r in cur.fetchall()}

        def add_column(table: str, coldef: str) -> None:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {coldef}")

        # ------------------------------------------------------------------
        # SINCRONIZACION AUTOMATICA DE COLUMNAS (aditiva, generica).
        # Recorre TODOS los modelos y agrega cualquier columna que exista en el
        # modelo pero falte en la tabla real. No borra, no modifica, no renombra.
        # Asi, agregar un campo a un modelo NO requiere tocar nada mas: al
        # reiniciar el servicio la DB se pone al dia sola.
        # ------------------------------------------------------------------
        try:
            from sqlalchemy.dialects import sqlite as _sqld
            _dialect = _sqld.dialect()

            def _scalar_default(col):
                d = col.default
                if d is not None and getattr(d, "is_scalar", False):
                    val = d.arg
                    if isinstance(val, bool):
                        return "1" if val else "0"
                    if isinstance(val, (int, float)):
                        return str(val)
                    if isinstance(val, str):
                        return "'" + val.replace("'", "''") + "'"
                return None

            for _tbl in db.metadata.sorted_tables:
                if not table_exists(_tbl.name):
                    continue  # tabla nueva: la crea db.create_all()
                _have = columns(_tbl.name)
                for _col in _tbl.columns:
                    if _col.name in _have:
                        continue
                    try:
                        _type = _col.type.compile(dialect=_dialect)
                    except Exception:
                        _type = "TEXT"
                    _default = _scalar_default(_col)
                    _clause = f"{_col.name} {_type}"
                    if _default is not None:
                        if not _col.nullable:
                            _clause += " NOT NULL"
                        _clause += f" DEFAULT {_default}"
                    # Sin default constante -> se agrega NULLABLE para que el
                    # ALTER nunca falle (SQLite no permite ADD COLUMN NOT NULL
                    # sin default constante). El ORM sigue exigiendo el valor.
                    try:
                        add_column(_tbl.name, _clause)
                        print(f"[schema-sync] +columna {_tbl.name}.{_col.name}")
                    except Exception as _e:
                        print(f"[schema-sync][WARN] {_tbl.name}.{_col.name}: {_e}")
        except Exception as _e:
            print(f"[schema-sync][WARN] sync generico omitido: {_e}")

        if table_exists("locations"):
            c = columns("locations")
            if "description" not in c:
                add_column("locations", "description TEXT")
            if "is_external" not in c:
                add_column("locations", "is_external INTEGER NOT NULL DEFAULT 0")
            if "is_truck" not in c:
                add_column("locations", "is_truck INTEGER NOT NULL DEFAULT 0")

        if not table_exists("location_responsibles"):
            cur.execute("""
                CREATE TABLE location_responsibles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    location_id INTEGER NOT NULL REFERENCES locations(id),
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    UNIQUE(location_id, user_id)
                )
            """)

        if table_exists("users"):
            c = columns("users")
            if "full_name" not in c:
                add_column("users", "full_name TEXT NOT NULL DEFAULT ''")
            if "role" not in c:
                add_column("users", "role TEXT NOT NULL DEFAULT 'LECTOR'")
            if "password_hash" not in c:
                add_column("users", "password_hash TEXT NOT NULL DEFAULT ''")
            if "email" not in c:
                add_column("users", "email TEXT")

        if table_exists("items"):
            c = columns("items")
            if "trackable" not in c:
                add_column("items", "trackable INTEGER NOT NULL DEFAULT 0")
            if "is_active" not in c:
                add_column("items", "is_active INTEGER NOT NULL DEFAULT 1")
            if "stock_min" not in c:
                add_column("items", "stock_min INTEGER NOT NULL DEFAULT 0")
            if "reference_link" not in c:
                add_column("items", "reference_link TEXT")
            if "unit" not in c:
                add_column("items", "unit VARCHAR(16) NOT NULL DEFAULT 'unidad'")
            if "serialized" not in c:
                add_column("items", "serialized INTEGER NOT NULL DEFAULT 0")

        # Unidades serializadas (aditivo). Tabla nueva: si no existe, se crea.
        # Los ítems existentes quedan con serialized=0 y sin unidades, o sea sin
        # cambios de comportamiento.
        if not table_exists("item_units"):
            cur.execute("""
                CREATE TABLE item_units (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER NOT NULL REFERENCES items(id),
                    serial VARCHAR(120) NOT NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'EN_STOCK',
                    location_id INTEGER REFERENCES locations(id),
                    notes VARCHAR(255),
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(item_id, serial)
                )
            """)

        # Proveedores (aditivo). Tabla nueva: si no existe, se crea.
        if not table_exists("suppliers"):
            cur.execute("""
                CREATE TABLE suppliers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    contact_name VARCHAR(120) NOT NULL,
                    business_name VARCHAR(160),
                    cuit VARCHAR(20),
                    legal_name VARCHAR(160),
                    email VARCHAR(255),
                    phone VARCHAR(40),
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

        # Ingresos/Egresos: proveedor asociado en el movimiento (aditivo).
        if table_exists("movements"):
            c = columns("movements")
            if "supplier_id" not in c:
                add_column("movements", "supplier_id INTEGER REFERENCES suppliers(id)")

        # Remito pendiente de impresion (aditivo).
        if table_exists("remitos"):
            c = columns("remitos")
            if "print_pending" not in c:
                add_column("remitos", "print_pending INTEGER NOT NULL DEFAULT 0")

        # Destinatarios de solicitudes de compra (aditivo). Tabla nueva: si no
        # existe, se crea. Reemplaza la lista de mails hardcodeada.
        if not table_exists("purchase_request_recipients"):
            cur.execute("""
                CREATE TABLE purchase_request_recipients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    purchase_request_id INTEGER NOT NULL REFERENCES purchase_requests(id),
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    UNIQUE(purchase_request_id, user_id)
                )
            """)

        if table_exists("categories"):
            c = columns("categories")
            if "prefix" not in c:
                add_column("categories", "prefix VARCHAR(8)")
            # Backfill de las 12 categorías conocidas: solo donde el prefijo
            # esté vacío. No pisa prefijos ya cargados a mano.
            cur.execute("SELECT id, name, prefix FROM categories")
            for cid, cname, cpref in cur.fetchall():
                if not (cpref or "").strip():
                    p = CATEGORY_PREFIXES.get(normalize_text(cname))
                    if p:
                        cur.execute("UPDATE categories SET prefix=? WHERE id=?", (p, cid))

        if table_exists("movements"):
            c = columns("movements")
            if "user_id" not in c:
                add_column("movements", "user_id INTEGER")
            if "observation" not in c:
                add_column("movements", "observation TEXT")
            if "year" not in c:
                add_column("movements", "year INTEGER")
            if "seq" not in c:
                add_column("movements", "seq INTEGER")
            if "number" not in c:
                add_column("movements", "number VARCHAR(32)")
            # backfill responsable si habia movimientos viejos
            try:
                cur.execute("UPDATE movements SET user_id = COALESCE(user_id, 1) WHERE user_id IS NULL")
            except Exception:
                pass

        # Remitos: responsables elegidos (aditivo, nullable). Los remitos viejos
        # quedan con estos campos en NULL.
        if table_exists("remitos"):
            c = columns("remitos")
            if "responsible_from_id" not in c:
                add_column("remitos", "responsible_from_id INTEGER")
            if "responsible_to_id" not in c:
                add_column("remitos", "responsible_to_id INTEGER")

        # Pendientes: item/cantidad de devolucion esperada (aditivo, nullable).
        # NULL = comportamiento previo (vuelve el mismo item y la misma cantidad).
        if table_exists("pending_deliveries"):
            c = columns("pending_deliveries")
            if "return_item_id" not in c:
                add_column("pending_deliveries", "return_item_id INTEGER")
            if "return_qty" not in c:
                add_column("pending_deliveries", "return_qty INTEGER")

        conn.commit()
    finally:
        conn.close()


def location_is_external(location_id: int) -> bool:
    loc = Location.query.get(location_id)
    return bool(loc and loc.is_external)


# ------------------ HELPERS: OPERACIONES DESTRUCTIVAS ------------------

def _backup_db(label: str) -> Path:
    """Backup consistente de stocks.db usando la API de backup de SQLite.

    Helper UNICO de backup. Lo usan: backup manual (admin_backup_db),
    clear-stock, clear-items y reset-db. Requisitos:
      - usa BACKUP_DIR (crea la carpeta si no existe);
      - verifica que DB_PATH exista;
      - sanitiza el label;
      - nombre con timestamp;
      - usa sqlite3.Connection.backup() (NO shutil.copy2 sobre una base abierta);
      - cierra origen y destino;
      - devuelve el Path del backup;
      - verifica que el archivo resultante exista y no este vacio;
      - si algo falla, propaga la excepcion (el caller debe abortar).
    """
    backup_dir = BACKUP_DIR
    backup_dir.mkdir(parents=True, exist_ok=True)

    if not DB_PATH.exists():
        raise FileNotFoundError(f"No existe la base a respaldar: {DB_PATH}")

    ts = datetime.utcnow().strftime("%Y-%m-%d_%H%M%S")
    # Sanitizamos label por las dudas.
    safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
    dest = backup_dir / f"stocks_{safe_label}_{ts}.db"

    # WAL: consolidar el -wal en el .db para que la copia sea consistente/completa.
    try:
        _conn = sqlite3.connect(str(DB_PATH))
        _conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        _conn.close()
    except Exception as exc:  # best-effort: el backup() igual copia consistente
        print(f"[WARN] wal_checkpoint antes de backup fallo: {exc}")

    # sqlite3 backup(): copia consistente incluso con la base abierta/en uso.
    src = None
    out = None
    try:
        src = sqlite3.connect(str(DB_PATH))
        out = sqlite3.connect(str(dest))
        src.backup(out)
    finally:
        if out is not None:
            out.close()
        if src is not None:
            src.close()

    if not dest.exists() or dest.stat().st_size == 0:
        raise IOError(f"El backup no se genero correctamente: {dest}")

    return dest


def _log_destructive(op: str, username: str, result: str, detail: str = "") -> None:
    """Appendea una linea al log de operaciones destructivas.

    Formato TSV: timestamp  username  op  result  detail
    No levanta excepciones: si no se puede escribir, imprime por consola.
    """
    try:
        log_dir = LOG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        with open(log_dir / "destructive_ops.log", "a", encoding="utf-8") as f:
            # Reemplazamos tabs/newlines por espacios en detail para no romper TSV.
            safe_detail = " ".join(str(detail).split())
            f.write(f"{ts}\t{username}\t{op}\t{result}\t{safe_detail}\n")
    except Exception as exc:  # log best-effort, nunca debe romper el handler
        print(f"[WARN] No se pudo escribir destructive_ops.log: {exc}")

def stock_level_class(item, quantity: int) -> str:
    """Devuelve clase visual segun stock_min.
    Solo aplica a items NO rastreables con stock_min > 0.
    """
    if not item or item.trackable:
        return ""

    stock_min = int(item.stock_min or 0)
    if stock_min <= 0:
        return ""

    # Semáforo Opción B: rojo/amarillo/verde sin huecos.
    #  - rojo   : qty <= floor(stock_min * 0.6)
    #  - verde  : qty >= ceil(stock_min * 1.4)
    #  - amarillo: todo lo del medio (incluye stock_min)
    low_threshold = math.floor(stock_min * 0.6)
    high_threshold = math.ceil(stock_min * 1.4)

    if quantity <= low_threshold:
        return "stock-red"
    if quantity >= high_threshold:
        return "stock-green"
    return "stock-yellow"


def is_alert_stock(item, quantity: int) -> bool:
    """Única fuente de verdad para decidir si un registro está en alerta.

    Coherente con el semáforo: un registro está en alerta si su nivel es rojo o
    amarillo. NO se usa el atajo `quantity < stock_min`, que dejaba afuera los
    amarillos por encima del mínimo (ej. stock_min=10, quantity=11).
    """
    return stock_level_class(item, quantity) in ("stock-red", "stock-yellow")


# ------------------ RUTAS ------------------

@app.route("/")
@login_required
def home():
    # Dashboard por rol: cada rol ve solo las tarjetas que le sirven.
    role = current_user.role
    hoy = now_ar().replace(hour=0, minute=0, second=0, microsecond=0)
    cards = []
    top_consumos = []  # KPI solo lectura: se llena solo para ADMIN/SUPERVISOR

    if role == "TECNICO":
        # El técnico ve lo suyo: su stock, no las métricas globales de la empresa.
        tech_location_ids = {
            l.id for l in Location.query.join(LocationResponsible).filter(
                LocationResponsible.user_id == current_user.id
            ).all()
        }

        if tech_location_ids:
            mi_stock = db.session.query(func.coalesce(func.sum(Stock.quantity), 0)).filter(
                Stock.location_id.in_(tech_location_ids), Stock.quantity > 0
            ).scalar() or 0
            mis_items = db.session.query(Stock.item_id).filter(
                Stock.location_id.in_(tech_location_ids), Stock.quantity > 0
            ).distinct().count()
            movs_hoy = Movement.query.filter(
                Movement.created_at >= hoy,
                Movement.from_location_id.in_(tech_location_ids),
            ).count()
        else:
            mi_stock, mis_items, movs_hoy = 0, 0, 0

        cards = [
            {"label": "Mi stock (unidades)", "value": mi_stock, "hint": "en mi(s) ubicación(es)", "tone": "accent", "href": url_for("stock")},
            {"label": "Ítems distintos", "value": mis_items, "hint": "con stock disponible", "tone": "", "href": url_for("stock")},
            {"label": "Mis movimientos hoy", "value": movs_hoy, "hint": "registrados hoy", "tone": "", "href": url_for("item_usage")},
        ]

        ultimos_movimientos = (
            Movement.query.filter(Movement.from_location_id.in_(tech_location_ids))
            .order_by(Movement.created_at.desc()).limit(8).all()
        ) if tech_location_ids else []

    elif role == "LECTOR":
        stock_total = db.session.query(func.coalesce(func.sum(Stock.quantity), 0)).scalar() or 0
        total_items = Item.query.filter_by(is_active=True).count()
        cards = [
            {"label": "Stock total (unidades)", "value": stock_total, "hint": f"{total_items} ítems activos", "tone": "accent", "href": url_for("stock")},
        ]
        ultimos_movimientos = (
            Movement.query.order_by(Movement.created_at.desc()).limit(8).all()
        )

    else:
        # ADMIN / SUPERVISOR: visión global completa.
        stock_total = db.session.query(func.coalesce(func.sum(Stock.quantity), 0)).scalar() or 0
        total_items = Item.query.filter_by(is_active=True).count()
        # Misma fuente de verdad que /stock-alerts y Solicitudes de compra:
        # ítems distintos con al menos una ubicación en rojo/amarillo (semáforo),
        # respetando ubicaciones responsables. No usar quantity < stock_min.
        alertas_bajo = len(alert_items_distinct())
        movimientos_hoy = Movement.query.filter(Movement.created_at >= hoy).count()
        remitos_pendientes = PendingDelivery.query.filter_by(returned=False).count()

        # --- KPIs adicionales (SOLO LECTURA, no tocan stock ni esquema) ---
        # "Consumo" = movimientos cuyo destino es la ubicación "Utilizado",
        # mismo criterio que la pantalla Utilizados (item_usage).
        inicio_mes = hoy.replace(day=1)
        hace_30 = hoy - timedelta(days=30)
        utilizado = Location.query.filter_by(name="Utilizado").first()

        consumo_mes = 0
        if utilizado is not None:
            consumo_mes = db.session.query(
                func.coalesce(func.sum(Movement.qty), 0)
            ).filter(
                Movement.to_location_id == utilizado.id,
                Movement.created_at >= inicio_mes,
            ).scalar() or 0

            top_rows = (
                db.session.query(Item, func.sum(Movement.qty).label("total"))
                .join(Movement, Movement.item_id == Item.id)
                .filter(
                    Movement.to_location_id == utilizado.id,
                    Movement.created_at >= inicio_mes,
                )
                .group_by(Item.id)
                .order_by(func.sum(Movement.qty).desc())
                .limit(5)
                .all()
            )
            top_consumos = [{"item": it, "total": int(total or 0)} for it, total in top_rows]

        # Ítems activos con stock > 0 que no tuvieron ningún movimiento en 30 días
        # (stock "quieto"). Cálculo con sets en memoria: barato para este tamaño.
        ids_con_stock = {r[0] for r in db.session.query(Stock.item_id).filter(Stock.quantity > 0).distinct()}
        ids_activos = {r[0] for r in db.session.query(Item.id).filter(Item.is_active == True)}
        ids_movidos = {r[0] for r in db.session.query(Movement.item_id).filter(Movement.created_at >= hace_30).distinct()}
        sin_movimiento_30d = len((ids_con_stock & ids_activos) - ids_movidos)

        cards = [
            {"label": "Stock total (unidades)", "value": stock_total, "hint": f"{total_items} ítems activos", "tone": "accent", "href": url_for("stock")},
            {"label": "Ítems en alerta", "value": alertas_bajo, "hint": "nivel rojo o amarillo", "tone": "warn", "href": url_for("stock_alerts")},
            {"label": "Movimientos hoy", "value": movimientos_hoy, "hint": "registrados hoy", "tone": "", "href": url_for("movements")},
            {"label": "Pendientes", "value": remitos_pendientes, "hint": "sin devolución confirmada", "tone": "danger", "href": url_for("pending_deliveries")},
            {"label": "Consumo del mes", "value": consumo_mes, "hint": "unidades utilizadas", "tone": "", "href": url_for("item_usage")},
            {"label": "Sin movimiento 30d", "value": sin_movimiento_30d, "hint": "ítems con stock quieto", "tone": "warn", "href": url_for("stock")},
        ]
        ultimos_movimientos = (
            Movement.query.order_by(Movement.created_at.desc()).limit(8).all()
        )

    return render_template("index.html", cards=cards, ultimos_movimientos=ultimos_movimientos, top_consumos=top_consumos)


# ---- AUTH ----

@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"], error_message="Demasiados intentos de acceso. Espera un minuto e intenta de nuevo.")
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        user = User.query.filter_by(username=username).first()
        if not user or not user.check_password(password):
            flash("Usuario o contraseña incorrectos", "error")
            return redirect(url_for("login"))

        login_user(user)
        return redirect(url_for("home"))

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ---- PERFIL: cambio de clave propio (cualquier usuario logueado) ----

@app.route("/perfil", methods=["GET", "POST"])
@login_required
def perfil():
    if request.method == "POST":
        current_password = request.form.get("current_password", "").strip()
        new_password = request.form.get("new_password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not current_user.check_password(current_password):
            flash("La contraseña actual es incorrecta.", "error")
            return redirect(url_for("perfil"))

        if not new_password or len(new_password) < 4:
            flash("La nueva contraseña es inválida (mínimo 4 caracteres).", "error")
            return redirect(url_for("perfil"))

        if new_password != confirm_password:
            flash("La nueva contraseña y su confirmación no coinciden.", "error")
            return redirect(url_for("perfil"))

        if new_password == current_password:
            flash("La nueva contraseña debe ser distinta de la actual.", "error")
            return redirect(url_for("perfil"))

        # Solo actua sobre el usuario logueado. Nunca sobre otros.
        current_user.set_password(new_password)
        db.session.commit()
        flash("Contraseña actualizada.", "ok")
        return redirect(url_for("perfil"))

    return render_template("perfil.html")


# ---- ADMIN: RESET DB ----

@app.route("/admin/reset-db", methods=["POST"])
@login_required
@role_required("ADMIN")
def admin_reset_db():
    # Operacion irreversible. Protecciones:
    #  1) Apagada por default. Solo corre si ENABLE_RESET_DB=true en entorno.
    #  2) Exige tipear CONFIRM_RESET_DB literal en el form.
    #  3) Hace backup automatico antes de tocar nada.
    #  4) Loguea cada intento (exitoso o fallido) en destructive_ops.log.
    username = getattr(current_user, "username", "?")

    if os.environ.get("ENABLE_RESET_DB", "").strip().lower() != "true":
        _log_destructive("reset_db", username, "DISABLED", "ENABLE_RESET_DB no seteada")
        flash(
            "Reinicio de BD deshabilitado. Para habilitar, definir "
            "ENABLE_RESET_DB=true en el entorno.",
            "error",
        )
        return redirect(url_for("admin_panel"))

    confirm_text = (request.form.get("confirm_text", "") or "").strip()
    if confirm_text != CONFIRM_RESET_DB:
        _log_destructive("reset_db", username, "REJECTED", "confirm_text invalido")
        flash(
            f"Confirmación inválida. Tenés que escribir exactamente '{CONFIRM_RESET_DB}'.",
            "error",
        )
        return redirect(url_for("admin_panel"))

    # Backup antes de destruir. Si falla, abortamos sin tocar nada.
    try:
        backup_path = _backup_db("reset_db")
    except Exception as exc:
        _log_destructive("reset_db", username, "ABORT_NO_BACKUP", str(exc))
        flash(f"No se pudo crear backup previo. Operación abortada: {exc}", "error")
        return redirect(url_for("admin_panel"))

    try:
        db.session.remove()
        db.engine.dispose()
        if DB_PATH.exists():
            DB_PATH.unlink()
        with app.app_context():
            db.create_all()
            seed_defaults()
        _log_destructive("reset_db", username, "OK", f"backup={backup_path.name}")
        flash(
            f"Base reiniciada. Backup previo: {backup_path.name}",
            "ok",
        )
    except Exception as e:
        _log_destructive("reset_db", username, "ERROR", str(e))
        flash(f"No se pudo reiniciar: {e}", "error")
    return redirect(url_for("home"))

@app.route("/admin/backup-db", methods=["POST"])
@login_required
@role_required("ADMIN")
def admin_backup_db():
    username = getattr(current_user, "username", "?")
    try:
        # Reutiliza EXACTAMENTE el mismo helper que las operaciones destructivas.
        db.session.commit()
        db.session.remove()
        backup_path = _backup_db("manual")
        _log_destructive("backup_manual", username, "OK", f"backup={backup_path.name}")
        flash(f"Backup generado correctamente: {backup_path.name}", "ok")
    except Exception as e:
        _log_destructive("backup_manual", username, "ERROR", str(e))
        flash(f"No se pudo generar el backup: {e}", "error")

    return redirect(url_for("admin_panel"))



# ---- ADMIN: PANEL + AJUSTE DE STOCK ----

@app.route("/admin", methods=["GET"])
@login_required
@role_required("ADMIN")
def admin_panel():
    # Panel simple de acciones de administración
    return render_template("admin.html")


@app.route("/admin/adjust-stock", methods=["GET", "POST"])
@login_required
@role_required("ADMIN")
def admin_adjust_stock():
    # Para operar: solo items activos. Históricos siguen mostrando items dados de baja.
    items_list = Item.query.filter_by(is_active=True).order_by(Item.code).all()
    locations_list = Location.query.order_by(Location.name).all()

    proveedor = Location.query.filter_by(name=LOCATION_PROVEEDOR).first()
    baja = Location.query.filter_by(name=LOCATION_DESCARTES).first()

    if request.method == "POST":
        item_id = request.form.get("item_id", "").strip()
        location_id = request.form.get("location_id", "").strip()
        action = request.form.get("action", "").strip()  # SUMAR / RESTAR
        qty_raw = request.form.get("qty", "").strip()
        reason = (request.form.get("reason", "") or "").strip()

        if not (item_id.isdigit() and location_id.isdigit()):
            flash("Item y ubicación son obligatorios.", "error")
            return redirect(url_for("admin_adjust_stock"))

        if action not in ("SUMAR", "RESTAR"):
            flash("Acción inválida.", "error")
            return redirect(url_for("admin_adjust_stock"))

        try:
            qty = int(qty_raw)
            if qty <= 0:
                raise ValueError()
        except Exception:
            flash("Cantidad inválida (debe ser > 0).", "error")
            return redirect(url_for("admin_adjust_stock"))

        if not reason:
            flash("La observación/motivo es obligatoria para ajustar stock.", "error")
            return redirect(url_for("admin_adjust_stock"))

        item_id = int(item_id)

        it = Item.query.get(item_id)
        if not it or not it.is_active:
            flash("El item seleccionado está dado de baja o no existe.", "error")
            return redirect(url_for("admin_adjust_stock"))

        # Serializados: SUMAR se permite (agrega cupo para luego etiquetar seriales
        # desde la ficha del ítem). RESTAR se bloquea: una baja debe elegir qué
        # serial sale (desde Movimientos) o quitarse desde la ficha, para no
        # desincronizar las unidades.
        if it.serialized and action == "RESTAR":
            flash(
                "Ese ítem es serializado: para dar de baja stock elegí el serial "
                "desde Movimientos (o quitá el serial desde la ficha del ítem).",
                "error",
            )
            return redirect(url_for("admin_adjust_stock"))
        location_id = int(location_id)

        if proveedor is None or baja is None:
            flash(
                f"Faltan ubicaciones requeridas: '{LOCATION_PROVEEDOR}' y/o '{LOCATION_DESCARTES}'. "
                "Revisá Ubicaciones.",
                "error",
            )
            return redirect(url_for("admin_adjust_stock"))

        # Para mantener coherencia del modelo (from/to obligatorios) registramos el ajuste como movimiento:
        # - SUMAR: Proveedor -> ubicación (Proveedor es externa: no se descuenta stock allí)
        # - RESTAR: ubicación -> Baja/Descarte (Baja es externa: sí acumulamos para ver descartes)
        try:
            if action == "SUMAR":
                from_id = proveedor.id
                to_id = location_id

                # Solo incrementamos destino (origen externo no se descuenta)
                upsert_stock(item_id, to_id, qty)

                obs = f"AJUSTE +{qty}: {reason}"
            else:  # RESTAR
                from_id = location_id
                to_id = baja.id

                upsert_stock(item_id, from_id, -qty)
                upsert_stock(item_id, to_id, qty)

                obs = f"AJUSTE -{qty}: {reason}"

            db.session.add(Movement(
                item_id=item_id,
                qty=qty,
                from_location_id=from_id,
                to_location_id=to_id,
                user_id=current_user.id,
                observation=obs
            ))
            db.session.commit()
            flash("Ajuste registrado.", "ok")
        except Exception as e:
            db.session.rollback()
            flash(f"No se pudo ajustar: {e}", "error")

        return redirect(url_for("admin_adjust_stock"))

    return render_template(
        "admin_adjust_stock.html",
        items=items_list,
        locations=locations_list,
        proveedor=proveedor,
        baja=baja,
    )


# ---- CONTEO CICLICO (stocktake) ----
# Ubicaciones "de sistema" que NO se cuentan fisicamente (externas / destinos
# logicos como Proveedor, Descartes, Utilizado, En reparacion, Recuperado).
CONTEO_EXCLUDE_LOCATIONS = {
    LOCATION_PROVEEDOR,
    LOCATION_DESCARTES,
    "Utilizado",
    LOCATION_EN_REPARACION,
    LOCATION_RECUPERADO,
}


def _conteo_countable_locations():
    """Ubicaciones fisicas contables: no externas y que no sean de sistema."""
    return [
        l for l in Location.query.order_by(Location.name).all()
        if not l.is_external and l.name not in CONTEO_EXCLUDE_LOCATIONS
    ]


def _conteo_is_valid_location(location) -> bool:
    return (
        location is not None
        and not location.is_external
        and location.name not in CONTEO_EXCLUDE_LOCATIONS
    )


@app.route("/conteo", methods=["GET", "POST"])
@login_required
@role_required("ADMIN", "SUPERVISOR")
def conteo():
    """Conteo ciclico: comparar stock fisico vs sistema por ubicacion y registrar
    las diferencias como AJUSTES trazables (Movement), reusando el mismo criterio
    que Ajustar stock. NO borra ni edita histórico; NO toca el esquema."""
    locations = _conteo_countable_locations()

    if request.method == "POST":
        action = request.form.get("action", "").strip()
        loc_raw = request.form.get("location_id", "").strip()
        motivo = (request.form.get("motivo", "") or "").strip()

        if not loc_raw.isdigit():
            flash("Elegí una ubicación válida.", "error")
            return redirect(url_for("conteo"))
        location = Location.query.get(int(loc_raw))
        if not _conteo_is_valid_location(location):
            flash("Ubicación no válida para conteo.", "error")
            return redirect(url_for("conteo"))
        if not motivo:
            flash("El motivo del conteo es obligatorio.", "error")
            return redirect(url_for("conteo", location_id=location.id))

        # Filas de stock actuales de la ubicacion (solo items activos).
        stock_rows = (
            db.session.query(Stock, Item)
            .join(Item, Item.id == Stock.item_id)
            .filter(Stock.location_id == location.id, Item.is_active == True)
            .order_by(Item.code)
            .all()
        )

        # Diferencias: para cada item se lee "contado_<id>". Vacio/invalido => no se toca.
        diffs = []
        for stock, item in stock_rows:
            raw = request.form.get(f"contado_{item.id}", "").strip()
            if raw == "":
                continue
            try:
                contado = int(raw)
                if contado < 0:
                    raise ValueError()
            except Exception:
                continue
            if contado != stock.quantity:
                diffs.append({
                    "item": item,
                    "sistema": stock.quantity,
                    "contado": contado,
                    "delta": contado - stock.quantity,
                })

        if action == "preview":
            if not diffs:
                flash("No hay diferencias entre el conteo y el sistema.", "ok")
                return redirect(url_for("conteo", location_id=location.id))
            return render_template(
                "conteo.html",
                locations=locations,
                location=location,
                step="preview",
                diffs=diffs,
                motivo=motivo,
                stock_rows=None,
            )

        if action == "apply":
            proveedor = Location.query.filter_by(name=LOCATION_PROVEEDOR).first()
            baja = Location.query.filter_by(name=LOCATION_DESCARTES).first()
            if proveedor is None or baja is None:
                flash(
                    f"Faltan ubicaciones requeridas: '{LOCATION_PROVEEDOR}' y/o "
                    f"'{LOCATION_DESCARTES}'. Revisá Ubicaciones.",
                    "error",
                )
                return redirect(url_for("conteo", location_id=location.id))

            aplicados = 0
            serial_faltantes = []
            try:
                for d in diffs:
                    item = d["item"]
                    # Recalcular sistema REAL al momento de aplicar (no confiar en el preview).
                    row = Stock.query.filter_by(item_id=item.id, location_id=location.id).first()
                    sistema = row.quantity if row else 0
                    contado = d["contado"]
                    delta = contado - sistema
                    if delta == 0:
                        continue

                    # Serializado con faltante: no se ajusta a ciegas (hay que elegir
                    # qué serial falta). Se omite y se avisa; el sobrante sí se aplica
                    # (agrega cupo para etiquetar seriales después).
                    if item.serialized and delta < 0:
                        serial_faltantes.append(f"{item.code} {item.name}")
                        continue

                    if delta > 0:
                        # Sobrante: ingresa desde Proveedor (externa) -> ubicacion.
                        upsert_stock(item.id, location.id, delta)
                        from_id, to_id = proveedor.id, location.id
                    else:
                        # Faltante: sale de la ubicacion -> Descartes.
                        upsert_stock(item.id, location.id, delta)  # delta < 0
                        upsert_stock(item.id, baja.id, -delta)
                        from_id, to_id = location.id, baja.id

                    y, seq, number = next_movement_number()
                    db.session.add(Movement(
                        item_id=item.id,
                        qty=abs(delta),
                        from_location_id=from_id,
                        to_location_id=to_id,
                        user_id=current_user.id,
                        observation=f"CONTEO {location.name}: sistema {sistema} -> contado {contado} | {motivo}",
                        year=y, seq=seq, number=number,
                    ))
                    db.session.flush()  # para que next_movement_number vea el seq recien creado
                    aplicados += 1

                db.session.commit()
                flash(f"Conteo aplicado: {aplicados} ajuste(s) registrado(s) en {location.name}.", "ok")
                if serial_faltantes:
                    flash(
                        "Ítems serializados con faltante NO ajustados (elegí el serial "
                        "desde Movimientos/Descarte): " + "; ".join(serial_faltantes),
                        "error",
                    )
            except Exception as e:
                db.session.rollback()
                flash(f"No se pudo aplicar el conteo: {e}", "error")
            return redirect(url_for("conteo"))

        flash("Acción inválida.", "error")
        return redirect(url_for("conteo"))

    # ---- GET ----
    loc_raw = request.args.get("location_id", "").strip()
    location = None
    stock_rows = None
    if loc_raw.isdigit():
        location = Location.query.get(int(loc_raw))
        if not _conteo_is_valid_location(location):
            flash("Ubicación no válida para conteo.", "error")
            return redirect(url_for("conteo"))
        stock_rows = (
            db.session.query(Stock, Item)
            .join(Item, Item.id == Stock.item_id)
            .filter(Stock.location_id == location.id, Item.is_active == True)
            .order_by(Item.code)
            .all()
        )

    return render_template(
        "conteo.html",
        locations=locations,
        location=location,
        step="count" if location else "choose",
        stock_rows=stock_rows,
        diffs=None,
        motivo="",
    )


# ---- UBICACIONES ----

@app.route("/locations", methods=["GET", "POST"])
@login_required
@role_required("ADMIN", "SUPERVISOR")
def locations():
    users_list = User.query.order_by(User.username).all()

    if request.method == "POST":
        # Crear ubicacion: solo ADMIN (SUPERVISOR ve el listado en modo lectura).
        if current_user.role != "ADMIN":
            flash("Solo ADMIN puede crear ubicaciones.", "error")
            return redirect(url_for("locations"))
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip() or None
        # Solo ADMIN puede marcar una ubicación como externa (Proveedor/Baja).
        is_external = (
            current_user.role == "ADMIN"
            and request.form.get("is_external") == "on"
        )

        if not name:
            flash("Nombre requerido", "error")
            return redirect(url_for("locations"))
        if _name_taken(Location, name):
            flash("Esa ubicación ya existe", "error")
            return redirect(url_for("locations"))

        loc = Location(name=name, description=description, is_external=is_external)
        db.session.add(loc)
        db.session.flush()
        for uid in request.form.getlist("responsible_user_ids"):
            if uid.isdigit():
                db.session.add(LocationResponsible(location_id=loc.id, user_id=int(uid)))
        db.session.commit()
        flash("Ubicación creada", "ok")
        return redirect(url_for("locations"))

    sort_by = request.args.get("sort_by", "name").strip()
    sort_dir = request.args.get("sort_dir", "asc").strip().lower()
    loc_sortable = {
        "id": Location.id,
        "name": Location.name,
        "description": Location.description,
        "is_external": Location.is_external,
        "is_truck": Location.is_truck,
    }
    loc_col = loc_sortable.get(sort_by, Location.name)
    loc_q = Location.query.order_by(loc_col.desc() if sort_dir == "desc" else loc_col.asc())

    return render_template(
        "locations.html",
        locations=loc_q.all(),
        users=users_list,
        selected_sort_by=sort_by,
        selected_sort_dir=sort_dir,
    )


# ---- USUARIOS ----

@app.route("/users", methods=["GET", "POST"])
@login_required
@role_required("ADMIN")
def users():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        full_name = request.form.get("full_name", "").strip()
        role = request.form.get("role", "").strip()
        password = request.form.get("password", "").strip()
        email = request.form.get("email", "").strip() or None

        if not username:
            flash("Username requerido", "error")
            return redirect(url_for("users"))
        if not full_name:
            flash("Nombre completo requerido", "error")
            return redirect(url_for("users"))
        if role not in ROLE_CHOICES:
            flash("Rol inválido (tenés que elegir uno real)", "error")
            return redirect(url_for("users"))
        # Guarda: un SUPERVISOR no puede crear usuarios ADMIN ni SUPERVISOR.
        if role not in assignable_roles_for_current():
            flash("No podés asignar ese rol.", "error")
            return redirect(url_for("users"))
        if not password or len(password) < MIN_PASSWORD_LEN:
            flash(f"Contraseña requerida (mínimo {MIN_PASSWORD_LEN} caracteres)", "error")
            return redirect(url_for("users"))
        if username_taken(username):
            flash("Ese username ya existe", "error")
            return redirect(url_for("users"))

        u = User(username=username, full_name=full_name, role=role, email=email)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        flash("Usuario creado", "ok")
        return redirect(url_for("users"))

    q_text = request.args.get("q", "").strip()
    sort_by = request.args.get("sort_by", "username").strip()
    sort_dir = request.args.get("sort_dir", "asc").strip().lower()

    uq = User.query
    if q_text:
        like = f"%{q_text}%"
        uq = uq.filter(db.or_(
            User.username.ilike(like),
            User.full_name.ilike(like),
            User.role.ilike(like),
        ))

    user_sortable = {
        "id": User.id,
        "username": User.username,
        "full_name": User.full_name,
        "role": User.role,
    }
    user_col = user_sortable.get(sort_by, User.username)
    uq = uq.order_by(user_col.desc() if sort_dir == "desc" else user_col.asc())

    users_list = uq.all()
    manageable_ids = {u.id for u in users_list if can_manage_target(u)}

    return render_template(
        "users.html",
        users=users_list,
        roles=assignable_roles_for_current(),
        manageable_ids=manageable_ids,
        can_see_passwords=(current_user.role == "ADMIN"),
        q=q_text,
        selected_sort_by=sort_by,
        selected_sort_dir=sort_dir,
    )


# ---- CATEGORÍAS ----

@app.route("/categories", methods=["GET", "POST"])
@login_required
@role_required("ADMIN", "SUPERVISOR")
def categories():
    if request.method == "POST":
        # Crear categoria: solo ADMIN (SUPERVISOR ve el listado en modo lectura).
        if current_user.role != "ADMIN":
            flash("Solo ADMIN puede crear categorías.", "error")
            return redirect(url_for("categories"))
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip() or None
        prefix = normalize_prefix(request.form.get("prefix", ""))

        if not name:
            flash("Nombre requerido", "error")
            return redirect(url_for("categories"))
        if _name_taken(Category, name):
            flash("Esa categoría ya existe", "error")
            return redirect(url_for("categories"))
        if not re.fullmatch(r"[A-Z]{3}", prefix):
            flash("El prefijo debe ser exactamente 3 letras (A-Z), por ejemplo CAB", "error")
            return redirect(url_for("categories"))
        if prefix_taken(prefix):
            flash("Ese prefijo ya lo usa otra categoría", "error")
            return redirect(url_for("categories"))

        db.session.add(Category(name=name, description=description, prefix=prefix))
        db.session.commit()
        flash("Categoría creada", "ok")
        return redirect(url_for("categories"))

    sort_by = request.args.get("sort_by", "name").strip()
    sort_dir = request.args.get("sort_dir", "asc").strip().lower()
    cat_sortable = {"id": Category.id, "name": Category.name, "description": Category.description}
    cat_col = cat_sortable.get(sort_by, Category.name)
    cat_q = Category.query.order_by(cat_col.desc() if sort_dir == "desc" else cat_col.asc())
    # Categorías que ya tienen items: su prefijo queda bloqueado en edición.
    locked_prefix_ids = {
        cid for (cid,) in db.session.query(Item.category_id).distinct().all()
        if cid is not None
    }
    return render_template(
        "categories.html",
        categories=cat_q.all(),
        locked_prefix_ids=locked_prefix_ids,
        selected_sort_by=sort_by,
        selected_sort_dir=sort_dir,
    )


@app.route("/categories/<int:cat_id>/edit", methods=["POST"])
@login_required
@role_required("ADMIN", "SUPERVISOR")
def category_edit(cat_id):
    if current_user.role != "ADMIN":
        flash("Solo ADMIN puede editar categorías.", "error")
        return redirect(url_for("categories"))

    cat = Category.query.get(cat_id)
    if not cat:
        flash("Categoría no encontrada.", "error")
        return redirect(url_for("categories"))

    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip() or None
    new_prefix = normalize_prefix(request.form.get("prefix", ""))

    if not name:
        flash("Nombre requerido", "error")
        return redirect(url_for("categories"))
    if _name_taken(Category, name, exclude_id=cat.id):
        flash("Ya existe otra categoría con ese nombre", "error")
        return redirect(url_for("categories"))

    has_items = Item.query.filter_by(category_id=cat.id).count() > 0
    if has_items:
        # Con items existentes, el prefijo queda fijo para no romper códigos.
        if new_prefix and new_prefix != normalize_prefix(cat.prefix or ""):
            flash("No se puede cambiar el prefijo: la categoría ya tiene items.", "error")
            return redirect(url_for("categories"))
    else:
        if not re.fullmatch(r"[A-Z]{3}", new_prefix):
            flash("El prefijo debe ser exactamente 3 letras (A-Z), por ejemplo CAB", "error")
            return redirect(url_for("categories"))
        if prefix_taken(new_prefix, exclude_id=cat.id):
            flash("Ese prefijo ya lo usa otra categoría", "error")
            return redirect(url_for("categories"))
        cat.prefix = new_prefix

    cat.name = name
    cat.description = description
    db.session.commit()
    flash("Categoría actualizada", "ok")
    return redirect(url_for("categories"))

# ---- ITEMS (con filtros) ----

@app.route("/items")
@login_required
@role_required("ADMIN", "SUPERVISOR", "LECTOR")
def items():
    cat_id = request.args.get("category_id", "").strip()
    trackable = request.args.get("trackable", "").strip()
    q_text = request.args.get("q", "").strip()
    item_filter = request.args.get("item_id", "").strip()

    sort_by = request.args.get("sort_by", "code").strip()
    sort_dir = request.args.get("sort_dir", "asc").strip().lower()

    q = Item.query.join(Category)

    if cat_id.isdigit():
        q = q.filter(Item.category_id == int(cat_id))

    if trackable in ("0", "1"):
        q = q.filter(Item.trackable == (trackable == "1"))

    # Buscador tipo Movimientos: si se elige un item puntual, se filtra por ese.
    if item_filter.isdigit():
        q = q.filter(Item.id == int(item_filter))

    if q_text:
        like = f"%{q_text}%"
        q = q.filter(
            db.or_(
                Item.code.ilike(like),
                Item.name.ilike(like),
                Item.description.ilike(like),
                Category.name.ilike(like)
            )
        )

    sortable_columns = {
        "code": Item.code,
        "name": Item.name,
        "category": Category.name,
        "trackable": Item.trackable,
        "stock_min": Item.stock_min,
        "description": Item.description,
        "is_active": Item.is_active,
    }

    sort_column = sortable_columns.get(sort_by, Item.code)

    if sort_dir == "desc":
        q = q.order_by(sort_column.desc())
    else:
        q = q.order_by(sort_column.asc())

    items_list = q.all()
    categories_list = Category.query.order_by(Category.name).all()
    all_items = Item.query.order_by(Item.code).all()

    # Próximo código por categoría, para autocompletar el preview en el alta.
    next_codes = {}
    for c in categories_list:
        p = prefix_for_category(c)
        next_codes[c.id] = next_item_code(p) if p else ""

    return render_template(
        "items.html",
        items=items_list,
        categories=categories_list,
        all_items=all_items,
        next_codes=next_codes,
        selected_category_id=cat_id,
        selected_trackable=trackable,
        selected_q=q_text,
        selected_item_id=item_filter,
        selected_sort_by=sort_by,
        selected_sort_dir=sort_dir,
    )


@app.route("/items/new", methods=["GET", "POST"])
@login_required
@role_required("ADMIN")
def item_new():
    cats = Category.query.order_by(Category.name).all()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip() or None
        category_id = request.form.get("category_id", "").strip()
        trackable = True if request.form.get("trackable") == "on" else False
        serialized = True if request.form.get("serialized") == "on" else False
        # Serializado y rastreable (único, máx 1) son excluyentes: si es serializado
        # se lleva por unidades y no aplica la regla de "1 en todo el sistema".
        if serialized:
            trackable = False
        stock_min_raw = request.form.get("stock_min", "").strip()
        reference_link = (request.form.get("reference_link", "") or "").strip()[:500] or None
        unit = request.form.get("unit", "unidad").strip()
        if unit not in ("unidad", "metros"):
            unit = "unidad"

        # Si el alta viene del modal (fetch AJAX), respondemos JSON para poder
        # mostrar el error dentro del popup sin recargar ni perder lo cargado.
        wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        def _fail(msg):
            if wants_json:
                return jsonify(ok=False, error=msg), 400
            flash(msg, "error")
            return redirect(url_for("item_new"))

        # 1) Categoría: primero y obligatoria
        if not category_id.isdigit():
            return _fail("Tenés que elegir una categoría")
        category = Category.query.get(int(category_id))
        if not category:
            return _fail("Categoría inexistente")

        # 2) Nombre obligatorio y único global (sin importar mayúsculas/acentos)
        if not name:
            return _fail("El nombre es obligatorio")
        if item_name_exists(name):
            return _fail("Ya existe un item con ese nombre")

        # 3) Prefijo de código de la categoría (el código se genera solo)
        prefix = prefix_for_category(category)
        if not prefix:
            return _fail(
                f"La categoría «{category.name}» no tiene prefijo de código asignado. "
                "Un ADMIN debe asignarlo antes de crear items de esta categoría."
            )

        # 4) Stock de seguridad: solo para NO rastreables
        stock_min = 0
        if not trackable and stock_min_raw:
            try:
                stock_min = int(stock_min_raw)
                if stock_min < 0:
                    raise ValueError()
            except Exception:
                return _fail("Stock de seguridad inválido (debe ser 0 o mayor)")

        # 5) Generar código correlativo y guardar. Reintenta ante choque de código
        #    (dos altas simultáneas en la misma categoría).
        for _ in range(5):
            code = next_item_code(prefix)
            db.session.add(Item(
                code=code,
                name=name,
                description=description,
                category_id=category.id,
                trackable=trackable,
                serialized=serialized,
                stock_min=stock_min,
                reference_link=reference_link,
                unit=unit,
            ))
            try:
                db.session.commit()
                flash(f"Item creado con código {code}", "ok")
                if wants_json:
                    return jsonify(ok=True, redirect=url_for("items"))
                return redirect(url_for("items"))
            except IntegrityError:
                db.session.rollback()
                continue

        return _fail("No se pudo generar un código único. Probá de nuevo.")

    # Próximo código por categoría, para autocompletar el preview al elegir.
    next_codes = {}
    for c in cats:
        p = prefix_for_category(c)
        next_codes[c.id] = next_item_code(p) if p else ""
    return render_template("item_new.html", categories=cats, next_codes=next_codes)


# ---- ADMIN: EDITAR / BAJA LÓGICA ITEMS ----

@app.route("/items/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("ADMIN")
def item_edit(item_id: int):
    it = Item.query.get_or_404(item_id)
    cats = Category.query.order_by(Category.name).all()

    if request.method == "POST":
        # El código NO se edita: es automático y fijo. Se ignora cualquier valor enviado.
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip() or None
        category_id = request.form.get("category_id", "").strip()
        trackable = True if request.form.get("trackable") == "on" else False
        serialized = True if request.form.get("serialized") == "on" else False
        if serialized:
            trackable = False
        stock_min_raw = request.form.get("stock_min", "").strip()
        is_active = True if request.form.get("is_active") == "on" else False
        reference_link = (request.form.get("reference_link", "") or "").strip()[:500] or None
        unit = request.form.get("unit", "unidad").strip()
        if unit not in ("unidad", "metros"):
            unit = "unidad"

        stock_min = 0
        if not trackable:
            # si no viene, dejamos el actual
            if stock_min_raw == "":
                stock_min = it.stock_min or 0
            else:
                try:
                    stock_min = int(stock_min_raw)
                    if stock_min < 0:
                        raise ValueError()
                except Exception:
                    flash("Stock de seguridad inválido (debe ser 0 o mayor)", "error")
                    return redirect(url_for("item_edit", item_id=it.id))

        if not name:
            flash("El nombre es obligatorio", "error")
            return redirect(url_for("item_edit", item_id=it.id))

        # Unicidad por nombre (global entre items activos, sin importar mayúsculas/acentos)
        if item_name_exists(name, exclude_id=it.id):
            flash("Ya existe otro item con ese nombre", "error")
            return redirect(url_for("item_edit", item_id=it.id))

        # it.code y it.category_id se mantienen fijos: la categoría no se edita
        # (el código encodea la categoría original y el historial depende de eso).
        it.name = name
        it.description = description
        it.trackable = trackable
        it.serialized = serialized
        # si se marca rastreable, el stock mínimo se ignora
        it.stock_min = 0 if trackable else stock_min
        it.is_active = is_active
        it.reference_link = reference_link  # None cuando queda vacío
        it.unit = unit

        try:
            db.session.commit()
            flash("Item actualizado", "ok")
        except Exception as e:
            db.session.rollback()
            flash(f"No se pudo actualizar: {e}", "error")

        return redirect(url_for("items"))

    return render_template("edit_item.html", it=it, categories=cats)


@app.route("/items/<int:item_id>/delete", methods=["POST"])
@login_required
@role_required("ADMIN")
def item_delete(item_id: int):
    """Borrado real de un ítem. Solo ADMIN y solo si NO tiene historial
    (movimientos, pendientes o stock con cantidad). Si tiene historial, no se
    borra: hay que usar la baja lógica (desactivar) para no romper trazabilidad."""
    if current_user.role != "ADMIN":
        flash("Solo ADMIN puede eliminar ítems.", "error")
        return redirect(url_for("items"))

    it = Item.query.get_or_404(item_id)

    n_mov = Movement.query.filter_by(item_id=it.id).count()
    n_pend = PendingDelivery.query.filter_by(item_id=it.id).count()
    stock_rows = Stock.query.filter_by(item_id=it.id).all()
    qty_total = sum((s.quantity or 0) for s in stock_rows)

    if n_mov or n_pend or qty_total:
        flash(
            "No se puede eliminar: el ítem tiene historial (movimientos, pendientes "
            "o stock). Usá la baja lógica (desactivar) para conservar la trazabilidad.",
            "error",
        )
        return redirect(url_for("items"))

    code = it.code
    try:
        # Filas de stock en 0 (sin historial) se limpian junto con el ítem.
        for s in stock_rows:
            db.session.delete(s)
        db.session.delete(it)
        db.session.commit()
        flash(f"Ítem {code} eliminado correctamente.", "ok")
    except Exception as e:
        db.session.rollback()
        flash(f"No se pudo eliminar: {e}", "error")

    return redirect(url_for("items"))


# ---- SERIALES / UNIDADES DE ÍTEM ----

UNIT_VIEW_ROLES = ("ADMIN", "SUPERVISOR", "LECTOR")


def _units_redirect(item_id: int):
    """Redirige a la ficha de seriales, preservando el modo embed (popup)."""
    if request.values.get("embed") == "1":
        return redirect(url_for("item_units", item_id=item_id, embed="1"))
    return redirect(url_for("item_units", item_id=item_id))


def _units_context(it: "Item"):
    """Datos para la ficha de seriales: unidades por estado y cupo por ubicación."""
    units = ItemUnit.query.filter_by(item_id=it.id).order_by(ItemUnit.status, ItemUnit.serial).all()
    en_stock = [u for u in units if u.status == UNIT_EN_STOCK]
    fuera = [u for u in units if u.status != UNIT_EN_STOCK]

    # Cupo para etiquetar seriales por ubicación interna: no se pueden registrar
    # más unidades EN_STOCK que la cantidad de stock agregado en esa ubicación.
    labeled_by_loc = {}
    for u in en_stock:
        labeled_by_loc[u.location_id] = labeled_by_loc.get(u.location_id, 0) + 1

    loc_rooms = []
    for loc in Location.query.order_by(Location.name).all():
        if loc.is_external:
            continue
        st = Stock.query.filter_by(item_id=it.id, location_id=loc.id).first()
        qty = (st.quantity if st else 0) or 0
        # Solo ubicaciones que TIENEN stock de este ítem: no tiene sentido
        # etiquetar un serial donde el ítem no está.
        if qty <= 0:
            continue
        labeled = labeled_by_loc.get(loc.id, 0)
        room = qty - labeled
        loc_rooms.append({"loc": loc, "qty": qty, "labeled": labeled, "room": room})

    return {"units": units, "en_stock": en_stock, "fuera": fuera, "loc_rooms": loc_rooms}


@app.route("/items/<int:item_id>/units", methods=["GET"])
@login_required
@role_required("ADMIN", "SUPERVISOR", "LECTOR", "TECNICO")
def item_units(item_id: int):
    it = Item.query.get_or_404(item_id)
    ctx = _units_context(it)
    embed = request.args.get("embed") == "1"

    # Filtro opcional por ubicación (ej. abrir desde Stock sobre una camioneta).
    focus_loc = None
    loc_raw = request.args.get("loc", "").strip()
    if loc_raw.isdigit():
        focus_loc = Location.query.get(int(loc_raw))
        # TECNICO: solo puede enfocar ubicaciones de las que es responsable.
        if current_user.role == "TECNICO":
            allowed = {l.id for l in Location.query.join(LocationResponsible).filter(
                LocationResponsible.user_id == current_user.id).all()}
            if focus_loc and focus_loc.id not in allowed:
                focus_loc = None

    return render_template("item_units.html", it=it, embed=embed, focus_loc=focus_loc, **ctx)


@app.route("/items/<int:item_id>/units/new", methods=["POST"])
@login_required
@role_required("ADMIN")
def item_unit_new(item_id: int):
    """Registra un serial de stock EXISTENTE en una ubicación (onboarding/etiquetado).

    No mueve stock: solo asocia un número de serie a stock que ya está en esa
    ubicación. Regla: no se pueden etiquetar más seriales EN_STOCK que la cantidad
    de stock agregado en esa ubicación (unidades_etiquetadas <= stock).
    """
    it = Item.query.get_or_404(item_id)
    if not it.serialized:
        flash("Ese ítem no es serializado. Activá 'Serializado' en la ficha del ítem.", "error")
        return _units_redirect(it.id)

    serial = (request.form.get("serial", "") or "").strip()[:120]
    location_id_raw = (request.form.get("location_id", "") or "").strip()
    notes = (request.form.get("notes", "") or "").strip()[:255] or None

    if not serial:
        flash("El número de serie es obligatorio.", "error")
        return _units_redirect(it.id)
    if not location_id_raw.isdigit():
        flash("Elegí la ubicación donde está la unidad.", "error")
        return _units_redirect(it.id)
    location_id = int(location_id_raw)

    loc = Location.query.get(location_id)
    if not loc or loc.is_external:
        flash("Ubicación inválida para registrar un serial.", "error")
        return _units_redirect(it.id)

    # Serial único dentro del ítem (case-insensitive).
    dup = ItemUnit.query.filter(
        ItemUnit.item_id == it.id,
        func.lower(ItemUnit.serial) == serial.lower(),
    ).first()
    if dup:
        flash(f"El serial «{serial}» ya está cargado para este ítem.", "error")
        return _units_redirect(it.id)

    st = Stock.query.filter_by(item_id=it.id, location_id=location_id).first()
    qty = (st.quantity if st else 0) or 0
    labeled = count_units_in_stock(it.id, location_id)
    if labeled >= qty:
        flash(
            f"No hay cupo en «{loc.name}»: hay {qty} en stock y ya {labeled} con serial. "
            "Ingresá stock desde Movimientos antes de etiquetar más seriales.",
            "error",
        )
        return _units_redirect(it.id)

    try:
        db.session.add(ItemUnit(
            item_id=it.id, serial=serial, status=UNIT_EN_STOCK,
            location_id=location_id, notes=notes,
        ))
        db.session.commit()
        flash(f"Serial «{serial}» registrado en {loc.name}.", "ok")
    except Exception as e:
        db.session.rollback()
        flash(f"No se pudo registrar el serial: {e}", "error")
    return _units_redirect(it.id)


@app.route("/units/<int:unit_id>/edit", methods=["POST"])
@login_required
@role_required("ADMIN")
def item_unit_edit(unit_id: int):
    """Corrige el número de serie o las notas de una unidad. No mueve stock."""
    u = ItemUnit.query.get_or_404(unit_id)
    serial = (request.form.get("serial", "") or "").strip()[:120]
    notes = (request.form.get("notes", "") or "").strip()[:255] or None

    if not serial:
        flash("El número de serie es obligatorio.", "error")
        return _units_redirect(u.item_id)

    dup = ItemUnit.query.filter(
        ItemUnit.item_id == u.item_id,
        func.lower(ItemUnit.serial) == serial.lower(),
        ItemUnit.id != u.id,
    ).first()
    if dup:
        flash(f"Ya existe otro serial «{serial}» en este ítem.", "error")
        return _units_redirect(u.item_id)

    try:
        u.serial = serial
        u.notes = notes
        db.session.commit()
        flash("Serial actualizado.", "ok")
    except Exception as e:
        db.session.rollback()
        flash(f"No se pudo actualizar: {e}", "error")
    return _units_redirect(u.item_id)


@app.route("/units/<int:unit_id>/delete", methods=["POST"])
@login_required
@role_required("ADMIN")
def item_unit_delete(unit_id: int):
    """Quita la etiqueta de serial de una unidad EN_STOCK. No toca stock agregado.

    Solo se permite borrar unidades EN_STOCK (las que ya salieron conservan
    trazabilidad y no se borran)."""
    u = ItemUnit.query.get_or_404(unit_id)
    item_id = u.item_id
    if u.status != UNIT_EN_STOCK:
        flash("No se puede borrar un serial que ya salió del stock (histórico).", "error")
        return _units_redirect(item_id)
    try:
        serial = u.serial
        db.session.delete(u)
        db.session.commit()
        flash(f"Serial «{serial}» quitado.", "ok")
    except Exception as e:
        db.session.rollback()
        flash(f"No se pudo quitar el serial: {e}", "error")
    return _units_redirect(item_id)


# ---- STOCK (con filtros) ----

@app.route("/stock")
@login_required
def stock():
    category_id = request.args.get("category_id", "").strip()
    trackable = request.args.get("trackable", "").strip()
    q_text = request.args.get("q", "").strip()

    is_tecnico = current_user.role == "TECNICO"

    if is_tecnico:
        tech_locations = Location.query.join(LocationResponsible).filter(
            LocationResponsible.user_id == current_user.id,
        ).all()
        tech_ids = [l.id for l in tech_locations]
        loc_id = request.args.get("location_id", "").strip()
    else:
        tech_locations = []
        tech_ids = []
        loc_id = request.args.get("location_id", "").strip()

    q = Stock.query.join(Item).join(Location).join(Category).filter(Item.is_active == True)

    if is_tecnico:
        if tech_ids:
            if loc_id.isdigit() and int(loc_id) in tech_ids:
                q = q.filter(Stock.location_id == int(loc_id))
            else:
                q = q.filter(Stock.location_id.in_(tech_ids))
        else:
            q = q.filter(Stock.location_id == -1)
    elif loc_id.isdigit():
        q = q.filter(Stock.location_id == int(loc_id))

    if category_id.isdigit():
        q = q.filter(Item.category_id == int(category_id))

    if trackable in ("0", "1"):
        q = q.filter(Item.trackable == (trackable == "1"))

    if q_text:
        like = f"%{q_text}%"
        q = q.filter(
            db.or_(
                Item.code.ilike(like),
                Item.name.ilike(like),
                Item.description.ilike(like)
            )
        )

    rows = q.order_by(Location.name, Item.code).all()

    locations_list = tech_locations if is_tecnico else Location.query.order_by(Location.name).all()
    if is_tecnico:
        # TECNICO: solo categorias presentes en su stock (>0)
        if tech_ids:
            categories_list = (
                Category.query
                .join(Item, Item.category_id == Category.id)
                .join(Stock, Stock.item_id == Item.id)
                .filter(
                    Item.is_active == True,
                    Stock.location_id.in_(tech_ids),
                    Stock.quantity > 0,
                )
                .distinct()
                .order_by(Category.name)
                .all()
            )
        else:
            categories_list = []
    else:
        categories_list = Category.query.order_by(Category.name).all()

    items_list = Item.query.filter(Item.is_active == True).order_by(Item.code).all()

    return render_template(
        "stock.html",
        stock_rows=rows,
        locations=locations_list,
        categories=categories_list,
        items=items_list,
        selected_location_id=loc_id,
        selected_category_id=category_id,
        selected_trackable=trackable,
        selected_q=q_text,
        is_tecnico=is_tecnico,
    )


@app.route("/stock/export.csv", methods=["GET"])
@login_required
def stock_export_csv():
    loc_id = request.args.get("location_id", "").strip()
    category_id = request.args.get("category_id", "").strip()
    trackable = request.args.get("trackable", "").strip()
    q_text = request.args.get("q", "").strip()

    is_tecnico = current_user.role == "TECNICO"

    q = Stock.query.join(Item).join(Location).join(Category).filter(Item.is_active == True)

    # Mismo scope por ubicacion que la pantalla /stock: el TECNICO solo ve
    # el stock de las ubicaciones de las que es responsable.
    if is_tecnico:
        tech_ids = [
            l.id for l in Location.query.join(LocationResponsible).filter(
                LocationResponsible.user_id == current_user.id,
            ).all()
        ]
        if tech_ids:
            if loc_id.isdigit() and int(loc_id) in tech_ids:
                q = q.filter(Stock.location_id == int(loc_id))
            else:
                q = q.filter(Stock.location_id.in_(tech_ids))
        else:
            q = q.filter(Stock.location_id == -1)
    elif loc_id.isdigit():
        q = q.filter(Stock.location_id == int(loc_id))

    if category_id.isdigit():
        q = q.filter(Item.category_id == int(category_id))

    if trackable in ("0", "1"):
        q = q.filter(Item.trackable == (trackable == "1"))

    if q_text:
        like = f"%{q_text}%"
        q = q.filter(
            db.or_(
                Item.code.ilike(like),
                Item.name.ilike(like),
                Item.description.ilike(like)
            )
        )

    rows = q.order_by(Location.name, Item.code).all()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "ubicacion",
        "codigo_item",
        "nombre_item",
        "categoria",
        "rastreable",
        "descripcion",
        "cantidad"
    ])

    for r in rows:
        writer.writerow([
            r.location.name,
            r.item.code,
            r.item.name,
            r.item.category.name,
            "Si" if r.item.trackable else "No",
            r.item.description or "",
            r.quantity
        ])

    csv_data = output.getvalue()
    output.close()

    filename = f"stock_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

# ---- MOVEMENTS (obligatorios from/to, responsable = current_user) ----

@app.route("/movements", methods=["GET", "POST"])
@login_required
@role_required("ADMIN", "SUPERVISOR", "TECNICO", "LECTOR")
def movements():
    is_tecnico = current_user.role == "TECNICO"
    tech_locations = []
    tech_location_ids = set()
    if is_tecnico:
        tech_locations = Location.query.join(LocationResponsible).filter(
            LocationResponsible.user_id == current_user.id
        ).all()
        tech_location_ids = {l.id for l in tech_locations}
        locations_list = Location.query.filter_by(is_truck=True).order_by(Location.name).all()
        # TECNICO: solo items que tiene en stock (>0) en su(s) ubicacion(es)
        if tech_location_ids:
            items_list = (
                Item.query.filter_by(is_active=True)
                .join(Stock, Stock.item_id == Item.id)
                .filter(Stock.location_id.in_(tech_location_ids), Stock.quantity > 0)
                .distinct()
                .order_by(Item.code)
                .all()
            )
        else:
            items_list = []
    else:
        # Proveedor se saca del desplegable de Movimientos: los ingresos/egresos
        # hacia/desde Proveedor se cargan en la sección Ingresos/Egresos.
        locations_list = (
            Location.query.filter(Location.name != LOCATION_PROVEEDOR)
            .order_by(Location.name).all()
        )
        items_list = Item.query.filter_by(is_active=True).order_by(Item.code).all()

    _descartes = Location.query.filter_by(name="Descartes").first()
    descartes_id = _descartes.id if _descartes else None

    if request.method == "POST":
        # LECTOR: solo lectura. No puede registrar movimientos.
        if current_user.role == "LECTOR":
            flash("No tenés permisos para registrar movimientos.", "error")
            return redirect(url_for("movements"))
        item_id = request.form.get("item_id", "").strip()
        qty_raw = request.form.get("qty", "").strip()
        from_id = request.form.get("from_location_id", "").strip()
        to_id = request.form.get("to_location_id", "").strip()
        observation = (request.form.get("observacion", "") or request.form.get("observation", "")).strip() or None

        generate_pending = request.form.get("generate_pending")
        pending_comment = request.form.get("pending_comment", "").strip() or None
        # Devolucion esperada (opcional). Si no vienen, se usa el mismo item y cantidad.
        pending_return_item_raw = request.form.get("pending_return_item_id", "").strip()
        pending_return_qty_raw = request.form.get("pending_return_qty", "").strip()

        # VALIDACIONES DURAS
        if not (item_id.isdigit() and from_id.isdigit() and to_id.isdigit()):
            flash("Item, Desde y Hacia son obligatorios.", "error")
            return redirect(url_for("movements"))

        try:
            qty = int(qty_raw)
            if qty <= 0:
                raise ValueError()
        except Exception:
            flash("Cantidad inválida.", "error")
            return redirect(url_for("movements"))

        from_id = int(from_id)
        to_id = int(to_id)
        item_id = int(item_id)

        # TECNICO solo puede mover desde sus ubicaciones asignadas
        if is_tecnico and from_id not in tech_location_ids:
            flash("Solo podés mover desde tus ubicaciones asignadas.", "error")
            return redirect(url_for("movements"))

        it = Item.query.get(item_id)
        if not it or not it.is_active:
            flash("El item seleccionado está dado de baja o no existe.", "error")
            return redirect(url_for("movements"))

        if from_id == to_id:
            flash("Desde y Hacia no pueden ser la misma ubicación.", "error")
            return redirect(url_for("movements"))

        # Si va a generar pendiente, la ubicación destino debe tener responsable
        to_location = Location.query.get(to_id)
        scrap_reason = request.form.get("scrap_reason", "").strip()
        is_to_descartes = to_location and to_location.name == "Descartes"

        if is_to_descartes and not scrap_reason:
            flash("Motivo de descarte obligatorio.", "error")
            return redirect(url_for("movements"))

        # --- ÍTEM SERIALIZADO: resolver qué seriales entran/salen ---
        # Para ítems serializados la cantidad la definen los seriales, no el campo
        # "qty" del form. Acá se valida y se arma el plan; los cambios en las filas
        # ItemUnit se aplican dentro del try junto al stock (misma transacción).
        from_ext = location_is_external(from_id)
        to_ext = location_is_external(to_id)
        serial_units_to_apply = []   # unidades existentes a mover (origen interno)
        serial_new_serials = []      # seriales nuevos a crear (ingreso desde externo)
        if it.serialized:
            if from_ext:
                # Ingreso: se cargan seriales nuevos.
                raw = request.form.getlist("unit_serial")
                serial_new_serials = [s.strip() for s in raw if s.strip()]
                if not serial_new_serials:
                    flash("Ítem serializado: ingresá al menos un número de serie.", "error")
                    return redirect(url_for("movements"))
                low = [s.lower() for s in serial_new_serials]
                if len(set(low)) != len(low):
                    flash("Hay números de serie repetidos en la carga.", "error")
                    return redirect(url_for("movements"))
                existing = {u.serial.lower() for u in ItemUnit.query.filter_by(item_id=item_id).all()}
                dup = [s for s in serial_new_serials if s.lower() in existing]
                if dup:
                    flash(f"Ya existe(n) el/los serial(es): {', '.join(dup)}", "error")
                    return redirect(url_for("movements"))
                qty = len(serial_new_serials)
            else:
                # Movimiento desde ubicación interna: regla auto/elegir (helper).
                serial_units_to_apply, err = resolve_serial_units_out(item_id, from_id, qty, request.form)
                if err:
                    flash(err, "error")
                    return redirect(url_for("movements"))

        pending_responsible_id = None

        if generate_pending:
            if not to_location:
                flash("La ubicación destino no existe.", "error")
                return redirect(url_for("movements"))

            first_resp = LocationResponsible.query.filter_by(location_id=to_id).first()
            if not first_resp:
                flash("La ubicación destino no tiene responsable asignado. Editá la ubicación antes de generar un pendiente.", "error")
                return redirect(url_for("movements"))

            pending_responsible_id = first_resp.user_id

        # Resolver item/cantidad de devolucion esperada (solo si hay pendiente).
        pending_return_item_id = None  # None = mismo item entregado
        pending_return_qty = None      # None = misma cantidad del movimiento
        if generate_pending:
            if pending_return_item_raw.isdigit() and int(pending_return_item_raw) != item_id:
                ret_it = Item.query.get(int(pending_return_item_raw))
                if not ret_it or not ret_it.is_active:
                    flash("El ítem a devolver no existe o está dado de baja.", "error")
                    return redirect(url_for("movements"))
                pending_return_item_id = ret_it.id
            if pending_return_qty_raw.isdigit():
                if int(pending_return_qty_raw) <= 0:
                    flash("La cantidad a devolver debe ser mayor a 0.", "error")
                    return redirect(url_for("movements"))
                if int(pending_return_qty_raw) > qty:
                    flash("La cantidad a devolver no puede superar la cantidad entregada.", "error")
                    return redirect(url_for("movements"))
                pending_return_qty = int(pending_return_qty_raw)

        try:
            if not from_ext:
                upsert_stock(item_id, from_id, -qty)
            if not to_ext:
                upsert_stock(item_id, to_id, qty)

            # Serializado: seriales a la observación (log y remito), sin tocar templates.
            obs_final = observation
            if it.serialized:
                moved_serials = (
                    serial_new_serials
                    if serial_new_serials
                    else [u.serial for u in serial_units_to_apply]
                )
                obs_final = serial_obs(observation, moved_serials)

            y, seq, number = next_movement_number()

            m = Movement(
                item_id=item_id,
                qty=qty,
                from_location_id=from_id,
                to_location_id=to_id,
                user_id=current_user.id,
                observation=obs_final,
                year=y,
                seq=seq,
                number=number,
            )
            db.session.add(m)
            db.session.flush()

            # Serializado: aplicar los cambios de estado/ubicación a cada unidad.
            if it.serialized:
                if serial_new_serials:
                    for s in serial_new_serials:
                        db.session.add(ItemUnit(
                            item_id=item_id,
                            serial=s,
                            status=UNIT_EN_STOCK,
                            location_id=(None if to_ext else to_id),
                        ))
                apply_serial_units_out(serial_units_to_apply, to_id)

            if generate_pending:
                # Un pendiente por unidad que debe volver (cada uno qty 1), asi
                # cada unidad se cierra por separado (una OK, otra a reparacion, etc.).
                # 'cantidad a devolver' = cuantos pendientes de 1 se generan (<= qty).
                n_pendientes = pending_return_qty if pending_return_qty else qty
                for _ in range(n_pendientes):
                    db.session.add(PendingDelivery(
                        movement_id=m.id,
                        responsible_from_id=current_user.id,
                        responsible_to_id=pending_responsible_id,
                        item_id=item_id,
                        return_item_id=pending_return_item_id,
                        return_qty=1,
                        comment=pending_comment,
                        returned=False,
                    ))

            if is_to_descartes:
                db.session.add(Scrap(
                    item_id=item_id,
                    location_id=from_id,
                    quantity=qty,
                    reason=scrap_reason,
                    user_id=current_user.id,
                ))

            db.session.commit()
            flash(f"Movimiento {number} registrado", "ok")

        except Exception as e:
            db.session.rollback()
            flash(f"No se pudo registrar: {e}", "error")

        return redirect(url_for("movements"))

    filters = _movements_filters_from_request()
    limit = filters["limit"]

    # Orden por columna (click en el header). Default: fecha descendente
    # (comportamiento historico). _build_movements_query ya hace join a Item y User.
    sort_by = (request.args.get("sort_by") or "date").strip()
    sort_dir = (request.args.get("sort_dir") or "desc").strip().lower()
    _mov_sortable = {
        "date": Movement.created_at,
        "qty": Movement.qty,
        "item": Item.code,
        "user": User.username,
    }
    _sort_col = _mov_sortable.get(sort_by, Movement.created_at)
    _order = _sort_col.desc() if sort_dir == "desc" else _sort_col.asc()

    logs_q = _build_movements_query(filters).order_by(_order)
    if is_tecnico and tech_location_ids:
        logs_q = logs_q.filter(Movement.from_location_id.in_(tech_location_ids))
    logs = logs_q.limit(limit).all()

    users_list = User.query.order_by(User.username).all()

    # Mapa ubicacion -> items con stock (>0), para filtrar el selector "Item" segun "Desde"
    stock_map = {}
    stock_qty_map = {}  # {loc_id(str): {item_id(str): qty}} para el tope de cantidad
    for s in Stock.query.filter(Stock.quantity > 0).all():
        stock_map.setdefault(s.location_id, []).append(s.item_id)
        stock_qty_map.setdefault(str(s.location_id), {})[str(s.item_id)] = s.quantity

    # Ubicaciones externas (Proveedor/Baja): no tienen stock propio, por eso el
    # selector "Item" debe ofrecer TODOS los items al elegirlas como origen.
    external_location_ids = [l.id for l in locations_list if l.is_external]

    # Seriales disponibles (EN_STOCK) por ítem y ubicación, para el selector del
    # egreso de ítems serializados. Estructura: { item_id: { loc_id: [[unit_id, serial], ...] } }
    # El TECNICO solo necesita (y solo debe ver) los seriales de sus ubicaciones.
    units_map, serialized_item_ids = build_units_map(
        location_ids=tech_location_ids if is_tecnico else None
    )

    return render_template(
        "movements.html",
        items=items_list,
        locations=locations_list,
        logs=logs,
        stock_map=stock_map,
        stock_qty_map=stock_qty_map,
        external_location_ids=external_location_ids,
        units_map=units_map,
        serialized_item_ids=serialized_item_ids,
        users=users_list,
        from_date=filters["date_from"],
        to_date=filters["date_to"],
        item_filter=str(filters["item_filter"] or ""),
        user_filter=str(filters["user_filter"] or ""),
        limit=str(limit),
        selected_item_id=str(filters["item_filter"] or ""),
        selected_user_id=str(filters["user_filter"] or ""),
        selected_date_from=filters["date_from"],
        selected_date_to=filters["date_to"],
        selected_limit=limit,
        selected_sort_by=sort_by,
        selected_sort_dir=sort_dir,
        is_tecnico=is_tecnico,
        tech_locations=tech_locations,
        descartes_id=descartes_id,
    )

@app.route("/movements/bulk", methods=["GET", "POST"])
@login_required
@role_required("ADMIN", "SUPERVISOR")
def movements_bulk():
    items_list = Item.query.filter_by(is_active=True).order_by(Item.code).all()
    locations_list = Location.query.order_by(Location.name).all()

    if request.method == "POST":
        from_id_raw = request.form.get("from_location_id", "").strip()
        to_id_raw = request.form.get("to_location_id", "").strip()
        observation = (request.form.get("observacion", "") or request.form.get("observation", "")).strip() or None

        item_ids = request.form.getlist("item_id[]")
        qtys = request.form.getlist("qty[]")
        pending_flags = request.form.getlist("generate_pending[]")
        pending_comments = request.form.getlist("pending_comment[]")
        scrap_reasons = request.form.getlist("scrap_reason[]")

        if not (from_id_raw.isdigit() and to_id_raw.isdigit()):
            flash("Desde y Hacia son obligatorios.", "error")
            return redirect(url_for("movements_bulk"))

        from_id = int(from_id_raw)
        to_id = int(to_id_raw)

        if from_id == to_id:
            flash("Desde y Hacia no pueden ser la misma ubicacion.", "error")
            return redirect(url_for("movements_bulk"))

        to_location = Location.query.get(to_id)
        is_to_descartes = to_location and to_location.name == "Descartes"

        # Limpiamos filas vacias
        lines = []
        for idx in range(len(item_ids)):
            item_raw = (item_ids[idx] or "").strip()
            qty_raw = (qtys[idx] or "").strip()
            pending_raw = (pending_flags[idx] or "").strip() if idx < len(pending_flags) else ""
            comment_raw = (pending_comments[idx] or "").strip() if idx < len(pending_comments) else ""
            scrap_reason_raw = (scrap_reasons[idx] or "").strip() if idx < len(scrap_reasons) else ""

            if not item_raw and not qty_raw:
                continue

            lines.append({
                "item_raw": item_raw,
                "qty_raw": qty_raw,
                "generate_pending": pending_raw == "1",
                "pending_comment": comment_raw or None,
                "scrap_reason": scrap_reason_raw,
            })

        if not lines:
            flash("Tenes que cargar al menos un item.", "error")
            return redirect(url_for("movements_bulk"))

        pending_responsible_id = None

        # Validacion de responsable solo si alguna linea genera pendiente
        if any(line["generate_pending"] for line in lines):
            if not to_location:
                flash("La ubicacion destino no existe.", "error")
                return redirect(url_for("movements_bulk"))

            first_resp = LocationResponsible.query.filter_by(location_id=to_id).first()
            if not first_resp:
                flash("La ubicacion destino no tiene responsable asignado. Edita la ubicacion antes de generar pendientes.", "error")
                return redirect(url_for("movements_bulk"))

            pending_responsible_id = first_resp.user_id

        parsed_lines = []

        # Validaciones por linea
        for idx, line in enumerate(lines, start=1):
            item_raw = line["item_raw"]
            qty_raw = line["qty_raw"]

            if not item_raw.isdigit():
                flash(f"Linea {idx}: item invalido.", "error")
                return redirect(url_for("movements_bulk"))

            try:
                qty = int(qty_raw)
                if qty <= 0:
                    raise ValueError()
            except Exception:
                flash(f"Linea {idx}: cantidad invalida.", "error")
                return redirect(url_for("movements_bulk"))

            if is_to_descartes and not line["scrap_reason"]:
                flash(f"Linea {idx}: motivo de descarte obligatorio.", "error")
                return redirect(url_for("movements_bulk"))

            item_id = int(item_raw)
            it = Item.query.get(item_id)
            if not it or not it.is_active:
                flash(f"Linea {idx}: el item seleccionado no existe o esta inactivo.", "error")
                return redirect(url_for("movements_bulk"))

            # Serializados: la carga múltiple no permite elegir seriales, así que
            # se bloquea para no desincronizar las unidades. Se usa Movimientos.
            if it.serialized:
                flash(
                    f"Linea {idx}: «{it.code} - {it.name}» es serializado. "
                    "Cargalo desde Movimientos para elegir los seriales.",
                    "error",
                )
                return redirect(url_for("movements_bulk"))

            parsed_lines.append({
                "item_id": item_id,
                "qty": qty,
                "generate_pending": line["generate_pending"],
                "pending_comment": line["pending_comment"],
                "scrap_reason": line["scrap_reason"],
            })

        try:
            created_count = 0

            for line in parsed_lines:
                item_id = line["item_id"]
                qty = line["qty"]

                if not location_is_external(from_id):
                    upsert_stock(item_id, from_id, -qty)
                if not location_is_external(to_id):
                    upsert_stock(item_id, to_id, qty)

                y, seq, number = next_movement_number()
                m = Movement(
                    item_id=item_id,
                    qty=qty,
                    from_location_id=from_id,
                    to_location_id=to_id,
                    user_id=current_user.id,
                    observation=observation,
                    year=y,
                    seq=seq,
                    number=number,
                )
                db.session.add(m)
                db.session.flush()

                if line["generate_pending"]:
                    pd = PendingDelivery(
                        movement_id=m.id,
                        responsible_from_id=current_user.id,
                        responsible_to_id=pending_responsible_id,
                        item_id=item_id,
                        comment=line["pending_comment"],
                        returned=False
                    )
                    db.session.add(pd)

                if is_to_descartes:
                    db.session.add(Scrap(
                        item_id=item_id,
                        location_id=from_id,
                        quantity=qty,
                        reason=line["scrap_reason"],
                        user_id=current_user.id,
                    ))

                created_count += 1

            db.session.commit()
            flash(f"Carga multiple registrada. Movimientos creados: {created_count}", "ok")

        except Exception as e:
            db.session.rollback()
            flash(f"No se pudo registrar la carga multiple: {e}", "error")

        return redirect(url_for("movements_bulk"))

    _descartes_b = Location.query.filter_by(name="Descartes").first()

    # Mapa ubicacion -> items con stock (>0), para filtrar el selector "Item" segun "Desde"
    stock_map = {}
    stock_qty_map = {}  # {loc_id(str): {item_id(str): qty}} para el tope de cantidad
    for s in Stock.query.filter(Stock.quantity > 0).all():
        stock_map.setdefault(s.location_id, []).append(s.item_id)
        stock_qty_map.setdefault(str(s.location_id), {})[str(s.item_id)] = s.quantity

    # Externas: ofrecer todos los items al elegirlas como origen (no tienen stock).
    external_location_ids = [l.id for l in locations_list if l.is_external]

    return render_template(
        "movements_bulk.html",
        items=items_list,
        locations=locations_list,
        descartes_id=_descartes_b.id if _descartes_b else None,
        stock_map=stock_map,
        stock_qty_map=stock_qty_map,
        external_location_ids=external_location_ids,
    )


@app.route("/movements/export.csv", methods=["GET"])
@login_required
@role_required("ADMIN", "SUPERVISOR")
def movements_export_csv():
    """Exporta movimientos a CSV respetando los mismos filtros que la pantalla."""
    filters = _movements_filters_from_request()

    # Límite para export (default 5000; cap 50000)
    raw_limit = (request.args.get("limit") or "5000").strip()
    try:
        export_limit = int(raw_limit)
    except Exception:
        export_limit = 5000
    export_limit = max(1, min(export_limit, 50000))

    q = _build_movements_query(filters)

    # Mismo scope por ubicacion que la pantalla /movements: el TECNICO solo ve
    # movimientos originados en sus ubicaciones asignadas.
    if current_user.role == "TECNICO":
        tech_location_ids = [
            l.id for l in Location.query.join(LocationResponsible).filter(
                LocationResponsible.user_id == current_user.id
            ).all()
        ]
        if tech_location_ids:
            q = q.filter(Movement.from_location_id.in_(tech_location_ids))
        else:
            q = q.filter(Movement.from_location_id == -1)

    q = q.order_by(Movement.created_at.desc()).limit(export_limit)
    rows = q.all()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "fecha",
        "hora",
        "item_codigo",
        "item_nombre",
        "cantidad",
        "desde",
        "hacia",
        "responsable",
        "observacion",
    ])

    for m in rows:
        writer.writerow([
            m.created_at.strftime("%Y-%m-%d"),
            m.created_at.strftime("%H:%M"),
            m.item.code,
            m.item.name,
            m.qty,
            m.from_location.name if m.from_location else "",
            m.to_location.name if m.to_location else "",
            m.user.full_name or m.user.username,
            m.observation or "",
        ])

    csv_bytes = output.getvalue().encode("utf-8-sig")
    filename = f"movimientos_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        csv_bytes,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )



# ------------------ UTILIZADOS ------------------

@app.route("/item-usage", methods=["GET", "POST"])
@login_required
@role_required("ADMIN", "SUPERVISOR", "TECNICO")
def item_usage():
    is_tecnico = current_user.role == "TECNICO"
    utilizados_loc = Location.query.filter_by(name="Utilizado").first()

    if is_tecnico:
        tech_locations = Location.query.join(LocationResponsible).filter(
            LocationResponsible.user_id == current_user.id,
        ).all()
        if not tech_locations:
            flash("No tenés ubicación asignada.", "error")
            # Contexto completo para no dejar variables Undefined en el template
            # (stock_map|tojson fallaba con 500 para un TECNICO sin ubicación).
            return render_template(
                "item_usage.html",
                items=[], is_tecnico=True, locations=[], movements=[],
                stock_map={}, users=[], from_date="", to_date="",
                item_filter="", user_filter="", limit="100",
            )
        tech_location_ids = {l.id for l in tech_locations}
    else:
        tech_locations = []
        tech_location_ids = set()

    if request.method == "POST":
        item_id = request.form.get("item_id", "").strip()
        qty_raw = request.form.get("qty", "").strip()
        observation = request.form.get("observation", "").strip() or None
        from_id_raw = request.form.get("from_location_id", "").strip()

        if not item_id.isdigit():
            flash("Item obligatorio.", "error")
            return redirect(url_for("item_usage"))

        try:
            qty = int(qty_raw)
            if qty <= 0:
                raise ValueError()
        except Exception:
            flash("Cantidad inválida.", "error")
            return redirect(url_for("item_usage"))

        if not from_id_raw.isdigit():
            flash("Ubicación origen obligatoria.", "error")
            return redirect(url_for("item_usage"))

        item_id = int(item_id)
        from_location_id = int(from_id_raw)

        if is_tecnico and from_location_id not in tech_location_ids:
            flash("Solo podés usar desde tus ubicaciones asignadas.", "error")
            return redirect(url_for("item_usage"))

        if not utilizados_loc:
            flash("Ubicación 'Utilizado' no existe.", "error")
            return redirect(url_for("item_usage"))

        try:
            item = Item.query.get(item_id)
            if not item or not item.is_active:
                flash("Item no existe o está dado de baja.", "error")
                return redirect(url_for("item_usage"))

            # Serializado: resolver qué seriales se consumen (auto/elegir).
            serial_units = []
            if item.serialized:
                serial_units, err = resolve_serial_units_out(item_id, from_location_id, qty, request.form)
                if err:
                    flash(err, "error")
                    return redirect(url_for("item_usage"))

            upsert_stock(item_id, from_location_id, -qty)

            base_obs = observation or "Utilizado"
            if item.serialized:
                base_obs = serial_obs(base_obs, [u.serial for u in serial_units])

            y, seq, number = next_movement_number()
            m = Movement(
                item_id=item_id,
                qty=qty,
                from_location_id=from_location_id,
                to_location_id=utilizados_loc.id,
                user_id=current_user.id,
                observation=base_obs,
                year=y,
                seq=seq,
                number=number,
            )
            db.session.add(m)
            db.session.flush()
            if item.serialized:
                # "Utilizado" es externa -> las unidades quedan ENTREGADO (consumidas).
                apply_serial_units_out(serial_units, utilizados_loc.id)
            db.session.commit()
            flash(f"Item marcado como utilizado. Movimiento {number} registrado.", "ok")
            return redirect(url_for("item_usage"))

        except ValueError as e:
            db.session.rollback()
            flash(f"Error de stock: {e}", "error")
            return redirect(url_for("item_usage"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "error")
            return redirect(url_for("item_usage"))

    if is_tecnico:
        # TECNICO: solo items que tiene en stock (>0) en su(s) ubicacion(es)
        items = (
            Item.query.filter_by(is_active=True)
            .join(Stock, Stock.item_id == Item.id)
            .filter(Stock.location_id.in_(tech_location_ids), Stock.quantity > 0)
            .distinct()
            .order_by(Item.code)
            .all()
        ) if tech_location_ids else []
    else:
        items = Item.query.filter_by(is_active=True).order_by(Item.code).all()
    utilizados_id = utilizados_loc.id if utilizados_loc else None

    # Filtros del historial (admin: fecha/item/responsable/mostrar; tecnico: fecha/item)
    f_date_from = request.args.get("from_date", "").strip()
    f_date_to = request.args.get("to_date", "").strip()
    f_item = (request.args.get("item_id") or request.args.get("item_filter") or "").strip()
    f_user = (request.args.get("user_id") or request.args.get("user_filter") or "").strip()
    f_limit_raw = request.args.get("limit", "").strip()
    default_limit = 100 if is_tecnico else 200
    f_limit = int(f_limit_raw) if f_limit_raw.isdigit() else default_limit
    f_limit = max(1, min(f_limit, 5000))

    if utilizados_id and (not is_tecnico or tech_location_ids):
        hq = Movement.query.filter(Movement.to_location_id == utilizados_id)
        if is_tecnico:
            hq = hq.filter(Movement.from_location_id.in_(tech_location_ids))
        if f_date_from:
            hq = hq.filter(Movement.created_at >= datetime.fromisoformat(f_date_from))
        if f_date_to:
            hq = hq.filter(Movement.created_at <= datetime.fromisoformat(f_date_to + "T23:59:59"))
        if f_item.isdigit():
            hq = hq.filter(Movement.item_id == int(f_item))
        if (not is_tecnico) and f_user.isdigit():
            hq = hq.filter(Movement.user_id == int(f_user))
        movements_list = hq.order_by(Movement.created_at.desc()).limit(f_limit).all()
    else:
        movements_list = []

    if is_tecnico:
        locations = tech_locations
    else:
        # Origen para Utilizados: camionetas + la Jaula central (no es truck pero
        # tambien se consumen items desde ahi).
        locations = (
            Location.query.filter(
                db.or_(Location.is_truck == True, Location.name == LOCATION_JAULA_TNG)
            )
            .order_by(Location.name)
            .all()
        )

    users_list = User.query.order_by(User.username).all()

    stock_map = {}
    for s in Stock.query.filter(Stock.quantity > 0).all():
        stock_map.setdefault(s.location_id, []).append(s.item_id)

    units_map, serialized_item_ids = build_units_map(
        location_ids=tech_location_ids if is_tecnico else None
    )

    return render_template(
        "item_usage.html",
        items=items,
        locations=locations,
        movements=movements_list,
        is_tecnico=is_tecnico,
        stock_map=stock_map,
        units_map=units_map,
        serialized_item_ids=serialized_item_ids,
        users=users_list,
        from_date=f_date_from,
        to_date=f_date_to,
        item_filter=f_item,
        user_filter=f_user,
        limit=str(f_limit),
    )


# ------------------ REMITOS (agrupan movimientos existentes) ------------------
#
# Refactor: el remito NO crea movimientos ni toca stock. Agrupa movimientos
# que YA ocurrieron en una relación Desde->Hacia y les da un número imprimible.

# Ver remitos: todos los roles autenticados (modo lectura).
REMITO_VIEW_ROLES = ("ADMIN", "SUPERVISOR", "TECNICO", "LECTOR")
# Crear remitos / usar los helpers del modal de creacion: solo ADMIN y SUPERVISOR.
REMITO_EDIT_ROLES = ("ADMIN", "SUPERVISOR")
# Compat: alias historico (algun codigo/documentacion previa lo referenciaba).
REMITO_ROLES = REMITO_VIEW_ROLES


def unremitted_movements(from_id: int, to_id: int, date_from=None, date_to=None):
    """Movimientos de la relación from->to, en el rango de fechas, sin remito.

    date_from / date_to son objetos date (inclusive). Si faltan, se usa el día
    de hoy. El orden es por fecha descendente. No hay límite de cantidad: el
    rango de fechas ya acota el resultado (por defecto, solo el día de hoy).
    """
    today = now_ar().date()
    if date_from is None:
        date_from = today
    if date_to is None:
        date_to = today

    start = datetime(date_from.year, date_from.month, date_from.day, 0, 0, 0)
    end = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59)

    used = db.session.query(RemitoLine.movement_id)
    return (
        Movement.query
        .filter(
            Movement.from_location_id == from_id,
            Movement.to_location_id == to_id,
            Movement.created_at >= start,
            Movement.created_at <= end,
            ~Movement.id.in_(used),
        )
        .order_by(Movement.created_at.desc())
        .all()
    )


def _parse_date_arg(raw: str):
    """Parsea 'YYYY-MM-DD' a date; devuelve None si está vacío o es inválido."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def location_responsibles(location_id: int):
    """Usuarios a cargo de una ubicación (ordenados por nombre)."""
    return (
        User.query
        .join(LocationResponsible, LocationResponsible.user_id == User.id)
        .filter(LocationResponsible.location_id == location_id)
        .order_by(User.full_name)
        .all()
    )


@app.route("/remitos", methods=["GET"])
@login_required
@role_required(*REMITO_VIEW_ROLES)
def remitos():
    resp_id = request.args.get("responsible_id", "").strip()

    query = Remito.query
    if current_user.role == "TECNICO":
        # TECNICO: solo remitos donde es parte (origen o destino). No puede
        # cambiar el filtro; se ignora responsible_id de la query.
        query = query.filter(
            db.or_(
                Remito.responsible_from_id == current_user.id,
                Remito.responsible_to_id == current_user.id,
            )
        )
        resp_id = ""
    elif resp_id.isdigit():
        rid = int(resp_id)
        query = query.filter(
            db.or_(
                Remito.responsible_from_id == rid,
                Remito.responsible_to_id == rid,
            )
        )

    remitos_list = query.order_by(Remito.created_at.desc()).limit(500).all()
    locations_list = Location.query.order_by(Location.name).all()

    # Usuarios que son responsables de alguna ubicación, para el filtro.
    responsible_users = (
        User.query
        .join(LocationResponsible, LocationResponsible.user_id == User.id)
        .distinct()
        .order_by(User.full_name)
        .all()
    )

    return render_template(
        "remitos.html",
        remitos=remitos_list,
        locations=locations_list,
        responsible_users=responsible_users,
        responsible_id=resp_id,
        today=now_ar().date().isoformat(),
    )


@app.route("/remitos/responsables", methods=["GET"])
@login_required
@role_required(*REMITO_EDIT_ROLES)
def remito_responsables():
    """JSON con los responsables de una ubicación (para el modal de creación)."""
    loc_id = request.args.get("location_id", "").strip()
    if not loc_id.isdigit():
        return {"external": False, "responsibles": []}
    loc_id = int(loc_id)
    external = location_is_external(loc_id)
    people = [] if external else [
        {"id": u.id, "name": u.full_name or u.username}
        for u in location_responsibles(loc_id)
    ]
    return {"external": external, "responsibles": people}


@app.route("/remitos/movimientos", methods=["GET"])
@login_required
@role_required(*REMITO_EDIT_ROLES)
def remito_movimientos():
    """Fragmento HTML: movimientos de la relación elegida en el rango de fechas."""
    from_id = request.args.get("from_location_id", "").strip()
    to_id = request.args.get("to_location_id", "").strip()

    if not (from_id.isdigit() and to_id.isdigit()):
        return render_template("_remito_mov_rows.html", movements=None, same=False)

    from_id, to_id = int(from_id), int(to_id)
    if from_id == to_id:
        return render_template("_remito_mov_rows.html", movements=None, same=True)

    date_from = _parse_date_arg(request.args.get("from_date") or request.args.get("date_from", ""))
    date_to = _parse_date_arg(request.args.get("to_date") or request.args.get("date_to", ""))
    movs = unremitted_movements(from_id, to_id, date_from, date_to)
    return render_template("_remito_mov_rows.html", movements=movs, same=False)


@app.route("/remitos/new", methods=["POST"])
@login_required
@role_required(*REMITO_EDIT_ROLES)
def remito_new():
    """Crea un remito agrupando movimientos existentes. NO toca stock."""
    from_id = request.form.get("from_location_id", "").strip()
    to_id = request.form.get("to_location_id", "").strip()
    observation = (request.form.get("observation", "") or "").strip() or None

    if not (from_id.isdigit() and to_id.isdigit()):
        flash("Desde y Hacia son obligatorios.", "error")
        return redirect(url_for("remitos"))

    from_id, to_id = int(from_id), int(to_id)
    if from_id == to_id:
        flash("Desde y Hacia no pueden ser la misma ubicación.", "error")
        return redirect(url_for("remitos"))

    selected = request.form.getlist("movement_id")
    used = {row[0] for row in db.session.query(RemitoLine.movement_id).all()}
    mov_ids = []
    for sid in selected:
        if not sid.isdigit():
            continue
        mid = int(sid)
        if mid in used or mid in mov_ids:
            continue
        m = Movement.query.get(mid)
        if not m:
            continue
        # Seguridad: el movimiento debe pertenecer a la relación elegida.
        if m.from_location_id != from_id or m.to_location_id != to_id:
            continue
        mov_ids.append(mid)

    if not mov_ids:
        flash("Seleccioná al menos un movimiento válido de esa relación.", "error")
        return redirect(url_for("remitos"))

    # Responsables elegidos. Regla: si la ubicación es externa (proveedor) el
    # responsable queda vacío (se completa a mano). Si NO es externa, es
    # obligatorio y debe ser un responsable real de esa ubicación.
    def resolve_responsible(field_name: str, loc_id: int, label: str):
        if location_is_external(loc_id):
            return None, None  # (valor, error)
        raw = request.form.get(field_name, "").strip()
        if not raw.isdigit():
            return None, f"Elegí el responsable de {label}."
        rid = int(raw)
        valid_ids = {u.id for u in location_responsibles(loc_id)}
        if rid not in valid_ids:
            return None, f"El responsable de {label} no es válido para esa ubicación."
        return rid, None

    resp_from, err = resolve_responsible("responsible_from_id", from_id, "entrega")
    if err:
        flash(err, "error")
        return redirect(url_for("remitos"))
    resp_to, err = resolve_responsible("responsible_to_id", to_id, "recibe")
    if err:
        flash(err, "error")
        return redirect(url_for("remitos"))

    try:
        y, seq, number = next_remito_number()
        r = Remito(
            year=y,
            seq=seq,
            number=number,
            status="CONFIRMADO",
            from_location_id=from_id,
            to_location_id=to_id,
            created_by_user_id=current_user.id,
            observation=observation,
            responsible_from_id=resp_from,
            responsible_to_id=resp_to,
        )
        db.session.add(r)
        db.session.flush()  # obtener r.id
        for mid in mov_ids:
            db.session.add(RemitoLine(remito_id=r.id, movement_id=mid))
        db.session.commit()
        flash(f"Remito creado: {number}", "ok")
        return redirect(url_for("remito_detail", remito_id=r.id))
    except Exception as e:
        db.session.rollback()
        flash(f"No se pudo crear el remito: {e}", "error")
        return redirect(url_for("remitos"))


@app.route("/remitos/<int:remito_id>", methods=["GET"])
@login_required
@role_required(*REMITO_VIEW_ROLES)
def remito_detail(remito_id: int):
    r = Remito.query.get_or_404(remito_id)
    # TECNICO: solo puede abrir remitos donde es parte (evita ver ajenos por URL).
    if current_user.role == "TECNICO" and current_user.id not in (
        r.responsible_from_id,
        r.responsible_to_id,
    ):
        flash("No tenés permisos para ver ese remito.", "error")
        return redirect(url_for("remitos"))
    lines = (
        RemitoLine.query.filter_by(remito_id=r.id)
        .join(Movement)
        .order_by(Movement.created_at.desc())
        .all()
    )
    # Filas vacías para que el remito impreso mantenga un cuerpo prolijo.
    fill_rows = max(0, 16 - len(lines))
    embed = request.args.get("embed") == "1"
    return render_template("remito_detail.html", remito=r, lines=lines, fill_rows=fill_rows, embed=embed)


def fmt_qty(qty, item=None):
    """Cantidad lista para mostrar, con su unidad al lado.

    - 'metros' -> "300 metros"
    - 'unidad' (o sin dato) -> "300" (comportamiento histórico, sin etiqueta).
    No cambia la lógica de stock: solo formatea para la vista.
    """
    unit = getattr(item, "unit", None) if item is not None else None
    if unit == "metros":
        return f"{qty} metros"
    return f"{qty}"


@app.context_processor
def inject_stock_helpers():
    return {
        "stock_level_class": stock_level_class,
        "fmt_qty": fmt_qty,
    }

@app.context_processor
def inject_me():
    # Hacemos que los templates tengan siempre `me` disponible de forma consistente.
    # Esto evita depender de session manual y respeta Flask-Login.
    if current_user.is_authenticated:
        return {"me": current_user}
    return {"me": None}


@app.context_processor
def inject_alert_badge():
    """Puntito de novedades en 'Alertas de Stock'.

    Cuenta ítems que entraron en alerta y que el usuario todavía no vio.
    'Visto' se guarda en la sesión (no toca la DB) y se actualiza cuando el
    usuario abre la pantalla de Alertas de Stock. Solo aplica a roles que
    pueden ver esa pantalla (ADMIN/SUPERVISOR).
    """
    count = 0
    try:
        if current_user.is_authenticated and current_user.role in ("ADMIN", "SUPERVISOR"):
            current_ids = {e["item"].id for e in alert_items_distinct()}
            seen = session.get("alerts_seen")
            if seen is None:
                # Primera carga de la sesión: tomamos la foto actual como base
                # para no marcar como "nuevo" todo lo que ya venía en alerta.
                session["alerts_seen"] = list(current_ids)
                count = 0
            else:
                count = len(current_ids - set(seen))
    except Exception:
        count = 0
    return {"alert_badge_count": count}


@app.context_processor
def inject_pending_badge():
    """Badge rojo de 'Pendientes' (persistente, no session).

    Cuenta pendientes abiertos (returned=False). A diferencia del badge de
    Alertas, no usa 'visto/no visto': se muestra siempre que haya pendientes
    abiertos y desaparece cuando llega a 0.

    - TECNICO: solo lo que él debe devolver (responsible_to_id == su id).
    - ADMIN/SUPERVISOR: total de pendientes abiertos.
    - LECTOR: sin badge (no tiene esa pantalla).
    """
    count = 0
    try:
        if current_user.is_authenticated:
            role = current_user.role
            if role == "TECNICO":
                count = PendingDelivery.query.filter_by(
                    returned=False, responsible_to_id=current_user.id
                ).count()
            elif role in ("ADMIN", "SUPERVISOR"):
                count = PendingDelivery.query.filter_by(returned=False).count()
    except Exception:
        count = 0
    return {"pending_badge_count": count}
# ------------------ ADMIN: EDICIÓN (solo ADMIN) ------------------

@app.route("/locations/<int:loc_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("ADMIN")
def location_edit(loc_id):
    loc = Location.query.get_or_404(loc_id)
    users_list = User.query.order_by(User.username).all()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip() or None
        is_external = True if request.form.get("is_external") == "on" else False

        if not name:
            flash("Nombre requerido", "error")
            return redirect(url_for("location_edit", loc_id=loc.id))

        if _name_taken(Location, name, exclude_id=loc.id):
            flash("Ya existe otra ubicación con ese nombre", "error")
            return redirect(url_for("location_edit", loc_id=loc.id))

        loc.name = name
        loc.description = description
        loc.is_external = is_external
        loc.is_truck = bool(request.form.get("is_truck"))

        LocationResponsible.query.filter_by(location_id=loc.id).delete()
        for uid in request.form.getlist("responsible_user_ids"):
            if uid.isdigit():
                db.session.add(LocationResponsible(location_id=loc.id, user_id=int(uid)))

        db.session.commit()
        flash("Ubicación actualizada", "ok")
        return redirect(url_for("locations"))

    current_responsibles = {lr.user_id for lr in LocationResponsible.query.filter_by(location_id=loc.id).all()}
    return render_template("edit_location.html", loc=loc, users=users_list, current_responsibles=current_responsibles)


@app.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("ADMIN")
def user_edit(user_id):
    u = User.query.get_or_404(user_id)

    # Guarda de permisos: un SUPERVISOR no puede tocar ADMIN ni otros SUPERVISOR.
    if not can_manage_target(u):
        flash("No tenés permisos para editar ese usuario.", "error")
        return redirect(url_for("users"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        full_name = request.form.get("full_name", "").strip()
        role = request.form.get("role", "").strip()

        if not username or not full_name or not role:
            flash("Username, nombre y rol son obligatorios", "error")
            return redirect(url_for("user_edit", user_id=u.id))

        # Un SUPERVISOR solo puede asignar roles no privilegiados.
        if role not in assignable_roles_for_current():
            flash("No podés asignar ese rol.", "error")
            return redirect(url_for("user_edit", user_id=u.id))

        # unicidad username (sin importar mayúsculas)
        if username_taken(username, exclude_id=u.id):
            flash("Ya existe otro usuario con ese username", "error")
            return redirect(url_for("user_edit", user_id=u.id))

        u.username = username
        u.full_name = full_name
        u.role = role
        u.email = request.form.get("email", "").strip() or None
        db.session.commit()
        flash("Usuario actualizado", "ok")
        return redirect(url_for("users"))

    return render_template("edit_user.html", u=u, roles=assignable_roles_for_current())


@app.route("/users/<int:user_id>/password", methods=["GET", "POST"])
@login_required
@role_required("ADMIN")
def user_password(user_id):
    u = User.query.get_or_404(user_id)

    # Guarda de permisos: un SUPERVISOR no puede cambiar la clave de ADMIN ni SUPERVISOR.
    if not can_manage_target(u):
        flash("No tenés permisos para cambiar la clave de ese usuario.", "error")
        return redirect(url_for("users"))

    if request.method == "POST":
        new_password = request.form.get("password", "").strip()
        if not new_password or len(new_password) < MIN_PASSWORD_LEN:
            flash(f"Contraseña inválida (mínimo {MIN_PASSWORD_LEN} caracteres)", "error")
            return redirect(url_for("user_password", user_id=u.id))

        u.set_password(new_password)
        db.session.commit()
        flash("Contraseña actualizada", "ok")
        return redirect(url_for("users"))

    return render_template("edit_user_password.html", u=u)

@app.route("/pending-deliveries", methods=["GET", "POST"])
@login_required
@role_required("ADMIN", "SUPERVISOR", "TECNICO")
def pending_deliveries():

    if request.method == "POST":
        # TECNICO: solo lectura. Puede ver lo que debe pero no cerrar pendientes.
        if current_user.role == "TECNICO":
            flash("No tenés permisos para cerrar pendientes.", "error")
            return redirect(url_for("pending_deliveries"))
        pid = request.form.get("pending_id", "").strip()
        return_observation = request.form.get("return_observation", "").strip()

        if not pid.isdigit():
            flash("Pendiente invalido.", "error")
            return redirect(url_for("pending_deliveries"))

        p = PendingDelivery.query.get(int(pid))
        if not p:
            flash("Pendiente no encontrado.", "error")
            return redirect(url_for("pending_deliveries"))

        if p.returned:
            flash("Ese pendiente ya estaba marcado como devuelto.", "ok")
            return redirect(url_for("pending_deliveries"))

        original_movement = p.movement
        if not original_movement:
            flash("El pendiente no tiene movimiento origen asociado.", "error")
            return redirect(url_for("pending_deliveries"))

        return_action = request.form.get("return_action", "return")
        scrap_reason = request.form.get("scrap_reason", "Otro").strip() or "Otro"

        try:
            # Cantidad e item que efectivamente vuelven (defaults = lo entregado).
            qty = p.return_qty if p.return_qty else original_movement.qty
            returns_item_id = p.return_item_id or original_movement.item_id
            # Swap: vuelve un item DISTINTO al entregado.
            is_swap = bool(p.return_item_id) and p.return_item_id != original_movement.item_id

            if is_swap:
                # Ingreso del item recuperado: origen externo 'Recuperado' (no descuenta).
                recuperado = Location.query.filter_by(name=LOCATION_RECUPERADO).first()
                if not recuperado:
                    flash("Ubicación 'Recuperado' no existe.", "error")
                    return redirect(url_for("pending_deliveries"))
                from_id = recuperado.id
            else:
                # Mismo item: sale de donde se lo habia enviado (ubicacion del tecnico).
                from_id = original_movement.to_location_id

            if return_action == "scrap":
                descartes_loc = Location.query.filter_by(name="Descartes").first()
                if not descartes_loc:
                    flash("Ubicación 'Descartes' no existe.", "error")
                    return redirect(url_for("pending_deliveries"))
                to_id = descartes_loc.id
            elif return_action == "repair":
                repair_loc = Location.query.filter_by(name=LOCATION_EN_REPARACION).first()
                if not repair_loc:
                    flash("Ubicación 'En reparación' no existe.", "error")
                    return redirect(url_for("pending_deliveries"))
                to_id = repair_loc.id
            else:
                # Devolver / Ingresar OK: mismo item vuelve a su origen; swap entra a Jaula.
                if is_swap:
                    jaula = Location.query.filter_by(name=LOCATION_JAULA_TNG).first()
                    if not jaula:
                        flash("Ubicación 'Jaula TNG' no existe.", "error")
                        return redirect(url_for("pending_deliveries"))
                    to_id = jaula.id
                else:
                    to_id = original_movement.from_location_id

            # Guarda serializados: la devolución/reparación por serial todavía no está
            # modelada. Se bloquea para no desincronizar las unidades; el serial se
            # maneja manualmente desde Movimientos.
            _ret_item = Item.query.get(returns_item_id)
            if _ret_item and _ret_item.serialized:
                flash(
                    "El ítem es serializado: gestioná la devolución/reparación del "
                    "serial desde Movimientos (esta pantalla aún no maneja seriales).",
                    "error",
                )
                return redirect(url_for("pending_deliveries"))

            if not location_is_external(from_id):
                upsert_stock(returns_item_id, from_id, -qty)
            if not location_is_external(to_id):
                upsert_stock(returns_item_id, to_id, qty)

            if return_action == "scrap":
                obs = return_observation or f"Scrap ({scrap_reason}) de pendiente #{p.id}"
            elif return_action == "repair":
                obs = return_observation or f"A reparación de pendiente #{p.id}"
            else:
                obs = return_observation or f"Devolucion de pendiente #{p.id}"
            if is_swap:
                obs = f"[Devolución distinta] {obs}"

            y, seq, number = next_movement_number()
            m = Movement(
                item_id=returns_item_id,
                qty=qty,
                from_location_id=from_id,
                to_location_id=to_id,
                user_id=current_user.id,
                observation=obs,
                year=y,
                seq=seq,
                number=number,
            )
            db.session.add(m)

            if return_action == "scrap":
                db.session.add(Scrap(
                    item_id=returns_item_id,
                    location_id=from_id,
                    quantity=qty,
                    reason=scrap_reason,
                    user_id=current_user.id,
                ))
            elif return_action == "repair":
                db.session.add(Repair(
                    item_id=returns_item_id,
                    quantity=qty,
                    status="EN_REPARACION",
                    pending_id=p.id,
                    source_location_id=from_id,
                    created_by_user_id=current_user.id,
                ))

            p.returned = True
            db.session.commit()
            accion = {"scrap": "Scrap", "repair": "Reparación"}.get(return_action, "Devuelto")
            flash(f"Pendiente cerrado ({number}). Acción: {accion}.", "ok")

        except Exception as e:
            db.session.rollback()
            flash(f"No se pudo cerrar el pendiente: {e}", "error")

        return redirect(url_for("pending_deliveries"))

    pendings_q = PendingDelivery.query
    # TECNICO: solo los pendientes que él debe devolver (a su nombre).
    if current_user.role == "TECNICO":
        pendings_q = pendings_q.filter(
            PendingDelivery.responsible_to_id == current_user.id
        )
    pendings = pendings_q.order_by(PendingDelivery.created_at.desc()).all()

    return render_template(
        "pending_deliveries.html",
        pendings=pendings
    )

@app.route("/import/items", methods=["GET", "POST"])
@login_required
@role_required("ADMIN")
def import_items():
    """Importa items desde CSV. SOLO ALTAS (no actualiza items existentes).

    Correcciones Fase 0:
      - seen_codes se crea ANTES del loop (detecta duplicados internos);
      - importa reference_link (se guarda realmente, máx 500);
      - valida encabezados minimos (code, name, category);
      - decodifica probando varias codificaciones y detecta delimitador;
      - no crea categorias automaticamente (fila sin categoria = error);
      - helper claro de booleanos (valor no reconocido = fila con error);
      - stock_min entero >= 0 (vacio = 0, negativo = error);
      - un unico commit al final; ante error de DB hace rollback y avisa;
      - contadores reales (creados / saltados / errores).

    reference_link se guarda realmente (columna del modelo, máx 500 chars).
    """
    if request.method != "POST":
        return render_template("import_items.html")

    file = request.files.get("file")
    if not file or not file.filename:
        flash("No se envió archivo.", "error")
        return redirect(request.url)

    created = 0
    skipped = 0
    errors = 0

    # 1) Leer y decodificar (varias codificaciones).
    try:
        content = file.read()
    except Exception as exc:
        flash(f"No se pudo leer el archivo: {exc}", "error")
        return redirect(request.url)

    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        flash("No se pudo decodificar el archivo (codificación no soportada).", "error")
        return redirect(request.url)

    # 2) Detectar delimitador (coma o punto y coma).
    sample = text[:4096]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=";,").delimiter
    except Exception:
        delimiter = ";" if sample.count(";") > sample.count(",") else ","

    # 3) Crear el reader sobre el TEXTO ya decodificado (no re-decodificar bytes).
    try:
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        raw_headers = reader.fieldnames or []
    except Exception as exc:
        flash(f"No se pudo interpretar el CSV: {exc}", "error")
        return redirect(request.url)

    # 4) Validar encabezados minimos (normalizando espacios y mayus/minus).
    headers = {(h or "").strip().lower(): (h or "") for h in raw_headers}
    required = ("code", "name", "category")
    missing = [c for c in required if c not in headers]
    if missing:
        flash(
            "El CSV no tiene los encabezados mínimos. Faltan: "
            + ", ".join(missing)
            + ". Encabezados requeridos: code, name, category.",
            "error",
        )
        return redirect(request.url)

    def cell(row, key):
        """Valor de una columna por nombre normalizado (case/espacios)."""
        original = headers.get(key)
        if original is None:
            return ""
        return (row.get(original) or "").strip()

    seen_codes = set()  # duplicados DENTRO del mismo CSV (antes del loop)

    # 5) Procesar filas. Preparamos todos los items validos; un unico commit.
    for row in reader:
        try:
            code = cell(row, "code")
            name = cell(row, "name")
            description = cell(row, "description") or None
            category_name = cell(row, "category")
            ref_link = cell(row, "reference_link") or None  # leido; ver nota

            if not code or not name:
                skipped += 1
                continue

            # Duplicado interno del CSV.
            if code.lower() in seen_codes:
                skipped += 1
                continue

            # Ya existe en la base (import es solo ALTAS).
            if Item.query.filter_by(code=code).first():
                skipped += 1
                continue

            # Categoria: debe existir. No se crea automaticamente.
            category = Category.query.filter_by(name=category_name).first()
            if not category:
                errors += 1
                continue

            # Booleanos con helper claro (valor no reconocido = error).
            trackable = parse_bool_cell(cell(row, "trackable"), default=False)
            is_active = parse_bool_cell(cell(row, "is_active"), default=True)
            if trackable is None or is_active is None:
                errors += 1
                continue

            # stock_min entero >= 0 (vacio = 0). Rastreable => 0.
            stock_min_raw = cell(row, "stock_min")
            if stock_min_raw == "":
                stock_min = 0
            else:
                try:
                    stock_min = int(stock_min_raw)
                except ValueError:
                    errors += 1
                    continue
                if stock_min < 0:
                    errors += 1
                    continue
            if trackable:
                stock_min = 0

            item = Item(
                code=code,
                name=name,
                description=description,
                category_id=category.id,
                trackable=trackable,
                stock_min=stock_min,
                is_active=is_active,
                reference_link=ref_link[:500] if ref_link else None,
            )

            db.session.add(item)
            seen_codes.add(code.lower())
            created += 1

        except Exception:
            db.session.rollback()
            errors += 1

    # 6) Commit unico. Si falla, rollback y contadores honestos.
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        flash(
            f"No se confirmó la importación (error al guardar): {exc}. "
            "No se creó ningún item.",
            "error",
        )
        return redirect(request.url)

    flash(
        f"Importación completa. Creados: {created}, Saltados: {skipped}, Errores: {errors}.",
        "ok",
    )
    return redirect(url_for("items"))

@app.route("/admin/clear-stock", methods=["POST"])
@login_required
@role_required("ADMIN")
def admin_clear_stock():
    username = getattr(current_user, "username", "?")

    confirm_text = (request.form.get("confirm_text", "") or "").strip()
    if confirm_text != CONFIRM_CLEAR_STOCK:
        _log_destructive("clear_stock", username, "REJECTED", "confirm_text invalido")
        flash(
            f"Confirmación inválida. Tenés que escribir exactamente '{CONFIRM_CLEAR_STOCK}'.",
            "error",
        )
        return redirect(url_for("admin_panel"))

    try:
        backup_path = _backup_db("clear_stock")
    except Exception as exc:
        _log_destructive("clear_stock", username, "ABORT_NO_BACKUP", str(exc))
        flash(f"No se pudo crear backup previo. Operación abortada: {exc}", "error")
        return redirect(url_for("admin_panel"))

    try:
        PendingDelivery.query.delete()
        RemitoLine.query.delete()
        Remito.query.delete()
        Movement.query.delete()
        Stock.query.delete()

        db.session.commit()
        _log_destructive("clear_stock", username, "OK", f"backup={backup_path.name}")
        flash(
            f"Stock, movimientos, remitos y pendientes eliminados. "
            f"Items, categorías, usuarios y ubicaciones conservados. "
            f"Backup previo: {backup_path.name}",
            "ok",
        )
    except Exception as e:
        db.session.rollback()
        _log_destructive("clear_stock", username, "ERROR", str(e))
        flash(f"No se pudo limpiar stock: {e}", "error")

    return redirect(url_for("admin_panel"))

@app.route("/admin/clear-items", methods=["POST"])
@login_required
@role_required("ADMIN")
def admin_clear_items():
    username = getattr(current_user, "username", "?")

    confirm_text = (request.form.get("confirm_text", "") or "").strip()
    if confirm_text != CONFIRM_CLEAR_ITEMS:
        _log_destructive("clear_items", username, "REJECTED", "confirm_text invalido")
        flash(
            f"Confirmación inválida. Tenés que escribir exactamente '{CONFIRM_CLEAR_ITEMS}'.",
            "error",
        )
        return redirect(url_for("admin_panel"))

    try:
        backup_path = _backup_db("clear_items")
    except Exception as exc:
        _log_destructive("clear_items", username, "ABORT_NO_BACKUP", str(exc))
        flash(f"No se pudo crear backup previo. Operación abortada: {exc}", "error")
        return redirect(url_for("admin_panel"))

    try:
        PendingDelivery.query.delete()
        RemitoLine.query.delete()
        Remito.query.delete()
        Movement.query.delete()
        Stock.query.delete()
        Item.query.delete()

        db.session.commit()
        _log_destructive("clear_items", username, "OK", f"backup={backup_path.name}")
        flash(
            f"Items, stock, movimientos, remitos y pendientes eliminados. "
            f"Categorías, usuarios y ubicaciones conservados. "
            f"Backup previo: {backup_path.name}",
            "ok",
        )
    except Exception as e:
        db.session.rollback()
        _log_destructive("clear_items", username, "ERROR", str(e))
        flash(f"No se pudo limpiar items: {e}", "error")

    return redirect(url_for("admin_panel"))

@app.route("/scrap", methods=["GET", "POST"])
@login_required
@role_required("ADMIN", "SUPERVISOR")
def scrap_report():
    descartes_loc = Location.query.filter_by(name=LOCATION_DESCARTES).first()

    if request.method == "POST":
        item_id = request.form.get("item_id", "").strip()
        qty_raw = request.form.get("qty", "").strip()
        from_id_raw = request.form.get("from_location_id", "").strip()
        reason = request.form.get("scrap_reason", "").strip()
        observation = request.form.get("observation", "").strip() or None

        if not item_id.isdigit():
            flash("Item obligatorio.", "error")
            return redirect(url_for("scrap_report"))
        try:
            qty = int(qty_raw)
            if qty <= 0:
                raise ValueError()
        except Exception:
            flash("Cantidad inválida.", "error")
            return redirect(url_for("scrap_report"))
        if not from_id_raw.isdigit():
            flash("Ubicación origen obligatoria.", "error")
            return redirect(url_for("scrap_report"))
        if not reason:
            flash("Motivo de descarte obligatorio.", "error")
            return redirect(url_for("scrap_report"))
        if not descartes_loc:
            flash("Ubicación 'Descartes' no existe.", "error")
            return redirect(url_for("scrap_report"))

        item_id = int(item_id)
        from_location_id = int(from_id_raw)

        try:
            item = Item.query.get(item_id)
            if not item or not item.is_active:
                flash("Item no existe o está dado de baja.", "error")
                return redirect(url_for("scrap_report"))

            # Serializado: resolver qué seriales se descartan (auto/elegir).
            serial_units = []
            if item.serialized:
                serial_units, err = resolve_serial_units_out(item_id, from_location_id, qty, request.form)
                if err:
                    flash(err, "error")
                    return redirect(url_for("scrap_report"))

            if not location_is_external(from_location_id):
                upsert_stock(item_id, from_location_id, -qty)
            if not location_is_external(descartes_loc.id):
                upsert_stock(item_id, descartes_loc.id, qty)

            base_obs = observation or ("Descarte: " + reason)
            if item.serialized:
                base_obs = serial_obs(base_obs, [u.serial for u in serial_units])

            y, seq, number = next_movement_number()
            m = Movement(
                item_id=item_id,
                qty=qty,
                from_location_id=from_location_id,
                to_location_id=descartes_loc.id,
                user_id=current_user.id,
                observation=base_obs,
                year=y,
                seq=seq,
                number=number,
            )
            db.session.add(m)
            db.session.flush()
            if item.serialized:
                apply_serial_units_out(serial_units, descartes_loc.id)
            db.session.add(Scrap(
                item_id=item_id,
                location_id=from_location_id,
                quantity=qty,
                reason=reason,
                user_id=current_user.id,
            ))
            db.session.commit()
            flash(f"Descarte registrado. Movimiento {number}.", "ok")
            return redirect(url_for("scrap_report"))
        except ValueError as e:
            db.session.rollback()
            flash(f"Error de stock: {e}", "error")
            return redirect(url_for("scrap_report"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "error")
            return redirect(url_for("scrap_report"))

    # Filtros del historial (desde/hasta/item/responsable/mostrar + motivo)
    reason_filter = request.args.get("reason", "").strip()
    f_date_from = request.args.get("from_date", "").strip()
    f_date_to = request.args.get("to_date", "").strip()
    f_item = (request.args.get("item_id") or request.args.get("item_filter") or "").strip()
    f_user = (request.args.get("user_id") or request.args.get("user_filter") or "").strip()
    f_limit_raw = request.args.get("limit", "").strip()
    f_limit = int(f_limit_raw) if f_limit_raw.isdigit() else 500
    f_limit = max(1, min(f_limit, 5000))

    q = Scrap.query
    if reason_filter:
        q = q.filter_by(reason=reason_filter)
    if f_date_from:
        q = q.filter(Scrap.created_at >= datetime.fromisoformat(f_date_from))
    if f_date_to:
        q = q.filter(Scrap.created_at <= datetime.fromisoformat(f_date_to + "T23:59:59"))
    if f_item.isdigit():
        q = q.filter(Scrap.item_id == int(f_item))
    if f_user.isdigit():
        q = q.filter(Scrap.user_id == int(f_user))
    scraps = q.order_by(Scrap.created_at.desc()).limit(f_limit).all()

    # Datos para el formulario de descarte + filtrado dinámico por ubicación
    # Origen: camionetas + la Jaula central (se descartan items desde ahi tambien).
    form_locations = (
        Location.query.filter(
            db.or_(Location.is_truck == True, Location.name == LOCATION_JAULA_TNG)
        )
        .order_by(Location.name)
        .all()
    )
    items_all = Item.query.filter_by(is_active=True).order_by(Item.code).all()
    users_list = User.query.order_by(User.username).all()
    stock_map = {}
    for s in Stock.query.filter(Stock.quantity > 0).all():
        stock_map.setdefault(s.location_id, []).append(s.item_id)

    units_map, serialized_item_ids = build_units_map()

    return render_template(
        "scrap_report.html",
        scraps=scraps,
        reason_filter=reason_filter,
        form_locations=form_locations,
        items=items_all,
        stock_map=stock_map,
        units_map=units_map,
        serialized_item_ids=serialized_item_ids,
        users=users_list,
        from_date=f_date_from,
        to_date=f_date_to,
        item_filter=f_item,
        user_filter=f_user,
        limit=str(f_limit),
    )


@app.route("/reparaciones", methods=["GET", "POST"])
@login_required
@role_required("ADMIN", "SUPERVISOR", "LECTOR")
def reparaciones():
    """Mesa de reparaciones. Items que entraron via 'A reparación' desde el
    cierre de un pendiente quedan EN_REPARACION en la ubicacion interna
    'En reparación'. Aca se resuelven:
      - REPARADO   -> movimiento a Jaula TNG (vuelve a stock)
      - DESCARTADO -> movimiento a Descartes + registro Scrap (con motivo)
    """
    repair_loc = Location.query.filter_by(name=LOCATION_EN_REPARACION).first()

    if request.method == "POST":
        # LECTOR: solo lectura. No puede resolver reparaciones.
        if current_user.role == "LECTOR":
            flash("No tenés permisos para resolver reparaciones.", "error")
            return redirect(url_for("reparaciones"))
        repair_id = request.form.get("repair_id", "").strip()
        action = request.form.get("repair_action", "").strip()  # reparado / descartado
        reason = request.form.get("scrap_reason", "").strip()
        observation = request.form.get("observation", "").strip() or None

        if not repair_id.isdigit():
            flash("Reparación inválida.", "error")
            return redirect(url_for("reparaciones"))

        r = Repair.query.get(int(repair_id))
        if not r:
            flash("Reparación no encontrada.", "error")
            return redirect(url_for("reparaciones"))
        if r.status != "EN_REPARACION":
            flash("Esa reparación ya fue resuelta.", "ok")
            return redirect(url_for("reparaciones"))
        if not repair_loc:
            flash("Ubicación 'En reparación' no existe.", "error")
            return redirect(url_for("reparaciones"))

        item_id = r.item_id
        qty = r.quantity
        from_id = repair_loc.id

        # Guarda serializados: la reparación por serial aún no está modelada.
        _rep_item = Item.query.get(item_id)
        if _rep_item and _rep_item.serialized:
            flash(
                "El ítem es serializado: resolvé la reparación del serial desde "
                "Movimientos (esta pantalla aún no maneja seriales).",
                "error",
            )
            return redirect(url_for("reparaciones"))

        if action == "reparado":
            dest = Location.query.filter_by(name=LOCATION_JAULA_TNG).first()
            if not dest:
                flash("Ubicación 'Jaula TNG' no existe.", "error")
                return redirect(url_for("reparaciones"))
            to_id = dest.id
        elif action == "descartado":
            if not reason:
                flash("Motivo de descarte obligatorio.", "error")
                return redirect(url_for("reparaciones"))
            dest = Location.query.filter_by(name=LOCATION_DESCARTES).first()
            if not dest:
                flash("Ubicación 'Descartes' no existe.", "error")
                return redirect(url_for("reparaciones"))
            to_id = dest.id
        else:
            flash("Acción inválida.", "error")
            return redirect(url_for("reparaciones"))

        try:
            if not location_is_external(from_id):
                upsert_stock(item_id, from_id, -qty)
            if not location_is_external(to_id):
                upsert_stock(item_id, to_id, qty)

            if action == "reparado":
                obs = observation or f"Reparado (rep #{r.id}) -> {LOCATION_JAULA_TNG}"
            else:
                obs = observation or f"Descarte post-reparación ({reason}) (rep #{r.id})"

            y, seq, number = next_movement_number()
            m = Movement(
                item_id=item_id,
                qty=qty,
                from_location_id=from_id,
                to_location_id=to_id,
                user_id=current_user.id,
                observation=obs,
                year=y,
                seq=seq,
                number=number,
            )
            db.session.add(m)
            db.session.flush()

            if action == "descartado":
                db.session.add(Scrap(
                    item_id=item_id,
                    location_id=from_id,
                    quantity=qty,
                    reason=reason,
                    user_id=current_user.id,
                ))

            r.status = "REPARADO" if action == "reparado" else "DESCARTADO"
            r.resolved_at = now_ar()
            r.resolved_by_user_id = current_user.id
            if action == "descartado":
                r.result_reason = reason
            db.session.commit()
            flash(f"Reparación cerrada ({number}). Resultado: {r.status.title()}.", "ok")
        except ValueError as e:
            db.session.rollback()
            flash(f"Error de stock: {e}", "error")
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "error")
        return redirect(url_for("reparaciones"))

    # GET: en reparacion (pendientes) + historial resuelto con filtros
    pendientes = (
        Repair.query.filter_by(status="EN_REPARACION")
        .order_by(Repair.created_at.asc())
        .all()
    )

    f_date_from = request.args.get("from_date", "").strip()
    f_date_to = request.args.get("to_date", "").strip()
    f_status = (request.args.get("status") or request.args.get("status_filter") or "").strip()  # REPARADO / DESCARTADO
    f_item = (request.args.get("item_id") or request.args.get("item_filter") or "").strip()
    f_limit_raw = request.args.get("limit", "").strip()
    f_limit = int(f_limit_raw) if f_limit_raw.isdigit() else 500
    f_limit = max(1, min(f_limit, 5000))

    q = Repair.query.filter(Repair.status != "EN_REPARACION")
    if f_status in ("REPARADO", "DESCARTADO"):
        q = q.filter(Repair.status == f_status)
    if f_date_from:
        q = q.filter(Repair.resolved_at >= datetime.fromisoformat(f_date_from))
    if f_date_to:
        q = q.filter(Repair.resolved_at <= datetime.fromisoformat(f_date_to + "T23:59:59"))
    if f_item.isdigit():
        q = q.filter(Repair.item_id == int(f_item))
    historial = q.order_by(Repair.resolved_at.desc()).limit(f_limit).all()

    # Resumen del mes en curso
    now = now_ar()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    reparados_mes = Repair.query.filter(
        Repair.status == "REPARADO", Repair.resolved_at >= month_start
    ).count()
    descartados_mes = Repair.query.filter(
        Repair.status == "DESCARTADO", Repair.resolved_at >= month_start
    ).count()

    items_all = Item.query.filter_by(is_active=True).order_by(Item.code).all()

    return render_template(
        "reparaciones.html",
        pendientes=pendientes,
        historial=historial,
        items=items_all,
        reparados_mes=reparados_mes,
        descartados_mes=descartados_mes,
        from_date=f_date_from,
        to_date=f_date_to,
        status_filter=f_status,
        item_filter=f_item,
        limit=str(f_limit),
    )


@app.route("/stock-alerts", methods=["GET"])
@login_required
@role_required("ADMIN", "SUPERVISOR")
def stock_alerts():
    loc_filter = request.args.get("location_id", "").strip()
    item_filter = request.args.get("item_id", "").strip()
    cat_filter = request.args.get("category_id", "").strip()
    sort_by = request.args.get("sort_by", "code").strip()
    sort_dir = request.args.get("sort_dir", "asc").strip().lower()

    q = (
        db.session.query(Item, Stock)
        .join(Stock, Item.id == Stock.item_id)
        # LEFT OUTER JOIN: no descartar items en alerta cuya categoria sea NULL o
        # inexistente. Antes era INNER JOIN y esos items desaparecian de Alertas
        # (aunque si aparecian en Solicitud de compra y en el badge).
        .outerjoin(Category, Category.id == Item.category_id)
        .filter(
            Item.is_active == True,
            Item.trackable == False,
            Item.stock_min > 0,
        )
    )
    # Restriccion por responsabilidad: si el usuario es responsable de una o mas
    # ubicaciones, solo ve alertas de ESAS ubicaciones. Sin ubicaciones asignadas
    # ve todo (comportamiento previo para supervisores generales).
    resp_ids = current_user_responsible_location_ids()
    if resp_ids:
        q = q.filter(Stock.location_id.in_(resp_ids))
    if loc_filter.isdigit():
        q = q.filter(Stock.location_id == int(loc_filter))
    if item_filter.isdigit():
        q = q.filter(Item.id == int(item_filter))
    if cat_filter.isdigit():
        q = q.filter(Item.category_id == int(cat_filter))

    rows = q.order_by(Item.code).all()

    # Ítems ya solicitados: derivado de solicitudes de compra REALIZADAS.
    requested_map = requested_numbers_map()

    alerts = []
    for item, stock in rows:
        # Única fuente de verdad: is_alert_stock (nivel rojo/amarillo).
        if not is_alert_stock(item, stock.quantity):
            continue
        level_class = stock_level_class(item, stock.quantity)
        alerts.append({
            "item": item,
            "stock": stock,
            "level": level_class,
            "level_name": "Critico" if level_class == "stock-red" else "Bajo",
            "requested": item.id in requested_map,
            "requested_numbers": requested_map.get(item.id, ""),
        })

    # Marcar como "vistas" las alertas actuales (para el puntito de novedades).
    try:
        session["alerts_seen"] = [e["item"].id for e in alert_items_distinct()]
    except Exception:
        pass

    # Ordenamiento (algunos campos son calculados, se ordena la lista final)
    keyfns = {
        "location": lambda a: (a["stock"].location.name or "").lower(),
        "code": lambda a: (a["item"].code or "").lower(),
        "name": lambda a: (a["item"].name or "").lower(),
        "category": lambda a: ((a["item"].category.name if a["item"].category else "") or "").lower(),
        "quantity": lambda a: a["stock"].quantity,
    }
    keyfn = keyfns.get(sort_by, keyfns["code"])
    alerts.sort(key=keyfn, reverse=(sort_dir == "desc"))

    locations_list = Location.query.order_by(Location.name).all()
    items_list = Item.query.filter_by(is_active=True).order_by(Item.code).all()
    categories_list = Category.query.order_by(Category.name).all()

    return render_template(
        "stock_alerts.html",
        alerts=alerts,
        locations=locations_list,
        items=items_list,
        categories=categories_list,
        selected_location_id=loc_filter,
        selected_item_id=item_filter,
        selected_category_id=cat_filter,
        selected_sort_by=sort_by,
        selected_sort_dir=sort_dir,
    )


# ------------------ SOLICITUDES DE COMPRA ------------------

# Destinatarios precargados del mail de solicitud de compra.
# Por ahora el envio es MANUAL (se copia/pega). A futuro se automatiza.
def selectable_recipient_users():
    """Usuarios que pueden elegirse como destinatarios de una solicitud de compra.

    Criterio: usuarios con email cargado. Se ordenan poniendo primero a los de
    tipo PROVEEDOR y despues por nombre. Reemplaza la lista de mails hardcodeada.
    """
    return (
        User.query
        .filter(User.email.isnot(None), User.email != "")
        .order_by((User.role != "PROVEEDOR"), User.full_name, User.username)
        .all()
    )


def alert_items_distinct():
    """Ítems en alerta (rojo/amarillo), únicos por ítem, sin importar ubicación.

    Reusa exactamente el mismo criterio que la vista de Alertas de Stock: ítems
    activos, NO rastreables, con stock_min > 0 y al menos una ubicación
    clasificada en rojo o amarillo según stock_level_class(). Como para comprar
    no importa la ubicación, agregamos el stock total del ítem (todas las filas
    visibles/permitidas) y nos quedamos con el peor nivel entre sus ubicaciones.
    """
    q = (
        db.session.query(Item, Stock)
        .join(Stock, Item.id == Stock.item_id)
        .filter(
            Item.is_active == True,
            Item.trackable == False,
            Item.stock_min > 0,
        )
    )
    # Mismo criterio de responsabilidad que la vista de Alertas: si el usuario es
    # responsable de ubicaciones, solo cuentan esas (afecta el puntito de
    # novedades y los items disponibles para solicitud de compra).
    resp_ids = current_user_responsible_location_ids()
    if resp_ids:
        q = q.filter(Stock.location_id.in_(resp_ids))
    rows = q.all()

    # Agrupamos por item. total_qty suma TODAS las filas visibles/permitidas del
    # item (incluidas las verdes); worst = peor nivel entre sus ubicaciones.
    by_item: dict[int, dict] = {}
    for item, stock in rows:
        level = stock_level_class(item, stock.quantity)
        entry = by_item.get(item.id)
        if entry is None:
            entry = by_item[item.id] = {
                "item": item,
                "total_qty": 0,
                "worst": "",
                "_has_red": False,
                "_has_yellow": False,
            }
        entry["total_qty"] += stock.quantity
        if level == "stock-red":
            entry["_has_red"] = True
        elif level == "stock-yellow":
            entry["_has_yellow"] = True

    # Nos quedamos solo con items que tengan al menos una ubicacion en alerta.
    result = []
    for entry in by_item.values():
        if entry["_has_red"]:
            entry["worst"] = "stock-red"
        elif entry["_has_yellow"]:
            entry["worst"] = "stock-yellow"
        else:
            continue  # ninguna ubicacion en alerta -> excluir (verde)
        entry.pop("_has_red", None)
        entry.pop("_has_yellow", None)
        result.append(entry)

    result.sort(key=lambda e: (e["item"].code or "").lower())
    return result


def requested_item_ids_set() -> set[int]:
    """IDs de ítems presentes en solicitudes de compra REALIZADAS."""
    rows = (
        db.session.query(PurchaseRequestLine.item_id)
        .join(PurchaseRequest, PurchaseRequest.id == PurchaseRequestLine.purchase_request_id)
        .filter(PurchaseRequest.status == "REALIZADA")
        .distinct()
        .all()
    )
    return {r[0] for r in rows}


def requested_numbers_map() -> dict:
    """Map item_id -> "SC-.., SC-.." de las solicitudes REALIZADAS que lo incluyen."""
    rows = (
        db.session.query(PurchaseRequestLine.item_id, PurchaseRequest.number)
        .join(PurchaseRequest, PurchaseRequest.id == PurchaseRequestLine.purchase_request_id)
        .filter(PurchaseRequest.status == "REALIZADA")
        .order_by(PurchaseRequest.number)
        .all()
    )
    out: dict[int, list] = {}
    for item_id, number in rows:
        out.setdefault(item_id, [])
        if number not in out[item_id]:
            out[item_id].append(number)
    return {k: ", ".join(v) for k, v in out.items()}


def build_purchase_request_email(pr: PurchaseRequest) -> dict:
    """Arma asunto + cuerpo de texto del mail para copiar/pegar."""
    subject = f"Solicitud de compra {pr.number}"

    # Firma: primer nombre del creador (ej. "Ignacio").
    full = (pr.created_by.full_name or "").strip() if pr.created_by else ""
    firma = full.split()[0] if full else "Ignacio"

    lines_txt = []
    for ln in pr.lines:
        it = ln.item
        name = it.name if it else "?"
        spec = f" ({ln.spec})" if ln.spec else ""
        lines_txt.append(f"* {ln.qty} {name}{spec}")

    body = (
        "Buenas,\n"
        "Solicito avanzar con la compra de los siguientes insumos:\n\n"
        + "\n".join(lines_txt)
        + "\n\n"
        "Estos materiales serán utilizados para tareas de armado, "
        "instalación y mantenimiento de equipos.\n"
        "Por favor, avanzar con la cotización y gestión de compra correspondiente.\n\n"
        "Gracias.\n\n"
        "Saludos,\n"
        f"{firma}.\n"
    )
    # Destinatarios: mails de los usuarios seleccionados al crear la solicitud.
    # Si no se selecciono ninguno, "to" queda vacio (envio manual).
    to_list = [
        r.user.email
        for r in pr.recipients
        if r.user and (r.user.email or "").strip()
    ]
    return {
        "to": ", ".join(to_list),
        "subject": subject,
        "body": body,
    }


@app.route("/solicitudes-compra", methods=["GET"])
@login_required
@role_required("ADMIN", "SUPERVISOR", "LECTOR")
def purchase_requests():
    reqs = (
        PurchaseRequest.query.order_by(PurchaseRequest.created_at.desc())
        .limit(500)
        .all()
    )
    alert_items = alert_items_distinct()
    return render_template(
        "purchase_requests.html",
        requests=reqs,
        alert_items=alert_items,
        recipient_users=selectable_recipient_users(),
    )


@app.route("/solicitudes-compra/new", methods=["POST"])
@login_required
@role_required("ADMIN", "SUPERVISOR")
def purchase_request_new():
    # Ítems válidos = los que hoy están en alerta (no se puede pedir otra cosa).
    valid_ids = {e["item"].id for e in alert_items_distinct()}

    selected = request.form.getlist("item_id")
    lines_to_create = []
    for sid in selected:
        if not sid.isdigit():
            continue
        iid = int(sid)
        if iid not in valid_ids:
            continue
        qty_raw = request.form.get(f"qty_{iid}", "").strip()
        try:
            qty = int(qty_raw)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        lines_to_create.append((iid, qty))

    if not lines_to_create:
        flash("Seleccioná al menos un ítem con cantidad mayor a 0.", "error")
        return redirect(url_for("purchase_requests"))

    observation = (request.form.get("observation", "") or "").strip() or None

    try:
        y, seq, number = next_purchase_request_number()
        pr = PurchaseRequest(
            year=y,
            seq=seq,
            number=number,
            status="PENDIENTE",
            created_by_user_id=current_user.id,
            observation=observation,
        )
        db.session.add(pr)
        db.session.flush()  # para tener pr.id
        for iid, qty in lines_to_create:
            db.session.add(
                PurchaseRequestLine(
                    purchase_request_id=pr.id,
                    item_id=iid,
                    qty=qty,
                )
            )

        # Destinatarios seleccionados (usuarios con email). Solo se guardan los
        # validos y con mail; se ignoran duplicados y ausentes.
        valid_recipient_ids = {u.id for u in selectable_recipient_users()}
        seen_recipients = set()
        for rid in request.form.getlist("recipient_id"):
            if not rid.isdigit():
                continue
            uid = int(rid)
            if uid in seen_recipients or uid not in valid_recipient_ids:
                continue
            db.session.add(
                PurchaseRequestRecipient(purchase_request_id=pr.id, user_id=uid)
            )
            seen_recipients.add(uid)

        db.session.commit()
        flash(f"Solicitud de compra creada: {number}", "ok")
        return redirect(url_for("purchase_request_detail", pr_id=pr.id))
    except Exception as e:
        db.session.rollback()
        flash(f"No se pudo crear la solicitud: {e}", "error")
        return redirect(url_for("purchase_requests"))


@app.route("/solicitudes-compra/<int:pr_id>", methods=["GET"])
@login_required
@role_required("ADMIN", "SUPERVISOR", "LECTOR")
def purchase_request_detail(pr_id: int):
    pr = PurchaseRequest.query.get_or_404(pr_id)
    email = build_purchase_request_email(pr)
    embed = request.args.get("embed") == "1"
    return render_template(
        "purchase_request_detail.html",
        pr=pr,
        email=email,
        embed=embed,
    )


@app.route("/solicitudes-compra/<int:pr_id>/realizar", methods=["POST"])
@login_required
@role_required("ADMIN", "SUPERVISOR")
def purchase_request_mark_done(pr_id: int):
    pr = PurchaseRequest.query.get_or_404(pr_id)
    if pr.status != "REALIZADA":
        pr.status = "REALIZADA"
        pr.sent_at = now_ar()
        db.session.commit()
        flash(f"Solicitud {pr.number} marcada como realizada.", "ok")
    return redirect(url_for("purchase_request_detail", pr_id=pr.id))


# ================================================================
#  MÉTRICAS  (sección solo-lectura para dirección)
#  Bloque AISLADO y ADITIVO. No modifica modelos, esquema ni datos.
#  Todas las consultas son SELECT. Roles: ADMIN / SUPERVISOR.
#  Prefijo _mx_ / metricas_ para no colisionar con nombres existentes.
# ================================================================

# Ubicaciones "de sistema" (no operativas): se excluyen de las vistas por
# ubicación/camioneta. El resto se considera ubicación operativa.
_MX_SYSTEM_LOCS = [
    LOCATION_PROVEEDOR, LOCATION_DESCARTES, LOCATION_RECUPERADO,
    "Utilizado", LOCATION_EN_REPARACION,
]

_MX_PRESETS = [
    ("mes", "Mes actual"),
    ("30d", "Últimos 30 días"),
    ("90d", "Últimos 90 días"),
    ("anio", "Año actual"),
    ("todo", "Todo"),
]


def _mx_period():
    """Resuelve el rango de fechas desde request.args.

    Prioridad: si vienen desde/hasta explícitos, se usan; si no, se aplica el
    preset (default 90d). Devuelve un dict con datetime de inicio/fin y strings
    para el formulario. Solo lectura.
    """
    now = now_ar()
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    desde_raw = (request.args.get("from_date") or request.args.get("desde") or "").strip()
    hasta_raw = (request.args.get("to_date") or request.args.get("hasta") or "").strip()
    preset = (request.args.get("preset") or "").strip()

    dt_from = dt_to = None
    # Rango custom por fechas explícitas.
    if desde_raw or hasta_raw:
        preset = ""
        try:
            if desde_raw:
                dt_from = datetime.fromisoformat(desde_raw)
        except Exception:
            dt_from = None
        try:
            if hasta_raw:
                dt_to = datetime.fromisoformat(hasta_raw + "T23:59:59")
        except Exception:
            dt_to = None

    if dt_from is None and dt_to is None:
        if not preset:
            preset = "90d"
        if preset == "mes":
            dt_from = today0.replace(day=1)
        elif preset == "30d":
            dt_from = today0 - timedelta(days=30)
        elif preset == "anio":
            dt_from = today0.replace(month=1, day=1)
        elif preset == "todo":
            first_mov = db.session.query(func.min(Movement.created_at)).scalar()
            dt_from = first_mov if isinstance(first_mov, datetime) else (today0 - timedelta(days=365))
        else:  # 90d
            preset = "90d"
            dt_from = today0 - timedelta(days=90)
        dt_to = now

    if dt_from is None:
        dt_from = today0 - timedelta(days=90)
    if dt_to is None:
        dt_to = now

    def _fmt(d):
        return d.strftime("%d/%m/%Y") if isinstance(d, datetime) else ""

    return {
        "dt_from": dt_from,
        "dt_to": dt_to,
        "desde": dt_from.strftime("%Y-%m-%d"),
        "hasta": dt_to.strftime("%Y-%m-%d"),
        "preset": preset,
        "presets": _MX_PRESETS,
        "range_label": f"{_fmt(dt_from)} – {_fmt(dt_to)}",
    }


def _mx_rows(pairs, tone="", badges=None):
    """Convierte [(label, value)] en filas para el macro hbars (calcula el máximo)."""
    vals = [int(v or 0) for _, v in pairs]
    mx = max(vals) if vals else 0
    out = []
    for (lbl, val) in pairs:
        out.append({
            "label": lbl,
            "value": int(val or 0),
            "max": mx,
            "tone": tone,
            "badge": (badges.get(lbl) if badges else None),
        })
    return out


def _mx_months(dt_from, dt_to, cap=12):
    """Lista de meses (label, inicio, fin_exclusivo) dentro del rango, máx `cap`."""
    end = dt_to
    # Empezar a lo sumo `cap` meses antes del fin.
    y, m = end.year, end.month
    months = []
    yy, mm = y, m
    for _ in range(cap):
        start = datetime(yy, mm, 1)
        if mm == 12:
            nxt = datetime(yy + 1, 1, 1)
        else:
            nxt = datetime(yy, mm + 1, 1)
        months.append((start.strftime("%m/%y"), start, nxt))
        if start <= dt_from:
            break
        mm -= 1
        if mm == 0:
            mm = 12
            yy -= 1
    months.reverse()
    return months


def _mx_operational_locations():
    return (
        Location.query.filter(~Location.name.in_(_MX_SYSTEM_LOCS))
        .order_by(Location.name)
        .all()
    )


def _mx_utilizado_id():
    loc = Location.query.filter_by(name="Utilizado").first()
    return loc.id if loc else None


def _mx_snapshot():
    """KPIs de foto actual (no dependen del período)."""
    stock_total = db.session.query(func.coalesce(func.sum(Stock.quantity), 0)).filter(Stock.quantity > 0).scalar() or 0
    try:
        alertas = len(alert_items_distinct())
    except Exception:
        alertas = 0
    pendientes = PendingDelivery.query.filter_by(returned=False).count()
    reparando = Repair.query.filter_by(status="EN_REPARACION").count()
    items_activos = Item.query.filter_by(is_active=True).count()
    return {
        "stock_total": int(stock_total),
        "alertas": alertas,
        "pendientes": pendientes,
        "reparando": reparando,
        "items_activos": items_activos,
    }


# ---------- HUB / RESUMEN ----------
@app.route("/metricas")
@login_required
@role_required("ADMIN", "SUPERVISOR", "LECTOR")
def metricas():
    p = _mx_period()
    dfrom, dto = p["dt_from"], p["dt_to"]
    util_id = _mx_utilizado_id()

    movimientos = Movement.query.filter(Movement.created_at >= dfrom, Movement.created_at <= dto).count()
    consumo = 0
    if util_id:
        consumo = db.session.query(func.coalesce(func.sum(Movement.qty), 0)).filter(
            Movement.to_location_id == util_id,
            Movement.created_at >= dfrom, Movement.created_at <= dto,
        ).scalar() or 0
    descartes = db.session.query(func.coalesce(func.sum(Scrap.quantity), 0)).filter(
        Scrap.created_at >= dfrom, Scrap.created_at <= dto,
    ).scalar() or 0
    solicitudes = PurchaseRequest.query.filter(
        PurchaseRequest.created_at >= dfrom, PurchaseRequest.created_at <= dto,
    ).count()

    snap = _mx_snapshot()
    cards = [
        {"label": "Movimientos", "value": movimientos, "hint": "en el período", "tone": "accent", "href": url_for("metricas_movimientos")},
        {"label": "Consumo (unidades)", "value": int(consumo), "hint": "utilizadas en el período", "tone": "", "href": url_for("metricas_consumo")},
        {"label": "Descartes (unidades)", "value": int(descartes), "hint": "en el período", "tone": "danger", "href": url_for("metricas_descartes")},
        {"label": "Solicitudes de compra", "value": solicitudes, "hint": "creadas en el período", "tone": "", "href": url_for("metricas_compras")},
        {"label": "Stock actual", "value": snap["stock_total"], "hint": f"{snap['items_activos']} ítems activos", "tone": "accent", "href": url_for("metricas_inventario")},
        {"label": "Ítems en alerta", "value": snap["alertas"], "hint": "bajo mínimo (foto actual)", "tone": "warn", "href": url_for("metricas_inventario")},
        {"label": "Pendientes abiertos", "value": snap["pendientes"], "hint": "sin devolución (foto actual)", "tone": "danger", "href": url_for("metricas_reparaciones")},
        {"label": "En reparación", "value": snap["reparando"], "hint": "abiertas (foto actual)", "tone": "warn", "href": url_for("metricas_reparaciones")},
    ]

    # Tendencia de consumo mensual
    trend = []
    if util_id:
        for lbl, ms, me in _mx_months(dfrom, dto):
            v = db.session.query(func.coalesce(func.sum(Movement.qty), 0)).filter(
                Movement.to_location_id == util_id,
                Movement.created_at >= ms, Movement.created_at < me,
            ).scalar() or 0
            trend.append((lbl, int(v)))
    trend_max = max([v for _, v in trend], default=0)
    trend_rows = [{"label": l, "value": v, "max": trend_max} for l, v in trend]

    # Top ítems consumidos
    top_consumo = []
    if util_id:
        rows = (
            db.session.query(Item, func.sum(Movement.qty))
            .join(Movement, Movement.item_id == Item.id)
            .filter(Movement.to_location_id == util_id, Movement.created_at >= dfrom, Movement.created_at <= dto)
            .group_by(Item.id).order_by(func.sum(Movement.qty).desc()).limit(8).all()
        )
        top_consumo = _mx_rows([(f"{it.code} · {it.name}", tot) for it, tot in rows])

    # Descartes por camioneta/ubicación de origen
    scrap_rows = (
        db.session.query(Location, func.sum(Scrap.quantity))
        .join(Scrap, Scrap.location_id == Location.id)
        .filter(Scrap.created_at >= dfrom, Scrap.created_at <= dto)
        .group_by(Location.id).order_by(func.sum(Scrap.quantity).desc()).limit(8).all()
    )
    descartes_loc = _mx_rows(
        [(loc.name, tot) for loc, tot in scrap_rows], tone="danger",
        badges={loc.name: ("camioneta" if loc.is_truck else None) for loc, _ in scrap_rows},
    )

    return render_template(
        "metricas.html", active_tab="resumen", cards=cards,
        trend_rows=trend_rows, top_consumo=top_consumo, descartes_loc=descartes_loc, **p,
    )


# ---------- COMPRAS ----------
@app.route("/metricas/compras")
@login_required
@role_required("ADMIN", "SUPERVISOR", "LECTOR")
def metricas_compras():
    p = _mx_period()
    dfrom, dto = p["dt_from"], p["dt_to"]

    prs = PurchaseRequest.query.filter(
        PurchaseRequest.created_at >= dfrom, PurchaseRequest.created_at <= dto
    ).all()
    total = len(prs)
    realizadas = sum(1 for r in prs if r.status == "REALIZADA")
    pendientes = total - realizadas

    # Tiempo promedio de gestión (created_at -> sent_at) en días
    difs = [
        (r.sent_at - r.created_at).total_seconds() / 86400.0
        for r in prs if r.sent_at and r.created_at
    ]
    prom_gestion = round(sum(difs) / len(difs), 1) if difs else 0

    total_unidades = (
        db.session.query(func.coalesce(func.sum(PurchaseRequestLine.qty), 0))
        .join(PurchaseRequest, PurchaseRequest.id == PurchaseRequestLine.purchase_request_id)
        .filter(PurchaseRequest.created_at >= dfrom, PurchaseRequest.created_at <= dto)
        .scalar() or 0
    )

    cards = [
        {"label": "Solicitudes creadas", "value": total, "hint": "en el período", "tone": "accent"},
        {"label": "Realizadas", "value": realizadas, "hint": "enviadas al proveedor", "tone": "ok" if realizadas else ""},
        {"label": "Pendientes", "value": pendientes, "hint": "sin realizar", "tone": "warn" if pendientes else ""},
        {"label": "Unidades solicitadas", "value": int(total_unidades), "hint": "sumando renglones", "tone": ""},
        {"label": "Gestión promedio", "value": f"{prom_gestion} d", "hint": "de creada a realizada", "tone": ""},
    ]

    # Ítems más solicitados
    item_rows = (
        db.session.query(Item, func.sum(PurchaseRequestLine.qty))
        .join(PurchaseRequestLine, PurchaseRequestLine.item_id == Item.id)
        .join(PurchaseRequest, PurchaseRequest.id == PurchaseRequestLine.purchase_request_id)
        .filter(PurchaseRequest.created_at >= dfrom, PurchaseRequest.created_at <= dto)
        .group_by(Item.id).order_by(func.sum(PurchaseRequestLine.qty).desc()).limit(10).all()
    )
    top_items = _mx_rows([(f"{it.code} · {it.name}", tot) for it, tot in item_rows])

    # Categorías más solicitadas
    cat_rows = (
        db.session.query(Category.name, func.sum(PurchaseRequestLine.qty))
        .join(Item, Item.category_id == Category.id)
        .join(PurchaseRequestLine, PurchaseRequestLine.item_id == Item.id)
        .join(PurchaseRequest, PurchaseRequest.id == PurchaseRequestLine.purchase_request_id)
        .filter(PurchaseRequest.created_at >= dfrom, PurchaseRequest.created_at <= dto)
        .group_by(Category.id).order_by(func.sum(PurchaseRequestLine.qty).desc()).limit(10).all()
    )
    top_cats = _mx_rows([(name, tot) for name, tot in cat_rows], tone="ok")

    # Solicitudes por usuario creador
    user_rows = (
        db.session.query(User, func.count(PurchaseRequest.id))
        .join(PurchaseRequest, PurchaseRequest.created_by_user_id == User.id)
        .filter(PurchaseRequest.created_at >= dfrom, PurchaseRequest.created_at <= dto)
        .group_by(User.id).order_by(func.count(PurchaseRequest.id).desc()).limit(10).all()
    )
    by_user = _mx_rows([((u.full_name or u.username), c) for u, c in user_rows])

    # Tendencia mensual (cantidad de solicitudes)
    trend = []
    for lbl, ms, me in _mx_months(dfrom, dto):
        c = PurchaseRequest.query.filter(PurchaseRequest.created_at >= ms, PurchaseRequest.created_at < me).count()
        trend.append((lbl, c))
    tmax = max([v for _, v in trend], default=0)
    trend_rows = [{"label": l, "value": v, "max": tmax} for l, v in trend]

    estado_labels = ["Realizadas", "Pendientes"]
    estado_values = [realizadas, pendientes]
    return render_template(
        "metricas_compras.html", active_tab="compras", cards=cards,
        top_items=top_items, top_cats=top_cats, by_user=by_user, trend_rows=trend_rows,
        estado_labels=estado_labels, estado_values=estado_values, **p,
    )


# ---------- CONSUMO / USO ----------
@app.route("/metricas/consumo")
@login_required
@role_required("ADMIN", "SUPERVISOR", "LECTOR")
def metricas_consumo():
    p = _mx_period()
    dfrom, dto = p["dt_from"], p["dt_to"]
    util_id = _mx_utilizado_id()

    def q_consumo():
        return db.session.query(Movement).filter(
            Movement.to_location_id == util_id,
            Movement.created_at >= dfrom, Movement.created_at <= dto,
        )

    total = 0
    n_mov = 0
    if util_id:
        total = db.session.query(func.coalesce(func.sum(Movement.qty), 0)).filter(
            Movement.to_location_id == util_id,
            Movement.created_at >= dfrom, Movement.created_at <= dto,
        ).scalar() or 0
        n_mov = q_consumo().count()

    cards = [
        {"label": "Unidades utilizadas", "value": int(total), "hint": "en el período", "tone": "accent"},
        {"label": "Registros de uso", "value": n_mov, "hint": "movimientos a Utilizado", "tone": ""},
    ]

    top_items, by_cat, by_loc, by_user, trend_rows = [], [], [], [], []
    if util_id:
        rows = (
            db.session.query(Item, func.sum(Movement.qty))
            .join(Movement, Movement.item_id == Item.id)
            .filter(Movement.to_location_id == util_id, Movement.created_at >= dfrom, Movement.created_at <= dto)
            .group_by(Item.id).order_by(func.sum(Movement.qty).desc()).limit(12).all()
        )
        top_items = _mx_rows([(f"{it.code} · {it.name}", tot) for it, tot in rows])

        crows = (
            db.session.query(Category.name, func.sum(Movement.qty))
            .join(Item, Item.category_id == Category.id)
            .join(Movement, Movement.item_id == Item.id)
            .filter(Movement.to_location_id == util_id, Movement.created_at >= dfrom, Movement.created_at <= dto)
            .group_by(Category.id).order_by(func.sum(Movement.qty).desc()).limit(12).all()
        )
        by_cat = _mx_rows([(name, tot) for name, tot in crows], tone="ok")

        lrows = (
            db.session.query(Location, func.sum(Movement.qty))
            .join(Movement, Movement.from_location_id == Location.id)
            .filter(Movement.to_location_id == util_id, Movement.created_at >= dfrom, Movement.created_at <= dto)
            .group_by(Location.id).order_by(func.sum(Movement.qty).desc()).limit(12).all()
        )
        by_loc = _mx_rows(
            [(loc.name, tot) for loc, tot in lrows],
            badges={loc.name: ("camioneta" if loc.is_truck else None) for loc, _ in lrows},
        )

        urows = (
            db.session.query(User, func.sum(Movement.qty))
            .join(Movement, Movement.user_id == User.id)
            .filter(Movement.to_location_id == util_id, Movement.created_at >= dfrom, Movement.created_at <= dto)
            .group_by(User.id).order_by(func.sum(Movement.qty).desc()).limit(12).all()
        )
        by_user = _mx_rows([((u.full_name or u.username), tot) for u, tot in urows])

        for lbl, ms, me in _mx_months(dfrom, dto):
            v = db.session.query(func.coalesce(func.sum(Movement.qty), 0)).filter(
                Movement.to_location_id == util_id, Movement.created_at >= ms, Movement.created_at < me,
            ).scalar() or 0
            trend_rows.append((lbl, int(v)))
        tmax = max([v for _, v in trend_rows], default=0)
        trend_rows = [{"label": l, "value": v, "max": tmax} for l, v in trend_rows]

    # Ítems con stock sin movimiento (stock quieto) - foto actual
    hace30 = now_ar() - timedelta(days=30)
    ids_stock = {r[0] for r in db.session.query(Stock.item_id).filter(Stock.quantity > 0).distinct()}
    ids_activos = {r[0] for r in db.session.query(Item.id).filter(Item.is_active == True)}
    ids_mov = {r[0] for r in db.session.query(Movement.item_id).filter(Movement.created_at >= hace30).distinct()}
    quietos = (ids_stock & ids_activos) - ids_mov
    cards.append({"label": "Sin movimiento 30d", "value": len(quietos), "hint": "ítems con stock quieto", "tone": "warn"})

    return render_template(
        "metricas_consumo.html", active_tab="consumo", cards=cards, util_ok=bool(util_id),
        top_items=top_items, by_cat=by_cat, by_loc=by_loc, by_user=by_user, trend_rows=trend_rows, **p,
    )


# ---------- DESCARTES ----------
@app.route("/metricas/descartes")
@login_required
@role_required("ADMIN", "SUPERVISOR", "LECTOR")
def metricas_descartes():
    p = _mx_period()
    dfrom, dto = p["dt_from"], p["dt_to"]

    total = db.session.query(func.coalesce(func.sum(Scrap.quantity), 0)).filter(
        Scrap.created_at >= dfrom, Scrap.created_at <= dto).scalar() or 0
    n_reg = Scrap.query.filter(Scrap.created_at >= dfrom, Scrap.created_at <= dto).count()

    # Ratio descarte vs consumo
    util_id = _mx_utilizado_id()
    consumo = 0
    if util_id:
        consumo = db.session.query(func.coalesce(func.sum(Movement.qty), 0)).filter(
            Movement.to_location_id == util_id, Movement.created_at >= dfrom, Movement.created_at <= dto).scalar() or 0
    ratio = round((total / consumo * 100), 1) if consumo else 0

    cards = [
        {"label": "Unidades descartadas", "value": int(total), "hint": "en el período", "tone": "danger"},
        {"label": "Registros de descarte", "value": n_reg, "hint": "operaciones", "tone": ""},
        {"label": "Descarte vs consumo", "value": f"{ratio}%", "hint": "unid. descartadas / utilizadas", "tone": "warn"},
    ]

    # Por camioneta / ubicación de origen  (pedido explícito)
    lrows = (
        db.session.query(Location, func.sum(Scrap.quantity))
        .join(Scrap, Scrap.location_id == Location.id)
        .filter(Scrap.created_at >= dfrom, Scrap.created_at <= dto)
        .group_by(Location.id).order_by(func.sum(Scrap.quantity).desc()).all()
    )
    by_loc = _mx_rows(
        [(loc.name, tot) for loc, tot in lrows], tone="danger",
        badges={loc.name: ("camioneta" if loc.is_truck else None) for loc, _ in lrows},
    )

    # Por motivo
    rrows = (
        db.session.query(func.coalesce(Scrap.reason, "(sin motivo)"), func.sum(Scrap.quantity))
        .filter(Scrap.created_at >= dfrom, Scrap.created_at <= dto)
        .group_by(Scrap.reason).order_by(func.sum(Scrap.quantity).desc()).all()
    )
    by_reason = _mx_rows([(name, tot) for name, tot in rrows], tone="warn")

    # Top ítems descartados
    irows = (
        db.session.query(Item, func.sum(Scrap.quantity))
        .join(Scrap, Scrap.item_id == Item.id)
        .filter(Scrap.created_at >= dfrom, Scrap.created_at <= dto)
        .group_by(Item.id).order_by(func.sum(Scrap.quantity).desc()).limit(10).all()
    )
    top_items = _mx_rows([(f"{it.code} · {it.name}", tot) for it, tot in irows], tone="danger")

    # Por categoría
    crows = (
        db.session.query(Category.name, func.sum(Scrap.quantity))
        .join(Item, Item.category_id == Category.id)
        .join(Scrap, Scrap.item_id == Item.id)
        .filter(Scrap.created_at >= dfrom, Scrap.created_at <= dto)
        .group_by(Category.id).order_by(func.sum(Scrap.quantity).desc()).limit(10).all()
    )
    by_cat = _mx_rows([(name, tot) for name, tot in crows], tone="danger")

    # Por responsable
    urows = (
        db.session.query(User, func.sum(Scrap.quantity))
        .join(Scrap, Scrap.user_id == User.id)
        .filter(Scrap.created_at >= dfrom, Scrap.created_at <= dto)
        .group_by(User.id).order_by(func.sum(Scrap.quantity).desc()).limit(10).all()
    )
    by_user = _mx_rows([((u.full_name or u.username), tot) for u, tot in urows], tone="danger")

    # Tendencia mensual
    trend = []
    for lbl, ms, me in _mx_months(dfrom, dto):
        v = db.session.query(func.coalesce(func.sum(Scrap.quantity), 0)).filter(
            Scrap.created_at >= ms, Scrap.created_at < me).scalar() or 0
        trend.append((lbl, int(v)))
    tmax = max([v for _, v in trend], default=0)
    trend_rows = [{"label": l, "value": v, "max": tmax} for l, v in trend]

    return render_template(
        "metricas_descartes.html", active_tab="descartes", cards=cards,
        by_loc=by_loc, by_reason=by_reason, top_items=top_items, by_cat=by_cat,
        by_user=by_user, trend_rows=trend_rows, **p,
    )


# ---------- CAMIONETAS (vista consolidada por ubicación operativa) ----------
@app.route("/metricas/camionetas")
@login_required
@role_required("ADMIN", "SUPERVISOR", "LECTOR")
def metricas_camionetas():
    p = _mx_period()
    dfrom, dto = p["dt_from"], p["dt_to"]
    util_id = _mx_utilizado_id()
    locs = _mx_operational_locations()

    # Stock actual por ubicación
    stock_by_loc = dict(
        db.session.query(Stock.location_id, func.coalesce(func.sum(Stock.quantity), 0))
        .filter(Stock.quantity > 0).group_by(Stock.location_id).all()
    )
    # Consumo (salidas hacia Utilizado) por ubicación de origen
    cons_by_loc = {}
    if util_id:
        cons_by_loc = dict(
            db.session.query(Movement.from_location_id, func.coalesce(func.sum(Movement.qty), 0))
            .filter(Movement.to_location_id == util_id, Movement.created_at >= dfrom, Movement.created_at <= dto)
            .group_by(Movement.from_location_id).all()
        )
    # Descartes por ubicación de origen
    scrap_by_loc = dict(
        db.session.query(Scrap.location_id, func.coalesce(func.sum(Scrap.quantity), 0))
        .filter(Scrap.created_at >= dfrom, Scrap.created_at <= dto)
        .group_by(Scrap.location_id).all()
    )
    # Movimientos (salidas) por ubicación de origen
    mov_by_loc = dict(
        db.session.query(Movement.from_location_id, func.count(Movement.id))
        .filter(Movement.created_at >= dfrom, Movement.created_at <= dto)
        .group_by(Movement.from_location_id).all()
    )

    tabla = []
    for loc in locs:
        tabla.append({
            "name": loc.name,
            "is_truck": bool(loc.is_truck),
            "stock": int(stock_by_loc.get(loc.id, 0) or 0),
            "consumo": int(cons_by_loc.get(loc.id, 0) or 0),
            "descartes": int(scrap_by_loc.get(loc.id, 0) or 0),
            "movimientos": int(mov_by_loc.get(loc.id, 0) or 0),
        })
    # Ordenar por actividad (movimientos) desc
    tabla.sort(key=lambda x: x["movimientos"], reverse=True)

    stock_rows = _mx_rows(
        [(r["name"], r["stock"]) for r in tabla], tone="accent",
        badges={r["name"]: ("camioneta" if r["is_truck"] else None) for r in tabla},
    )
    cons_rows = _mx_rows(
        [(r["name"], r["consumo"]) for r in tabla],
        badges={r["name"]: ("camioneta" if r["is_truck"] else None) for r in tabla},
    )
    scrap_rows = _mx_rows(
        [(r["name"], r["descartes"]) for r in tabla], tone="danger",
        badges={r["name"]: ("camioneta" if r["is_truck"] else None) for r in tabla},
    )

    # Pendientes abiertos por responsable (destino)
    prows = (
        db.session.query(User, func.count(PendingDelivery.id))
        .join(PendingDelivery, PendingDelivery.responsible_to_id == User.id)
        .filter(PendingDelivery.returned == False)
        .group_by(User.id).order_by(func.count(PendingDelivery.id).desc()).limit(12).all()
    )
    pend_rows = _mx_rows([((u.full_name or u.username), c) for u, c in prows], tone="warn")

    return render_template(
        "metricas_camionetas.html", active_tab="camionetas", tabla=tabla,
        stock_rows=stock_rows, cons_rows=cons_rows, scrap_rows=scrap_rows, pend_rows=pend_rows, **p,
    )


# ---------- INVENTARIO (foto actual; el filtro de período no aplica) ----------
@app.route("/metricas/inventario")
@login_required
@role_required("ADMIN", "SUPERVISOR", "LECTOR")
def metricas_inventario():
    p = _mx_period()
    snap = _mx_snapshot()

    total_items = Item.query.count()
    inactivos = Item.query.filter_by(is_active=False).count()
    con_stock = db.session.query(Stock.item_id).filter(Stock.quantity > 0).distinct().count()
    sin_stock = Item.query.filter_by(is_active=True).count() - con_stock

    hace30 = now_ar() - timedelta(days=30)
    hace90 = now_ar() - timedelta(days=90)
    ids_stock = {r[0] for r in db.session.query(Stock.item_id).filter(Stock.quantity > 0).distinct()}
    ids_activos = {r[0] for r in db.session.query(Item.id).filter(Item.is_active == True)}
    ids_mov30 = {r[0] for r in db.session.query(Movement.item_id).filter(Movement.created_at >= hace30).distinct()}
    ids_mov90 = {r[0] for r in db.session.query(Movement.item_id).filter(Movement.created_at >= hace90).distinct()}
    quietos30 = (ids_stock & ids_activos) - ids_mov30
    quietos90 = (ids_stock & ids_activos) - ids_mov90

    cards = [
        {"label": "Stock total", "value": snap["stock_total"], "hint": "unidades disponibles", "tone": "accent"},
        {"label": "Ítems activos", "value": snap["items_activos"], "hint": f"{total_items} en catálogo", "tone": ""},
        {"label": "En alerta", "value": snap["alertas"], "hint": "bajo mínimo (rojo/amarillo)", "tone": "warn"},
        {"label": "Sin stock", "value": max(sin_stock, 0), "hint": "activos en cero", "tone": "danger"},
        {"label": "Inactivos", "value": inactivos, "hint": "dados de baja", "tone": ""},
        {"label": "Quietos 30d", "value": len(quietos30), "hint": "con stock, sin movimiento", "tone": "warn"},
        {"label": "Quietos 90d", "value": len(quietos90), "hint": "con stock, sin movimiento", "tone": "danger"},
    ]

    # Stock por categoría
    crows = (
        db.session.query(Category.name, func.coalesce(func.sum(Stock.quantity), 0))
        .join(Item, Item.category_id == Category.id)
        .join(Stock, Stock.item_id == Item.id)
        .filter(Stock.quantity > 0)
        .group_by(Category.id).order_by(func.sum(Stock.quantity).desc()).all()
    )
    by_cat = _mx_rows([(name, tot) for name, tot in crows], tone="accent")

    # Stock por ubicación
    lrows = (
        db.session.query(Location, func.coalesce(func.sum(Stock.quantity), 0))
        .join(Stock, Stock.location_id == Location.id)
        .filter(Stock.quantity > 0)
        .group_by(Location.id).order_by(func.sum(Stock.quantity).desc()).all()
    )
    by_loc = _mx_rows(
        [(loc.name, tot) for loc, tot in lrows], tone="accent",
        badges={loc.name: ("camioneta" if loc.is_truck else None) for loc, _ in lrows},
    )

    return render_template(
        "metricas_inventario.html", active_tab="inventario", cards=cards,
        by_cat=by_cat, by_loc=by_loc, show_filter=False, **p,
    )


# ---------- MOVIMIENTOS ----------
@app.route("/metricas/movimientos")
@login_required
@role_required("ADMIN", "SUPERVISOR", "LECTOR")
def metricas_movimientos():
    p = _mx_period()
    dfrom, dto = p["dt_from"], p["dt_to"]

    total = Movement.query.filter(Movement.created_at >= dfrom, Movement.created_at <= dto).count()
    unidades = db.session.query(func.coalesce(func.sum(Movement.qty), 0)).filter(
        Movement.created_at >= dfrom, Movement.created_at <= dto).scalar() or 0

    cards = [
        {"label": "Movimientos", "value": total, "hint": "en el período", "tone": "accent"},
        {"label": "Unidades movidas", "value": int(unidades), "hint": "sumando cantidades", "tone": ""},
    ]

    # Por usuario
    urows = (
        db.session.query(User, func.count(Movement.id))
        .join(Movement, Movement.user_id == User.id)
        .filter(Movement.created_at >= dfrom, Movement.created_at <= dto)
        .group_by(User.id).order_by(func.count(Movement.id).desc()).limit(12).all()
    )
    by_user = _mx_rows([((u.full_name or u.username), c) for u, c in urows])

    # Por ruta (origen -> destino)
    from sqlalchemy.orm import aliased
    FromLoc = aliased(Location)
    ToLoc = aliased(Location)
    route_rows = (
        db.session.query(FromLoc.name, ToLoc.name, func.count(Movement.id))
        .join(FromLoc, Movement.from_location_id == FromLoc.id)
        .join(ToLoc, Movement.to_location_id == ToLoc.id)
        .filter(Movement.created_at >= dfrom, Movement.created_at <= dto)
        .group_by(Movement.from_location_id, Movement.to_location_id)
        .order_by(func.count(Movement.id).desc()).limit(12).all()
    )
    by_route = _mx_rows([(f"{a} → {b}", c) for a, b, c in route_rows])

    # Ítems más movidos
    irows = (
        db.session.query(Item, func.count(Movement.id))
        .join(Movement, Movement.item_id == Item.id)
        .filter(Movement.created_at >= dfrom, Movement.created_at <= dto)
        .group_by(Item.id).order_by(func.count(Movement.id).desc()).limit(12).all()
    )
    top_items = _mx_rows([(f"{it.code} · {it.name}", c) for it, c in irows], tone="ok")

    # Tendencia mensual (cantidad de movimientos)
    trend = []
    for lbl, ms, me in _mx_months(dfrom, dto):
        c = Movement.query.filter(Movement.created_at >= ms, Movement.created_at < me).count()
        trend.append((lbl, c))
    tmax = max([v for _, v in trend], default=0)
    trend_rows = [{"label": l, "value": v, "max": tmax} for l, v in trend]

    return render_template(
        "metricas_movimientos.html", active_tab="movimientos", cards=cards,
        by_user=by_user, by_route=by_route, top_items=top_items, trend_rows=trend_rows, **p,
    )


# ---------- REPARACIONES + PENDIENTES ----------
@app.route("/metricas/reparaciones")
@login_required
@role_required("ADMIN", "SUPERVISOR", "LECTOR")
def metricas_reparaciones():
    p = _mx_period()
    dfrom, dto = p["dt_from"], p["dt_to"]

    creadas = Repair.query.filter(Repair.created_at >= dfrom, Repair.created_at <= dto).count()
    abiertas = Repair.query.filter_by(status="EN_REPARACION").count()
    reparadas = Repair.query.filter(
        Repair.status == "REPARADO", Repair.resolved_at >= dfrom, Repair.resolved_at <= dto).count()
    descartadas = Repair.query.filter(
        Repair.status == "DESCARTADO", Repair.resolved_at >= dfrom, Repair.resolved_at <= dto).count()
    resueltas = reparadas + descartadas
    tasa_recup = round((reparadas / resueltas * 100), 1) if resueltas else 0

    # Tiempo promedio de reparación (created -> resolved) en días
    difs = [
        (r.resolved_at - r.created_at).total_seconds() / 86400.0
        for r in Repair.query.filter(Repair.resolved_at.isnot(None),
                                     Repair.resolved_at >= dfrom, Repair.resolved_at <= dto).all()
        if r.resolved_at and r.created_at
    ]
    prom_dias = round(sum(difs) / len(difs), 1) if difs else 0

    # Pendientes (foto actual)
    pend_abiertos = PendingDelivery.query.filter_by(returned=False).count()
    pend_total = PendingDelivery.query.count()
    tasa_dev = round(((pend_total - pend_abiertos) / pend_total * 100), 1) if pend_total else 0

    cards = [
        {"label": "Reparaciones creadas", "value": creadas, "hint": "en el período", "tone": "accent"},
        {"label": "Abiertas", "value": abiertas, "hint": "en reparación ahora", "tone": "warn"},
        {"label": "Reparadas", "value": reparadas, "hint": "resueltas OK", "tone": "ok"},
        {"label": "Descartadas", "value": descartadas, "hint": "no recuperadas", "tone": "danger"},
        {"label": "Tasa de recupero", "value": f"{tasa_recup}%", "hint": "reparadas / resueltas", "tone": ""},
        {"label": "Reparación promedio", "value": f"{prom_dias} d", "hint": "de alta a resolución", "tone": ""},
        {"label": "Pendientes abiertos", "value": pend_abiertos, "hint": "sin devolución (foto)", "tone": "danger"},
        {"label": "Tasa de devolución", "value": f"{tasa_dev}%", "hint": "histórico", "tone": ""},
    ]

    # Reparaciones por ítem
    irows = (
        db.session.query(Item, func.count(Repair.id))
        .join(Repair, Repair.item_id == Item.id)
        .filter(Repair.created_at >= dfrom, Repair.created_at <= dto)
        .group_by(Item.id).order_by(func.count(Repair.id).desc()).limit(10).all()
    )
    by_item = _mx_rows([(f"{it.code} · {it.name}", c) for it, c in irows], tone="warn")

    # Pendientes abiertos por responsable
    prows = (
        db.session.query(User, func.count(PendingDelivery.id))
        .join(PendingDelivery, PendingDelivery.responsible_to_id == User.id)
        .filter(PendingDelivery.returned == False)
        .group_by(User.id).order_by(func.count(PendingDelivery.id).desc()).limit(12).all()
    )
    pend_by_user = _mx_rows([((u.full_name or u.username), c) for u, c in prows], tone="danger")

    estado_labels = ["Abiertas", "Reparadas", "Descartadas"]
    estado_values = [abiertas, reparadas, descartadas]
    return render_template(
        "metricas_reparaciones.html", active_tab="reparaciones", cards=cards,
        by_item=by_item, pend_by_user=pend_by_user,
        estado_labels=estado_labels, estado_values=estado_values, **p,
    )

# ================================================================
#  FIN MÉTRICAS
# ================================================================


# ================================================================
#  PROVEEDORES + INGRESOS/EGRESOS
# ================================================================

SUPPLIER_ROLES = ("ADMIN", "SUPERVISOR")
INOUT_VIEW_ROLES = ("ADMIN", "SUPERVISOR", "LECTOR")
INOUT_EDIT_ROLES = ("ADMIN", "SUPERVISOR")


def get_jaula_location():
    return Location.query.filter_by(name=LOCATION_JAULA_TNG).first()


def get_proveedor_location():
    return Location.query.filter_by(name=LOCATION_PROVEEDOR).first()


# ---------------- Carta de Proveedores (ABM) ----------------

@app.route("/proveedores", methods=["GET", "POST"])
@login_required
@role_required(*SUPPLIER_ROLES)
def suppliers():
    if request.method == "POST":
        contact_name = request.form.get("contact_name", "").strip()
        if not contact_name:
            flash("El nombre del contacto es obligatorio.", "error")
            return redirect(url_for("suppliers"))
        s = Supplier(
            contact_name=contact_name,
            business_name=request.form.get("business_name", "").strip() or None,
            cuit=request.form.get("cuit", "").strip() or None,
            legal_name=request.form.get("legal_name", "").strip() or None,
            email=request.form.get("email", "").strip() or None,
            phone=request.form.get("phone", "").strip() or None,
        )
        db.session.add(s)
        db.session.commit()
        flash("Proveedor creado.", "ok")
        return redirect(url_for("suppliers"))

    show_inactive = request.args.get("inactivos") == "1"
    q = Supplier.query
    if not show_inactive:
        q = q.filter_by(is_active=True)
    suppliers_list = q.order_by(Supplier.contact_name).all()
    return render_template(
        "suppliers.html", suppliers=suppliers_list, show_inactive=show_inactive
    )


@app.route("/proveedores/<int:supplier_id>/edit", methods=["POST"])
@login_required
@role_required(*SUPPLIER_ROLES)
def supplier_edit(supplier_id):
    s = Supplier.query.get_or_404(supplier_id)
    contact_name = request.form.get("contact_name", "").strip()
    if not contact_name:
        flash("El nombre del contacto es obligatorio.", "error")
        return redirect(url_for("suppliers"))
    s.contact_name = contact_name
    s.business_name = request.form.get("business_name", "").strip() or None
    s.cuit = request.form.get("cuit", "").strip() or None
    s.legal_name = request.form.get("legal_name", "").strip() or None
    s.email = request.form.get("email", "").strip() or None
    s.phone = request.form.get("phone", "").strip() or None
    db.session.commit()
    flash("Proveedor actualizado.", "ok")
    return redirect(url_for("suppliers"))


@app.route("/proveedores/<int:supplier_id>/baja", methods=["POST"])
@login_required
@role_required(*SUPPLIER_ROLES)
def supplier_toggle(supplier_id):
    s = Supplier.query.get_or_404(supplier_id)
    s.is_active = not s.is_active
    db.session.commit()
    flash("Proveedor " + ("reactivado." if s.is_active else "dado de baja."), "ok")
    return redirect(url_for("suppliers"))


# ---------------- Ingresos / Egresos ----------------

@app.route("/ingresos-egresos", methods=["GET", "POST"])
@login_required
@role_required(*INOUT_VIEW_ROLES)
def in_out():
    jaula = get_jaula_location()
    proveedor_loc = get_proveedor_location()

    if request.method == "POST":
        if current_user.role not in INOUT_EDIT_ROLES:
            flash("No tenés permisos para registrar ingresos/egresos.", "error")
            return redirect(url_for("in_out"))
        if not jaula or not proveedor_loc:
            flash(
                f"Faltan ubicaciones requeridas: '{LOCATION_JAULA_TNG}' y/o "
                f"'{LOCATION_PROVEEDOR}'. Crealas antes de operar.", "error"
            )
            return redirect(url_for("in_out"))

        tipo = request.form.get("tipo", "").strip().upper()  # INGRESO / EGRESO
        supplier_raw = request.form.get("supplier_id", "").strip()
        observation = (request.form.get("observation", "") or "").strip() or None

        if tipo not in ("INGRESO", "EGRESO"):
            flash("Elegí si es ingreso o egreso.", "error")
            return redirect(url_for("in_out"))
        if not supplier_raw.isdigit():
            flash("Elegí un proveedor.", "error")
            return redirect(url_for("in_out"))
        supplier = Supplier.query.get(int(supplier_raw))
        if not supplier or not supplier.is_active:
            flash("El proveedor no existe o está dado de baja.", "error")
            return redirect(url_for("in_out"))

        # Ingreso: Proveedor -> Jaula. Egreso: Jaula -> Proveedor.
        if tipo == "INGRESO":
            from_id, to_id = proveedor_loc.id, jaula.id
        else:
            from_id, to_id = jaula.id, proveedor_loc.id
        from_ext = location_is_external(from_id)
        to_ext = location_is_external(to_id)

        # Filas multi-ítem (posicionales y alineadas). line_serials[] trae los
        # seriales de esa fila (uno por línea), o vacío si el ítem no es serializado.
        item_ids = request.form.getlist("item_id[]")
        qtys = request.form.getlist("qty[]")
        serials_list = request.form.getlist("line_serials[]")

        # Se valida TODO antes de tocar nada (operación todo-o-nada).
        planned = []  # {it, qty, new_serials, out_units}
        seen_serials = set()  # evita repetir un serial entre filas
        for idx in range(len(item_ids)):
            item_raw = (item_ids[idx] or "").strip()
            qty_raw = (qtys[idx] or "").strip() if idx < len(qtys) else ""
            serials_raw = (serials_list[idx] or "") if idx < len(serials_list) else ""

            if not item_raw:
                continue  # fila vacía
            if not item_raw.isdigit():
                flash("Ítem inválido en una de las filas.", "error")
                return redirect(url_for("in_out"))
            it = Item.query.get(int(item_raw))
            if not it or not it.is_active:
                flash("Hay una fila con un ítem inexistente o dado de baja.", "error")
                return redirect(url_for("in_out"))

            out_units = []
            if it.serialized and tipo == "EGRESO":
                # Egreso serializado: OBLIGATORIO elegir los seriales (botón
                # «Elegir S/N»). Deben estar EN_STOCK en la Jaula. La cantidad del
                # egreso la definen los seriales elegidos.
                txt = serials_raw
                for sep in (",", ";"):
                    txt = txt.replace(sep, "\n")
                serials = [s.strip() for s in txt.splitlines() if s.strip()]
                if not serials:
                    flash(f"«{it.code} - {it.name}» es serializado: elegí los seriales a egresar con «Elegir S/N».", "error")
                    return redirect(url_for("in_out"))
                low = [s.lower() for s in serials]
                if len(set(low)) != len(low):
                    flash(f"«{it.code} - {it.name}»: hay seriales repetidos en la fila.", "error")
                    return redirect(url_for("in_out"))
                for s in low:
                    key = (it.id, s)
                    if key in seen_serials:
                        flash(f"«{it.code} - {it.name}»: serial repetido entre filas.", "error")
                        return redirect(url_for("in_out"))
                    seen_serials.add(key)
                for s in serials:
                    u = (ItemUnit.query
                         .filter(ItemUnit.item_id == it.id,
                                 func.lower(ItemUnit.serial) == s.lower(),
                                 ItemUnit.status == UNIT_EN_STOCK,
                                 ItemUnit.location_id == jaula.id)
                         .first())
                    if not u:
                        flash(f"«{it.code} - {it.name}»: el serial «{s}» no está disponible en la Jaula.", "error")
                        return redirect(url_for("in_out"))
                    out_units.append(u)
                qty = len(out_units)
            else:
                # Ingreso (serializado o no) y egreso no serializado: por cantidad.
                # En un ingreso serializado los seriales se etiquetan DESPUÉS, desde
                # la ficha del ítem (acá solo entra el cupo/stock).
                try:
                    qty = int(qty_raw)
                    if qty <= 0:
                        raise ValueError()
                except Exception:
                    flash(f"«{it.code} - {it.name}»: cantidad inválida.", "error")
                    return redirect(url_for("in_out"))

            planned.append({"it": it, "qty": qty, "out_units": out_units})

        if not planned:
            flash("Cargá al menos un ítem.", "error")
            return redirect(url_for("in_out"))

        tipo_label = "Ingreso" if tipo == "INGRESO" else "Egreso"
        try:
            # Un solo remito agrupa toda la operación.
            ry, rseq, rnumber = next_remito_number()
            r_obs = f"{tipo_label} · {supplier.contact_name}"
            if observation:
                r_obs = f"{r_obs} · {observation}"
            r = Remito(
                year=ry, seq=rseq, number=rnumber,
                status="CONFIRMADO", print_pending=True,
                from_location_id=from_id, to_location_id=to_id,
                created_by_user_id=current_user.id,
                observation=r_obs[:255],
                responsible_from_id=None, responsible_to_id=None,
            )
            db.session.add(r)
            db.session.flush()

            for p in planned:
                it, qty = p["it"], p["qty"]
                if not from_ext:
                    upsert_stock(it.id, from_id, -qty)
                if not to_ext:
                    upsert_stock(it.id, to_id, qty)

                obs_final = observation
                if p["out_units"]:
                    obs_final = serial_obs(observation, [u.serial for u in p["out_units"]])

                y, seq, number = next_movement_number()
                m = Movement(
                    item_id=it.id, qty=qty,
                    from_location_id=from_id, to_location_id=to_id,
                    user_id=current_user.id, observation=obs_final,
                    year=y, seq=seq, number=number,
                    supplier_id=supplier.id,
                )
                db.session.add(m)
                db.session.flush()

                # Egreso serializado: marcar las unidades elegidas como salidas.
                if p["out_units"]:
                    apply_serial_units_out(p["out_units"], to_id)

                db.session.add(RemitoLine(remito_id=r.id, movement_id=m.id))

            db.session.commit()
            flash(
                f"{tipo_label} registrado ({len(planned)} ítem/s). "
                f"Remito {rnumber} generado — acordate de imprimirlo.", "ok",
            )
        except Exception as e:
            db.session.rollback()
            flash(f"No se pudo registrar: {e}", "error")
        return redirect(url_for("in_out"))

    # ---- GET ----
    suppliers_list = Supplier.query.filter_by(is_active=True).order_by(Supplier.contact_name).all()
    items_list = Item.query.filter_by(is_active=True).order_by(Item.code).all()

    logs = (
        Movement.query.filter(Movement.supplier_id.isnot(None))
        .order_by(Movement.created_at.desc()).limit(300).all()
    )
    # Remito por movimiento (para el link/estado de impresion).
    line_map = {ln.movement_id: ln.remito for ln in RemitoLine.query.all()}

    # Seriales disponibles en Jaula (para egreso de serializados).
    jaula_units = {}
    if jaula:
        for u in (ItemUnit.query
                  .filter_by(status=UNIT_EN_STOCK, location_id=jaula.id)
                  .order_by(ItemUnit.item_id, ItemUnit.serial).all()):
            jaula_units.setdefault(u.item_id, []).append([u.id, u.serial])
    serialized_item_ids = [it.id for it in items_list if it.serialized]

    # Stock de la Jaula por ítem (para egreso: qué ítems mostrar y el tope de
    # cantidad). {item_id: cantidad}.
    jaula_stock = {}
    if jaula:
        for s in Stock.query.filter(Stock.location_id == jaula.id, Stock.quantity > 0).all():
            jaula_stock[s.item_id] = s.quantity

    return render_template(
        "ingresos_egresos.html",
        suppliers=suppliers_list,
        items=items_list,
        jaula_stock=jaula_stock,
        logs=logs,
        line_map=line_map,
        jaula_units=jaula_units,
        serialized_item_ids=serialized_item_ids,
        jaula=jaula,
        proveedor_loc=proveedor_loc,
        can_edit=(current_user.role in INOUT_EDIT_ROLES),
    )


@app.route("/remitos/<int:remito_id>/impreso", methods=["POST"])
@login_required
@role_required(*REMITO_EDIT_ROLES)
def remito_mark_printed(remito_id):
    r = Remito.query.get_or_404(remito_id)
    r.print_pending = False
    db.session.commit()
    flash(f"Remito {r.number} marcado como impreso.", "ok")
    return redirect(request.referrer or url_for("remitos"))


@app.context_processor
def inject_print_badge():
    """Badge de remitos pendientes de imprimir (auto-generados por Ingresos/Egresos).

    Solo cuenta remitos con print_pending=True. Los remitos normales quedan en
    False, asi que no disparan alerta. Visible para ADMIN/SUPERVISOR.
    """
    count = 0
    try:
        if current_user.is_authenticated and current_user.role in ("ADMIN", "SUPERVISOR"):
            count = Remito.query.filter_by(print_pending=True).count()
    except Exception:
        count = 0
    return {"print_badge_count": count}


# ---------------------------------------------------------------------------
# ARRANQUE: sincronizacion automatica del esquema. Corre al IMPORTAR la app,
# asi aplica tanto con `python app.py` (local) como sirviendo por WSGI
# (serve.py + nssm en AWS), que es donde antes NO se actualizaba la DB y el
# server caia con "no such column". Best-effort: si algo falla, se loguea pero
# no impide levantar la app.
# ---------------------------------------------------------------------------
def _startup_db_sync() -> None:
    try:
        with app.app_context():
            ensure_sqlite_schema()   # agrega columnas faltantes (aditivo)
            db.create_all()          # crea tablas nuevas
    except Exception as exc:
        print(f"[startup][WARN] no se pudo sincronizar el esquema: {exc}")


_startup_db_sync()


if __name__ == "__main__":
    with app.app_context():
        ensure_sqlite_schema()
        db.create_all()
        seed_defaults()

    app_host = os.environ.get("APP_HOST", "127.0.0.1")
    app_port = int(os.environ.get("APP_PORT", "5000"))
    app_debug = os.environ.get("APP_DEBUG", "false").lower() == "true"
    app.run(host=app_host, port=app_port, debug=app_debug)