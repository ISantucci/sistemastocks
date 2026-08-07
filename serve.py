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
    print(f"[serve] waitress escuchando en http://{host}:{port} (threads={threads})")
    serve(app, host=host, port=port, threads=threads)
