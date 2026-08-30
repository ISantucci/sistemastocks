"""Script standalone: crea las tablas nuevas de la integracion y una
ApiKey inicial.

No hay Flask-Migrate/Alembic en este repo (ver requirements.txt: no figura
la dependencia, y no existe carpeta migrations/). Por eso se usa
`db.create_all()`, igual que hace `serve.py` con las tablas historicas -
es idempotente y SOLO crea tablas que todavia no existen, nunca toca ni
borra las que ya estan.

Uso (desde la carpeta sistemastocks/, con el venv del proyecto activado):

    python scripts/create_integration_tables.py

Imprime la Api Key generada UNA sola vez, en texto plano, para copiarla a
mano al .env de TNGTickets. No se guarda en ningun archivo del repo ni se
loguea: solo se persiste su hash sha256 en la tabla integration_api_keys.
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path

# Permite correr el script desde cualquier lado apuntando siempre al
# sistemastocks/ que lo contiene (mismo criterio que el resto de scripts/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app, db  # noqa: E402
from integration_api import _hash_key  # noqa: E402
from integration_models import ApiKey, IntegrationConsumo  # noqa: E402,F401


def main() -> None:
    with app.app_context():
        db.create_all()
        print("[OK] Tablas de integracion creadas/verificadas "
              "(integration_api_keys, integration_consumos).")

        if ApiKey.query.count() > 0:
            print(
                "[INFO] Ya existe al menos una ApiKey. No se genero una nueva. "
                "Si necesitas otra, insertala a mano o adapta este script."
            )
            return

        raw_key = secrets.token_urlsafe(32)
        api_key = ApiKey(
            name="TNGTickets (integracion consumos)",
            key_hash=_hash_key(raw_key),
            scopes="",
            is_active=True,
        )
        db.session.add(api_key)
        db.session.commit()

        print("")
        print("=" * 78)
        print("API KEY GENERADA (se muestra UNA sola vez, no queda guardada en texto")
        print("plano en ningun lado - copiala ahora al .env de TNGTickets):")
        print("")
        print(f"    {raw_key}")
        print("")
        print("Usarla como header: Authorization: Api-Key " + raw_key)
        print("=" * 78)


if __name__ == "__main__":
    main()
