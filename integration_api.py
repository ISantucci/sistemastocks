"""API de integracion TNGTickets -> SistemaStocksTNG.

Archivo NUEVO. No reimplementa la logica de movimientos: importa y reusa
`upsert_stock()` y `next_movement_number()` de app.py, las mismas funciones
que usa la vista /item-usage para descontar stock y numerar el Movement.
Ningun modelo existente se toca ni se le agregan columnas.

Autenticacion: header `Authorization: Api-Key <key>` contra la tabla nueva
`integration_api_keys` (se compara el sha256 de la key recibida, la key en
texto plano no se guarda en ningun lado).

Formato de respuesta uniforme:
  exito -> {"ok": true, "data": {...}}
  error -> {"ok": false, "error": "<codigo_corto>", "detail": "<mensaje>"}
"""
from __future__ import annotations

import hashlib
from functools import wraps

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError

from app import (
    csrf,
    db,
    Item,
    Location,
    Movement,
    Stock,
    User,
    upsert_stock,
    next_movement_number,
)
from integration_models import ApiKey, IntegrationConsumo

bp = Blueprint("integration_api", __name__, url_prefix="/api/v1")

# CSRFProtect es global en app.py (ver app.py, seccion "CSRF"). Esta API no
# usa cookies de sesion ni formularios: se autentica con Api-Key propia, asi
# que el token CSRF (pensado para sesiones de navegador) no aplica. Se exime
# el blueprint entero, no una vista individual, para no tener que acordarse
# de eximir cada ruta nueva que se agregue despues.
csrf.exempt(bp)


# ------------------ helpers de respuesta ------------------

def _ok(data, status: int = 200):
    return jsonify({"ok": True, "data": data}), status


def _err(status: int, code: str, detail: str):
    return jsonify({"ok": False, "error": code, "detail": detail}), status


# ------------------ autenticacion ------------------

def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def require_api_key(*scopes: str):
    """Exige `Authorization: Api-Key <key>` valida y activa.

    Si se pasan `scopes` y la key tiene scopes propios definidos (campo
    `scopes` de ApiKey, no vacio), exige que al menos uno matchee. Una key
    sin scopes definidos (caso tipico: una sola key compartida para todo)
    pasa cualquier chequeo de scope.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Api-Key "):
                return _err(401, "unauthorized", "Falta header Authorization: Api-Key <key>")

            raw_key = auth[len("Api-Key "):].strip()
            if not raw_key:
                return _err(401, "unauthorized", "Api key vacia")

            api_key = ApiKey.query.filter_by(key_hash=_hash_key(raw_key), is_active=True).first()
            if not api_key:
                return _err(401, "unauthorized", "Api key invalida o inactiva")

            if scopes:
                key_scopes = set(api_key.scope_list())
                if key_scopes and not key_scopes.intersection(scopes):
                    return _err(403, "forbidden", "La api key no tiene permiso para esta operacion")

            request.integration_api_key = api_key
            return fn(*args, **kwargs)

        return wrapper

    return decorator


# ------------------ helpers de dominio ------------------

def _find_truck_location(name: str):
    """Location por nombre exacto normalizado (case-insensitive, trim) Y
    is_truck=True. Se compara en Python (no en SQL) para no depender del
    collation de SQLite y para que "trim + case-insensitive" sea exactamente
    lo mismo sin importar el motor de base que termine usando la app.
    """
    normalized = (name or "").strip().casefold()
    if not normalized:
        return None
    for loc in Location.query.filter_by(is_truck=True).all():
        if (loc.name or "").strip().casefold() == normalized:
            return loc
    return None


def _find_consumable_item(code: str):
    """Item activo y NO serializado por code exacto. None si no existe o si
    existe pero esta inactivo o serializado (fuera de alcance, ver contrato).
    """
    code = (code or "").strip()
    if not code:
        return None
    item = Item.query.filter_by(code=code).first()
    if not item or not item.is_active or item.serialized:
        return None
    return item


def _resolve_tecnico_user(username: str):
    """Usuario de sistemastocks a atribuir en el Movement, resuelto por
    `username` exacto (case-sensitive). Los usernames se sincronizan a mano
    entre los dos sistemas (igual que los nombres de camioneta) -- si no
    hay match, quien llama tiene que romper visible, nunca caer en un
    usuario por default.
    """
    username = (username or "").strip()
    if not username:
        return None
    return User.query.filter_by(username=username).first()


def _location_responsable_name(loc) -> str | None:
    names = [
        (r.user.full_name or r.user.username)
        for r in (loc.responsibles or [])
        if r.user
    ]
    return ", ".join(names) if names else None


def _consumo_payload(registro: IntegrationConsumo, fallback_item=None, fallback_loc=None, fallback_cantidad=None):
    """Arma la data de respuesta de un consumo ya aplicado (nuevo o repetido
    por idempotencia), leyendo del Movement real cuando esta disponible.
    """
    mv = registro.movement
    return {
        "movement_id": registro.movement_id,
        "item_code": (mv.item.code if mv and mv.item else (fallback_item.code if fallback_item else None)),
        "cantidad": (mv.qty if mv else fallback_cantidad),
        "location_name": (mv.from_location.name if mv and mv.from_location else (fallback_loc.name if fallback_loc else None)),
    }


# ------------------ rutas ------------------

@bp.route("/locations", methods=["GET"])
@require_api_key()
def list_locations():
    is_truck_param = (request.args.get("is_truck") or "").strip().lower()
    query = Location.query
    if is_truck_param in ("1", "true", "yes", "si"):
        query = query.filter_by(is_truck=True)

    locations = query.order_by(Location.name.asc()).all()
    data = [
        {
            "id": loc.id,
            "name": loc.name,
            "responsable": _location_responsable_name(loc),
        }
        for loc in locations
    ]
    return _ok(data)


@bp.route("/locations/by-name/<name>/items", methods=["GET"])
@require_api_key()
def location_items(name):
    loc = _find_truck_location(name)
    if not loc:
        return _err(
            404,
            "location_not_found",
            f"No existe una camioneta con nombre '{name}' en el sistema de stock",
        )

    rows = (
        db.session.query(Stock, Item)
        .join(Item, Item.id == Stock.item_id)
        .filter(
            Stock.location_id == loc.id,
            Stock.quantity > 0,
            Item.is_active.is_(True),
            Item.serialized.is_(False),
        )
        .order_by(Item.name.asc())
        .all()
    )

    items = [
        {"code": item.code, "name": item.name, "unit": item.unit, "quantity": stock.quantity}
        for stock, item in rows
    ]
    return _ok({"location": {"id": loc.id, "name": loc.name}, "items": items})


@bp.route("/consumos", methods=["POST"])
@require_api_key()
def create_consumo():
    body = request.get_json(silent=True)
    if body is None:
        return _err(400, "body_invalido", "Body invalido o no es JSON")

    location_name = (body.get("location_name") or "").strip()
    item_code = (body.get("item_code") or "").strip()
    cantidad_raw = body.get("cantidad")
    ticket_id = body.get("ticket_id")
    tecnico_legajo = body.get("tecnico_legajo")
    tecnico_nombre = body.get("tecnico_nombre")
    tecnico_username = (body.get("tecnico_username") or "").strip()
    idempotency_key = (body.get("idempotency_key") or "").strip()

    if not idempotency_key:
        return _err(400, "idempotency_key_requerida", "Falta idempotency_key")
    if ticket_id is None or str(ticket_id).strip() == "":
        return _err(400, "ticket_id_requerido", "Falta ticket_id")

    # --- Orden de validacion fijo (contrato del Paso 2): corta en la primera
    # que falle. ---

    loc = _find_truck_location(location_name)
    if not loc:
        return _err(
            404,
            "location_not_found",
            f"No existe una camioneta con nombre '{location_name}' en el sistema de stock",
        )

    item = _find_consumable_item(item_code)
    if not item:
        return _err(
            404,
            "item_not_found",
            f"No existe un item activo y no serializado con codigo '{item_code}'",
        )

    try:
        cantidad = float(cantidad_raw)
    except (TypeError, ValueError):
        return _err(400, "cantidad_invalida", "cantidad debe ser numerica")
    if cantidad <= 0 or cantidad != int(cantidad):
        return _err(400, "cantidad_invalida", "cantidad debe ser un entero mayor a 0")
    cantidad = int(cantidad)

    # Reintento de red del otro sistema: mismo idempotency_key ya aplicado
    # antes. Se devuelve el resultado de esa vez, SIN volver a tocar stock.
    existing = IntegrationConsumo.query.filter_by(idempotency_key=idempotency_key).first()
    if existing:
        return _ok(_consumo_payload(existing, fallback_item=item, fallback_loc=loc, fallback_cantidad=cantidad))

    utilizado_loc = Location.query.filter_by(name="Utilizado").first()
    if not utilizado_loc:
        return _err(500, "config_invalida", "Ubicacion 'Utilizado' no existe en el sistema de stock")

    if not tecnico_username:
        return _err(400, "tecnico_username_requerido", "Falta tecnico_username")

    system_user = _resolve_tecnico_user(tecnico_username)
    if not system_user:
        return _err(
            404,
            "tecnico_no_encontrado",
            f"No existe en el sistema de stock un usuario con username '{tecnico_username}'. "
            "Sincroniza los usernames entre los dos sistemas para poder registrar el consumo.",
        )

    try:
        stock_query = Stock.query.filter_by(item_id=item.id, location_id=loc.id)
        # Postgres/MySQL: lock de fila real para el chequeo atomico de stock
        # bajo concurrencia. SQLite (motor actual, ver app.py
        # SQLALCHEMY_DATABASE_URI) no soporta SELECT ... FOR UPDATE; ahi la
        # atomicidad la da la transaccion + el UNIQUE de idempotency_key, que
        # ademas cubre el caso de dos requests concurrentes con la misma key.
        if db.engine.dialect.name in ("postgresql", "mysql"):
            stock_query = stock_query.with_for_update()
        stock_row = stock_query.first()
        disponible = stock_row.quantity if stock_row else 0

        if disponible < cantidad:
            db.session.rollback()
            return _err(409, "stock_insuficiente", f"Stock disponible: {disponible}")

        upsert_stock(item.id, loc.id, -cantidad)

        y, seq, number = next_movement_number()
        observacion = (
            f"Consumo desde ticket #{ticket_id} (TNGTickets) - "
            f"tecnico: {tecnico_nombre or 's/d'}"
        )
        movement = Movement(
            item_id=item.id,
            qty=cantidad,
            from_location_id=loc.id,
            to_location_id=utilizado_loc.id,
            user_id=system_user.id,
            observation=observacion,
            year=y,
            seq=seq,
            number=number,
        )
        db.session.add(movement)
        db.session.flush()

        registro = IntegrationConsumo(
            movement_id=movement.id,
            idempotency_key=idempotency_key,
            ticket_id=str(ticket_id),
            tecnico_legajo=(str(tecnico_legajo) if tecnico_legajo is not None else None),
            tecnico_nombre=(str(tecnico_nombre) if tecnico_nombre is not None else None),
        )
        db.session.add(registro)
        db.session.commit()

    except ValueError as e:
        # upsert_stock() detecto stock insuficiente en la carrera final antes
        # del commit (ventana entre el chequeo de arriba y el update, la
        # misma que ya asume upsert_stock en el resto del sistema).
        db.session.rollback()
        return _err(409, "stock_insuficiente", str(e))

    except IntegrityError:
        # Dos requests con la misma idempotency_key llegaron practicamente
        # a la vez: el UNIQUE de integration_consumos frena al segundo commit.
        # Ese segundo request no fallo por las suyas: devolvemos el resultado
        # del que si se aplico, igual que un reintento normal.
        db.session.rollback()
        existing = IntegrationConsumo.query.filter_by(idempotency_key=idempotency_key).first()
        if existing:
            return _ok(_consumo_payload(existing, fallback_item=item, fallback_loc=loc, fallback_cantidad=cantidad))
        return _err(500, "error_interno", "No se pudo registrar el consumo")

    except Exception as e:
        db.session.rollback()
        return _err(500, "error_interno", str(e))

    return _ok({
        "movement_id": movement.id,
        "item_code": item.code,
        "cantidad": cantidad,
        "location_name": loc.name,
    })


@bp.route("/tickets/<ticket_id>/consumos", methods=["GET"])
@require_api_key()
def ticket_consumos(ticket_id):
    rows = (
        db.session.query(IntegrationConsumo, Movement, Item, Location)
        .join(Movement, Movement.id == IntegrationConsumo.movement_id)
        .join(Item, Item.id == Movement.item_id)
        .join(Location, Location.id == Movement.from_location_id)
        .filter(IntegrationConsumo.ticket_id == str(ticket_id))
        .order_by(IntegrationConsumo.created_at.asc())
        .all()
    )

    data = [
        {
            "item_code": item.code,
            "item_name": item.name,
            "cantidad": mv.qty,
            "location_name": loc.name,
            "tecnico_nombre": ic.tecnico_nombre,
            "created_at": ic.created_at.isoformat(),
        }
        for ic, mv, item, loc in rows
    ]
    return _ok(data)
