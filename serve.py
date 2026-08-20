"""Entrypoint de PRODUCCION (WSGI real via waitress).

Uso:  python serve.py

Reemplaza a "python app.py" (server de desarrollo de Werkzeug) en produccion:
EC2 Windows (nssm) y Docker. Como produccion es Windows, se usa waitress
(gunicorn no corre en Windows). 1 proceso + varios threads, apropiado para
SQLite con WAL + busy_timeout ya configurados en app.py.

NO modifica app.py: el modo desarrollo sigue funcionando con "python app.py".
Replica el mismo arranque de esquema/seed que el bloque __main__ de app.py.

Config por entorno (igual criterio que app.py):
  APP_HOST          (default 127.0.0.1)
  APP_PORT          (default 5000)
  WAITRESS_THREADS  (default 8)

IMPORTANTE - exposicion de red:
  El default de APP_HOST es 127.0.0.1 A PROPOSITO. Waitress NO debe ser
  accesible directamente desde Internet: el unico punto de entrada tiene que
  ser Apache (o el reverse proxy que corresponda), que es quien termina TLS,
  fuerza HTTPS y agrega las cabeceras. Si se pone APP_HOST=0.0.0.0 hay que
  asegurarse de que el firewall bloquee el puerto desde afuera.
"""
import os

from waitress import serve

# Al importar app se ejecuta _startup_db_sync() (ensure_sqlite_schema + create_all).
# Aca ademas corremos seed_defaults(), que en app.py solo corria bajo __main__.
from app import app, db, ensure_sqlite_schema, seed_defaults


def _prepare_db() -> None:
    with app.app_context():
        ensure_sqlite_schema()   # agrega columnas faltantes (aditivo)
        db.create_all()          # crea tablas nuevas
        seed_defaults()          # admin/ubicaciones por defecto (idempotente)


if __name__ == "__main__":
    _prepare_db()
    host = os.environ.get("APP_HOST", "127.0.0.1")
    port = int(os.environ.get("APP_PORT", "5000"))
    threads = int(os.environ.get("WAITRESS_THREADS", "8"))

    if host in ("0.0.0.0", "::"):
        print(
            "[WARN] APP_HOST expone waitress en todas las interfaces. "
            "Verifica que el firewall solo permita el acceso desde el proxy."
        )

    print(f"[serve] waitress escuchando en http://{host}:{port} (threads={threads})")
    serve(
        app,
        host=host,
        port=port,
        threads=threads,
        # No publicar "Server: waitress": evita regalar el stack exacto.
        ident=None,
        # Cabeceras X-Forwarded-* del proxy: sin esto, request.is_secure es
        # siempre False detras de Apache y el HSTS condicional nunca se aplica.
        # Se confia en 1 solo proxy (Apache). Si se agrega otro delante, subir
        # el numero; si no hay proxy, poner TRUSTED_PROXY_COUNT=0.
        url_scheme=os.environ.get("APP_URL_SCHEME", "http"),
        trusted_proxy=os.environ.get("TRUSTED_PROXY", "127.0.0.1"),
        trusted_proxy_count=int(os.environ.get("TRUSTED_PROXY_COUNT", "1")),
        trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"},
        clear_untrusted_proxy_headers=True,
    )
