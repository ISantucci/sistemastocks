# -*- coding: utf-8 -*-
"""El build de los assets: que lo servido corresponda al fuente.

El riesgo de tener un paso de build es siempre el mismo, y no da error: alguien
edita static/js/algo.js, se olvida de correr `npm run build`, y producción sigue
sirviendo el minificado viejo. El bug "arreglado" reaparece y nadie entiende por
qué, porque el fuente en git ya tiene el arreglo.

Estas pruebas son lo que convierte ese olvido silencioso en un test rojo.

No necesitan Node: leen el manifiesto que dejó el build y comparan hashes.
"""
import hashlib
import json
import os

import pytest

from conftest import make_user, login

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STATIC = os.path.join(BASE, "static")
DIST = os.path.join(STATIC, "dist")
MANIFEST = os.path.join(DIST, "manifest.json")


def _hash(ruta):
    with open(ruta, "rb") as fh:
        return hashlib.sha1(fh.read()).hexdigest()[:12]


def _fuentes():
    """Los mismos archivos que toma scripts/build_assets.mjs."""
    out = []
    for carpeta in ("js", "css"):
        d = os.path.join(STATIC, carpeta)
        if not os.path.isdir(d):
            continue
        for nombre in sorted(os.listdir(d)):
            if not nombre.endswith((".js", ".css")):
                continue
            if ".bak" in nombre:
                continue
            out.append(f"{carpeta}/{nombre}")
    return sorted(out)


hay_dist = pytest.mark.skipif(
    not os.path.exists(MANIFEST),
    reason="static/dist/ no está construido (npm run build). La app sirve los fuentes.",
)


# --------------------------------------------------------- el test que importa

@hay_dist
def test_el_minificado_esta_al_dia_con_los_fuentes():
    """Si esto falla: alguien tocó un archivo de static/ y no corrió el build.

    Se arregla con `npm run build` y commiteando static/dist/. No se arregla
    borrando este test: sin él, el desfasaje se descubre en producción.
    """
    manifiesto = json.load(open(MANIFEST, encoding="utf-8")).get("fuentes", {})
    desactualizados = []
    for rel in _fuentes():
        actual = _hash(os.path.join(STATIC, rel))
        if manifiesto.get(rel) != actual:
            desactualizados.append(rel)
    sobrantes = [r for r in manifiesto if r not in _fuentes()]

    assert not desactualizados, (
        "static/dist/ quedó desactualizado respecto de estos fuentes: "
        f"{desactualizados}. Corré 'npm run build' y commiteá static/dist/."
    )
    assert not sobrantes, (
        f"el manifiesto tiene archivos que ya no existen: {sobrantes}. "
        "Corré 'npm run build'."
    )


@hay_dist
def test_todos_los_fuentes_tienen_su_minificado():
    for rel in _fuentes():
        destino = os.path.join(DIST, rel)
        assert os.path.exists(destino), f"falta el minificado de {rel}"
        assert os.path.getsize(destino) > 0, f"{rel} quedó vacío al minificar"


@hay_dist
def test_el_minificado_no_puede_ser_mas_grande_que_el_fuente():
    """Guarda simple contra un build roto que copie en vez de minificar."""
    for rel in _fuentes():
        origen = os.path.getsize(os.path.join(STATIC, rel))
        destino = os.path.getsize(os.path.join(DIST, rel))
        assert destino <= origen, f"{rel}: el 'minificado' pesa más que el fuente"


@hay_dist
def test_el_minificado_no_lleva_los_comentarios():
    """Es medio el punto de todo esto: lo que se publica no explica el sistema."""
    with open(os.path.join(STATIC, "js", "movements.js"), encoding="utf-8") as fh:
        fuente = fh.read()
    with open(os.path.join(DIST, "js", "movements.js"), encoding="utf-8") as fh:
        servido = fh.read()
    # El fuente sí tiene comentarios explicando el porqué...
    assert "Tope de cantidad" in fuente
    # ...y lo que se sirve, no.
    assert "Tope de cantidad" not in servido


# ------------------------------------------------------- lo que sirve la app

@hay_dist
def test_la_app_sirve_el_minificado(A, client):
    make_user(A, "adm", "ADMIN")
    login(client, "adm")
    html = client.get("/stock").get_data(as_text=True)
    assert "/static/dist/js/app.js" in html, "la app sigue sirviendo el fuente"
    assert "/static/dist/css/app.css" in html


@hay_dist
def test_en_debug_se_sirve_el_fuente(A, client):
    """En desarrollo hay que ver lo que uno edita, no el minificado de ayer."""
    A.app.debug = True
    A._ASSET_DIST_CACHE.clear()
    try:
        make_user(A, "adm", "ADMIN")
        login(client, "adm")
        html = client.get("/stock").get_data(as_text=True)
        assert "/static/js/app.js" in html
        assert "/static/dist/" not in html
    finally:
        A.app.debug = False
        A._ASSET_DIST_CACHE.clear()


def test_sin_dist_la_app_sigue_andando(A, client, monkeypatch, tmp_path):
    """Degradación segura: si nadie corrió el build, se sirven los fuentes.

    Importa porque significa que el build mejora lo que se publica pero no es
    una dependencia dura: un deploy sin static/dist/ arranca igual.
    """
    monkeypatch.setattr(A, "BASE_DIR", tmp_path)   # /static vacío: no hay dist
    A._ASSET_DIST_CACHE.clear()
    try:
        make_user(A, "adm", "ADMIN")
        login(client, "adm")
        r = client.get("/stock")
        assert r.status_code == 200
        assert "/static/dist/" not in r.get_data(as_text=True)
    finally:
        A._ASSET_DIST_CACHE.clear()
