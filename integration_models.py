"""Modelos de la integracion TNGTickets <-> SistemaStocksTNG.

Archivo NUEVO, no toca ningun modelo existente. Vive en el mismo `db`
(mismo `Base.metadata`) que app.py para que las tablas queden en la misma
base SQLite; se crean con `scripts/create_integration_tables.py` (o al
levantar `serve_integration.py`, que llama a `db.create_all()` igual que
`serve.py` hace con las tablas historicas).

Tablas:
  - ApiKey: claves de API compartidas (no por usuario) para autenticar al
    otro sistema. Se guarda el HASH sha256 de la key, nunca la key en texto
    plano.
  - IntegrationConsumo: un registro por cada consumo aplicado via
    POST /api/v1/consumos, enlazado al Movement real que genero el
    descuento de stock. `idempotency_key` es UNIQUE: es lo que evita que un
    reintento de red del otro sistema duplique el movimiento.
"""
from __future__ import annotations

from app import db, now_ar


class ApiKey(db.Model):
    __tablename__ = "integration_api_keys"
    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(120), nullable=False)
    # sha256 hexdigest (64 caracteres) de la key real. La key en si NUNCA se
    # guarda ni se loguea: se muestra una sola vez al crearla (ver script).
    key_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)

    # Lista de scopes separados por coma (ej. "consumos,locations"). Vacio o
    # NULL = sin scopes especificos, ver require_api_key().
    scopes = db.Column(db.String(255), nullable=True, default="")

    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=now_ar, nullable=False)

    def scope_list(self) -> list[str]:
        return [s.strip() for s in (self.scopes or "").split(",") if s.strip()]


class IntegrationConsumo(db.Model):
    """Un consumo aplicado desde TNGTickets. Enlaza con el Movement real
    (inmutable, igual que el resto del sistema) que efectivamente movio el
    stock hacia "Utilizado".
    """
    __tablename__ = "integration_consumos"
    id = db.Column(db.Integer, primary_key=True)

    movement_id = db.Column(db.Integer, db.ForeignKey("movements.id"), nullable=False, index=True)

    # UNIQUE + NOT NULL: la clave de idempotencia que manda el otro sistema.
    # Un reintento con la misma key nunca vuelve a aplicar el movimiento (se
    # devuelve el resultado guardado la primera vez).
    idempotency_key = db.Column(db.String(120), unique=True, nullable=False, index=True)

    ticket_id = db.Column(db.String(64), nullable=False, index=True)
    tecnico_legajo = db.Column(db.String(64), nullable=True)
    tecnico_nombre = db.Column(db.String(160), nullable=True)

    created_at = db.Column(db.DateTime, default=now_ar, nullable=False)

    movement = db.relationship("Movement")
