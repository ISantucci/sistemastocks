"""Descarga las dependencias front-end de terceros y las deja servidas localmente.

Problema que resuelve
---------------------
Los templates cargaban Tom Select y Chart.js directamente desde jsDelivr y
cdnjs, sin Subresource Integrity. Eso implica:
  - si el CDN se cae o la red de la oficina lo bloquea, el sistema queda sin
    los selects buscables y sin los graficos de metricas;
  - si el CDN sirviera un archivo alterado, el navegador lo ejecutaria igual.

Este script descarga las versiones ya fijadas en static/vendor/assets.json,
las guarda en static/vendor/<nombre>/<version>/, calcula el hash SRI (sha384)
y actualiza assets.json. A partir de ahi la app las sirve desde /static y no
depende mas del CDN.

Uso
---
    python scripts/vendor_assets.py            # descarga lo que falte
    python scripts/vendor_assets.py --force    # vuelve a descargar todo
    python scripts/vendor_assets.py --check    # solo verifica, no descarga

Es idempotente y NO toca base de datos, ni codigo de la app, ni configuracion
de produccion. Si falla la descarga, no modifica nada: la app sigue usando el
CDN exactamente como hasta ahora.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
VENDOR_DIR = BASE_DIR / "static" / "vendor"
MANIFEST = VENDOR_DIR / "assets.json"
TIMEOUT = 30


def sri_hash(payload: bytes) -> str:
    digest = hashlib.sha384(payload).digest()
    return "sha384-" + base64.b64encode(digest).decode("ascii")


def local_path_for(name: str, meta: dict) -> Path:
    filename = meta["url"].rsplit("/", 1)[-1]
    return VENDOR_DIR / name / meta["version"] / filename


def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "TNGStocks-vendor/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        return resp.read()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-descargar aunque exista")
    parser.add_argument("--check", action="store_true", help="solo verificar integridad")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assets = manifest["assets"]
    changed = False
    failures = 0

    for name, meta in assets.items():
        target = local_path_for(name, meta)
        rel = target.relative_to(BASE_DIR / "static").as_posix()

        if args.check:
            if not target.exists():
                print(f"[  ] {name}: no vendorizado (se sirve desde CDN)")
                continue
            actual = sri_hash(target.read_bytes())
            ok = actual == meta.get("integrity")
            print(f"[{'OK' if ok else '!!'}] {name}: {rel}")
            if not ok:
                failures += 1
            continue

        if target.exists() and not args.force:
            print(f"[--] {name}: ya existe, se saltea ({rel})")
            continue

        print(f"[..] {name}: descargando {meta['url']}")
        try:
            payload = download(meta["url"])
        except Exception as exc:  # red bloqueada, CDN caido, proxy, etc.
            print(f"[!!] {name}: fallo la descarga ({exc}). Se mantiene el CDN.")
            failures += 1
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        meta["local"] = f"vendor/{name}/{meta['version']}/{target.name}"
        meta["integrity"] = sri_hash(payload)
        changed = True
        print(f"[OK] {name}: {rel}  ({len(payload)} bytes)  {meta['integrity']}")

    if changed:
        MANIFEST.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print("\nassets.json actualizado. Reinicia la app para que tome los archivos locales.")
        print("Recorda commitear static/vendor/ para que el deploy no dependa del CDN.")

    if failures:
        print(f"\n{failures} asset(s) con problemas.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
