"""Sección Costos: carga del precio, promedios, valorización, corrección y permisos.

Criterio de estas pruebas: lo que se verifica no es que la pantalla dibuje bien,
sino que NO se pueda romper la trazabilidad ni el stock desde acá. La sección
Costos lee el historial y le pone precio; la única escritura es sobre sus dos
tablas propias.
"""
from datetime import timedelta

from conftest import login, make_item, make_location, make_user


# ------------------------------------------------------------------ helpers

def _admin(client):
    return login(client, "admin", "admin123")


def _mk_supplier(A, name="ACME"):
    s = A.Supplier(contact_name=name, is_active=True)
    A.db.session.add(s)
    A.db.session.commit()
    return s


def _base(A):
    make_location(A, "Jaula TNG")
    make_location(A, "Proveedor", is_external=True)


def _ingreso(client, s, items_qty_price, follow=True):
    """items_qty_price = [(item, qty, "precio UNITARIO"), ...]

    El formulario manda el TOTAL de cada línea (que es lo que dice el remito),
    pero acá se expresa el unitario porque es lo que los tests verifican: el
    helper hace la multiplicación. Si el unitario no es un número redondo,
    pasar el total a mano con _ingreso_total().
    """
    def _total(precio, qty):
        return f"{float(str(precio).replace('.', '').replace(',', '.')) * qty:.2f}".replace(".", ",")
    return client.post("/ingresos-egresos", data={
        "tipo": "INGRESO", "supplier_id": str(s.id),
        "item_id[]": [str(i.id) for i, _, _ in items_qty_price],
        "qty[]": [str(q) for _, q, _ in items_qty_price],
        "line_serials[]": ["" for _ in items_qty_price],
        "line_total[]": [_total(pr, q) for _, q, pr in items_qty_price],
    }, follow_redirects=follow)


def _ingreso_total(client, s, item, qty, total, follow=True):
    """Ingreso de una línea cargando el TOTAL tal cual, sin derivarlo."""
    return client.post("/ingresos-egresos", data={
        "tipo": "INGRESO", "supplier_id": str(s.id),
        "item_id[]": [str(item.id)], "qty[]": [str(qty)],
        "line_serials[]": [""], "line_total[]": [total],
    }, follow_redirects=follow)


# ------------------------------------------------- parseo y formato de plata

def test_parse_money_acepta_lo_que_la_gente_tipea(A):
    f = A.parse_money_to_cents
    assert f("1000") == 100000
    assert f("1000,50") == 100050
    assert f("1000.50") == 100050
    assert f("1.234,56") == 123456
    assert f("$ 1.234,56") == 123456
    assert f("1.234.567") == 123456700     # puntos de miles, sin decimales
    assert f("0,01") == 1


def test_parse_money_rechaza_lo_invalido(A):
    f = A.parse_money_to_cents
    for malo in ("", "   ", None, "0", "-5", "abc", "1,2,3,4x"):
        assert f(malo) is None, malo


def test_fmt_money_sin_precio_no_es_cero(A):
    """Un ítem sin precio vale DESCONOCIDO, no cero. Toda la valorización
    depende de que esa diferencia se sostenga hasta la pantalla."""
    assert A.fmt_money(None) == "—"
    assert A.fmt_money(0) == "$ 0,00"
    assert A.fmt_money(123456) == "$ 1.234,56"
    assert A.fmt_money(100) == "$ 1,00"


# ------------------------------------------------ carga del precio (Fase 1)

def test_ingreso_sin_precio_se_rechaza_y_no_mueve_stock(A, client):
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-200")
    s = _mk_supplier(A)

    client.post("/ingresos-egresos", data={
        "tipo": "INGRESO", "supplier_id": str(s.id),
        "item_id[]": [str(it.id)], "qty[]": ["5"], "line_serials[]": [""],
    }, follow_redirects=True)

    assert A.Movement.query.count() == 0
    assert A.Stock.query.count() == 0
    assert A.ItemPurchasePrice.query.count() == 0


def test_ingreso_con_precio_invalido_se_rechaza(A, client):
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-201")
    s = _mk_supplier(A)

    for malo in ("0", "-100", "abc", ""):
        _ingreso_total(client, s, it, 3, malo)
        assert A.Movement.query.count() == 0, malo
        assert A.ItemPurchasePrice.query.count() == 0, malo


def test_ingreso_guarda_el_precio_y_suma_stock(A, client):
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-202")
    s = _mk_supplier(A)

    _ingreso(client, s, [(it, 10, "1.500,50")])

    p = A.ItemPurchasePrice.query.one()
    assert p.unit_price_cents == 150050      # 15.005,00 / 10
    assert p.item_id == it.id
    assert p.qty == 10                       # sale del Movement, no se duplica
    assert p.total_cents == 1500500
    jaula = A.Location.query.filter_by(name="Jaula TNG").first()
    assert A.Stock.query.filter_by(item_id=it.id, location_id=jaula.id).one().quantity == 10


def test_multilinea_es_todo_o_nada_tambien_con_el_precio(A, client):
    """Si a UNA línea le falta el precio, no entra NINGUNA. Mismo criterio que
    el resto de los handlers multi-línea del sistema."""
    _admin(client)
    _base(A)
    i1 = make_item(A, code="CAB-203")
    i2 = make_item(A, code="CAB-204")
    s = _mk_supplier(A)

    client.post("/ingresos-egresos", data={
        "tipo": "INGRESO", "supplier_id": str(s.id),
        "item_id[]": [str(i1.id), str(i2.id)],
        "qty[]": ["4", "2"], "line_serials[]": ["", ""],
        "line_total[]": ["1000", ""],        # la segunda sin precio
    }, follow_redirects=True)

    assert A.Movement.query.count() == 0
    assert A.ItemPurchasePrice.query.count() == 0
    assert A.Stock.query.count() == 0


def test_el_egreso_no_pide_ni_guarda_precio(A, client):
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-205")
    s = _mk_supplier(A)
    _ingreso(client, s, [(it, 5, "1000")])
    assert A.ItemPurchasePrice.query.count() == 1

    client.post("/ingresos-egresos", data={
        "tipo": "EGRESO", "motivo": "OTRO", "supplier_id": str(s.id),
        "item_id[]": [str(it.id)], "qty[]": ["2"], "line_serials[]": [""],
    }, follow_redirects=True)

    # El egreso no agrega precios: el promedio mira solo ingresos.
    assert A.ItemPurchasePrice.query.count() == 1


def test_el_retorno_de_reparacion_entra_sin_precio(A, client):
    """El cierre de "reparada por proveedor" mete stock en la Jaula por otro
    camino (_repair_transfer_with_remito). No es una compra: no lleva precio,
    y la pantalla no se tiene que romper por eso."""
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-206")
    s = _mk_supplier(A)
    _ingreso(client, s, [(it, 5, "1000")])

    client.post("/ingresos-egresos", data={
        "tipo": "EGRESO", "motivo": "REPARACION", "supplier_id": str(s.id),
        "item_id[]": [str(it.id)], "qty[]": ["2"], "line_serials[]": [""],
    }, follow_redirects=True)
    rep = A.Repair.query.filter_by(status="EN_PROVEEDOR").first()
    assert rep is not None

    client.post("/reparaciones", data={
        "action": "resolver_proveedor", "repair_id": str(rep.id),
        "supplier_id": str(s.id),
    }, follow_redirects=True)

    # Volvió mercadería a la Jaula, pero NO se creó un precio nuevo.
    assert A.ItemPurchasePrice.query.count() == 1
    assert client.get("/ingresos-egresos").status_code == 200
    assert client.get("/costos/ingresos").status_code == 200


# ----------------------------------------------------- promedios (D6 y D7)

def test_promedio_simple_no_pondera_por_cantidad(A, client):
    """10 unidades a $2 y 5 a $6 -> promedio simple $4 (cada compra pesa igual)."""
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-207")
    s = _mk_supplier(A)
    _ingreso(client, s, [(it, 10, "2,00")])
    _ingreso(client, s, [(it, 5, "6,00")])

    assert A.avg_price_map([it.id])[it.id] == 400          # $4,00


def test_promedio_ponderado_si_pondera(A, client):
    """Los mismos datos, ponderados: (10x2 + 5x6) / 15 = $3,33."""
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-208")
    s = _mk_supplier(A)
    _ingreso(client, s, [(it, 10, "2,00")])
    _ingreso(client, s, [(it, 5, "6,00")])

    assert A.weighted_avg_price_map([it.id])[it.id] == 333  # $3,33


def test_item_sin_ingresos_no_tiene_promedio(A, client):
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-209")
    assert A.avg_price_map([it.id]).get(it.id) is None


# ------------------------------------------------------- valorización (D9)

def test_valorizacion_multiplica_promedio_por_stock(A, client):
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-210")
    s = _mk_supplier(A)
    _ingreso(client, s, [(it, 10, "100,00")])   # promedio $100, stock 10

    with A.app.test_request_context():
        snap = A._valorizacion_snapshot()
    assert snap["total_cents"] == 100000        # $1.000,00
    assert snap["sin_costo_items"] == 0


def test_stock_sin_precio_no_vale_cero_se_informa_aparte(A, client):
    """Un ítem que entró por un camino que no es compra queda FUERA del total,
    no suma cero. Es lo que hace creíble el número los primeros meses."""
    _admin(client)
    _base(A)
    con_precio = make_item(A, code="CAB-211")
    sin_precio = make_item(A, code="CAB-212")
    s = _mk_supplier(A)
    _ingreso(client, s, [(con_precio, 2, "50,00")])

    # Stock que nunca pasó por un ingreso con precio (ej. ajuste, conteo).
    jaula = A.Location.query.filter_by(name="Jaula TNG").first()
    with A.app.test_request_context():
        A.upsert_stock(sin_precio.id, jaula.id, 7)
        A.db.session.commit()
        snap = A._valorizacion_snapshot()

    assert snap["total_cents"] == 10000         # solo el ítem con precio
    assert snap["sin_costo_items"] == 1
    assert snap["sin_costo_unidades"] == 7


# --------------------------------------------- corrección del precio (D10-D12)

def test_corregir_precio_no_toca_stock_ni_el_movimiento(A, client):
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-213")
    s = _mk_supplier(A)
    _ingreso(client, s, [(it, 4, "1.000.000,00")])   # el error clásico

    p = A.ItemPurchasePrice.query.one()
    mov_obs = p.movement.observation
    jaula = A.Location.query.filter_by(name="Jaula TNG").first()
    stock_antes = A.Stock.query.filter_by(item_id=it.id, location_id=jaula.id).one().quantity

    r = client.post(f"/costos/precios/{p.id}/editar",
                    data={"line_total": "400.000,00"}, follow_redirects=True)
    assert r.status_code == 200

    p = A.ItemPurchasePrice.query.one()
    assert p.unit_price_cents == 10000000        # $100.000,00
    # Nada más se movió:
    assert A.Stock.query.filter_by(item_id=it.id, location_id=jaula.id).one().quantity == stock_antes
    assert p.movement.qty == 4
    assert p.movement.observation == mov_obs
    assert A.Movement.query.count() == 1         # no se generó contra-movimiento


def test_corregir_precio_deja_rastro_y_recalcula_el_promedio(A, client):
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-214")
    s = _mk_supplier(A)
    _ingreso(client, s, [(it, 1, "500,00")])
    p = A.ItemPurchasePrice.query.one()

    client.post(f"/costos/precios/{p.id}/editar",
                data={"line_total": "300,00"}, follow_redirects=True)

    e = A.ItemPriceEdit.query.one()
    assert e.old_unit_price_cents == 50000
    assert e.new_unit_price_cents == 30000
    assert e.purchase_price_id == p.id
    assert e.edited_by_user_id is not None
    assert A.avg_price_map([it.id])[it.id] == 30000


def test_precio_invalido_no_cambia_nada(A, client):
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-215")
    s = _mk_supplier(A)
    _ingreso(client, s, [(it, 1, "500,00")])
    p = A.ItemPurchasePrice.query.one()

    client.post(f"/costos/precios/{p.id}/editar",
                data={"line_total": "-3"}, follow_redirects=True)

    assert A.ItemPurchasePrice.query.one().unit_price_cents == 50000
    assert A.ItemPriceEdit.query.count() == 0


def test_fuera_de_la_ventana_el_post_directo_se_rechaza(A, client):
    """La ventana se valida en el backend. Ocultar el botón no es un permiso."""
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-216")
    s = _mk_supplier(A)
    _ingreso(client, s, [(it, 1, "500,00")])

    p = A.ItemPurchasePrice.query.one()
    p.created_at = A.now_ar() - timedelta(minutes=A.COST_PRICE_EDIT_MINUTES + 1)
    A.db.session.commit()

    client.post(f"/costos/precios/{p.id}/editar",
                data={"line_total": "1,00"}, follow_redirects=True)

    assert A.ItemPurchasePrice.query.one().unit_price_cents == 50000
    assert A.ItemPriceEdit.query.count() == 0


def test_dentro_de_la_ventana_el_boton_aparece_y_despues_no(A, client):
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-217")
    s = _mk_supplier(A)
    _ingreso(client, s, [(it, 1, "500,00")])

    html = client.get("/costos/ingresos").get_data(as_text=True)
    assert "Editar precio" in html

    p = A.ItemPurchasePrice.query.one()
    p.created_at = A.now_ar() - timedelta(minutes=A.COST_PRICE_EDIT_MINUTES + 1)
    A.db.session.commit()

    html = client.get("/costos/ingresos").get_data(as_text=True)
    assert "Editar precio" not in html


# ------------------------------------------------------------- permisos (D15)

def test_el_tecnico_no_llega_a_ninguna_ruta_de_costos(A, client):
    make_user(A, "tec", "TECNICO")
    c = A.app.test_client()
    login(c, "tec")
    # El sistema deniega con redirect a home (302), no con 403: es el criterio
    # que ya usa role_required en todas las rutas. Lo que importa es que NO entre.
    for url in ("/costos/ingresos", "/costos/valorizacion", "/costos/parametros",
                "/metricas/costos", "/costos/item/1/historial"):
        r = c.get(url)
        assert r.status_code in (302, 403), f"{url} devolvió {r.status_code}"
        assert r.status_code != 200


def test_el_tecnico_no_puede_corregir_un_precio_por_post_directo(A, client):
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-218")
    s = _mk_supplier(A)
    _ingreso(client, s, [(it, 1, "500,00")])
    p = A.ItemPurchasePrice.query.one()

    make_user(A, "tec2", "TECNICO")
    c = A.app.test_client()
    login(c, "tec2")
    assert c.post(f"/costos/precios/{p.id}/editar",
                  data={"line_total": "1,00"}).status_code in (302, 403)
    assert A.ItemPurchasePrice.query.one().unit_price_cents == 50000


def test_el_lector_ve_pero_no_edita(A, client):
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-219")
    s = _mk_supplier(A)
    _ingreso(client, s, [(it, 1, "500,00")])
    p = A.ItemPurchasePrice.query.one()

    make_user(A, "lec", "LECTOR")
    c = A.app.test_client()
    login(c, "lec")

    assert c.get("/costos/ingresos").status_code == 200
    assert c.get("/costos/valorizacion").status_code == 200
    assert c.get("/metricas/costos").status_code == 200
    # Ver sí, tocar no:
    assert "Editar precio" not in c.get("/costos/ingresos").get_data(as_text=True)
    assert c.post(f"/costos/precios/{p.id}/editar",
                  data={"line_total": "1,00"}).status_code in (302, 403)
    assert A.ItemPurchasePrice.query.one().unit_price_cents == 50000
    assert c.get("/costos/parametros").status_code in (302, 403)


def test_solo_admin_toca_los_parametros(A, client):
    make_user(A, "sup", "SUPERVISOR")
    c = A.app.test_client()
    login(c, "sup")
    assert c.get("/costos/parametros").status_code in (302, 403)

    _admin(client)
    assert client.get("/costos/parametros").status_code == 200
    client.post("/costos/parametros",
                data={"fecha_inicio": "2026-08-21", "preset_default": "mes"},
                follow_redirects=True)
    with A.app.test_request_context():
        assert A.get_setting(A.SETTING_COST_START_DATE) == "2026-08-21"
        assert A.get_setting(A.SETTING_COST_DEFAULT_PRESET) == "mes"


def test_parametros_rechaza_valores_invalidos(A, client):
    _admin(client)
    client.post("/costos/parametros",
                data={"fecha_inicio": "no-es-fecha", "preset_default": "mes"},
                follow_redirects=True)
    with A.app.test_request_context():
        assert A.get_setting(A.SETTING_COST_START_DATE, "") == ""


# ----------------------------------------------------------------- pantallas

def test_items_muestra_el_promedio_y_ordena_por_precio(A, client):
    _admin(client)
    _base(A)
    barato = make_item(A, code="CAB-220", name="Barato")
    caro = make_item(A, code="CAB-221", name="Caro")
    s = _mk_supplier(A)
    _ingreso(client, s, [(barato, 1, "10,00")])
    _ingreso(client, s, [(caro, 1, "900,00")])

    html = client.get("/items").get_data(as_text=True)
    assert "Precio prom." in html
    assert "$ 900,00" in html

    # Ordenar por precio no rompe y respeta la dirección. Se mira SOLO el
    # <tbody> de la tabla: el <select> de filtros también lista los códigos, y
    # ese va siempre ordenado por código.
    def _cuerpo(url):
        h = client.get(url).get_data(as_text=True)
        return h[h.index("<tbody>"):h.index("</tbody>")]

    cuerpo = _cuerpo("/items?sort_by=avg_price&sort_dir=desc")
    assert cuerpo.index("CAB-221") < cuerpo.index("CAB-220")
    cuerpo = _cuerpo("/items?sort_by=avg_price&sort_dir=asc")
    assert cuerpo.index("CAB-220") < cuerpo.index("CAB-221")


def test_historial_de_precios_de_un_item_en_json(A, client):
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-222")
    s = _mk_supplier(A, "Proveedor Uno")
    _ingreso(client, s, [(it, 2, "100,00")])
    _ingreso(client, s, [(it, 3, "200,00")])

    data = client.get(f"/costos/item/{it.id}/historial").get_json()
    assert len(data["ingresos"]) == 2
    assert data["promedio_simple"] == "$ 150,00"
    assert data["promedio_ponderado"] == "$ 160,00"   # (2x100 + 3x200)/5
    assert data["ingresos"][0]["proveedor"] == "Proveedor Uno"


def test_total_del_filtro_coincide_con_lo_cargado(A, client):
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-223")
    s = _mk_supplier(A)
    _ingreso(client, s, [(it, 3, "100,00")])     # 300
    _ingreso(client, s, [(it, 2, "50,00")])      # 100

    html = client.get("/costos/ingresos").get_data(as_text=True)
    assert "$ 400,00" in html


def test_metricas_costos_el_gasto_es_dato_duro(A, client):
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-224")
    s = _mk_supplier(A)
    _ingreso(client, s, [(it, 4, "250,00")])     # gasto = $1.000,00

    html = client.get("/metricas/costos").get_data(as_text=True)
    assert html.count("$ 1.000,00") >= 1
    assert "Gasto del período" in html


def test_las_pantallas_de_costos_no_rompen_vacias(A, client):
    """Sin un solo precio cargado, todo tiene que renderizar igual."""
    _admin(client)
    for url in ("/costos/ingresos", "/costos/valorizacion", "/costos/parametros",
                "/metricas/costos"):
        assert client.get(url).status_code == 200, url


def test_el_menu_dice_scrap_y_no_basura(A, client):
    _admin(client)
    html = client.get("/").get_data(as_text=True)
    assert "Scrap" in html
    assert "Basura" not in html


# ------------------------------------------- carga por TOTAL de línea (D3 rev.)

def test_el_unitario_se_deriva_del_total(A, client):
    """Se carga lo que dice el remito ($15.005,00 por 10) y el sistema saca el
    unitario. Es lo que evita la división a mano, sobre todo en metros."""
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-230")
    s = _mk_supplier(A)

    _ingreso_total(client, s, it, 10, "15.005,00")

    p = A.ItemPurchasePrice.query.one()
    assert p.total_price_cents == 1500500
    assert p.unit_price_cents == 150050          # 15.005,00 / 10 = 1.500,50
    assert p.total_cents == 1500500


def test_el_total_no_pierde_centavos_aunque_no_divida_exacto(A, client):
    """$1.000 entre 3 da $333,3333: el unitario redondea, pero el TOTAL que se
    informa como gasto sigue siendo el real. Si se guardara solo el unitario,
    el gasto diría $999,99."""
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-231")
    s = _mk_supplier(A)

    _ingreso_total(client, s, it, 3, "1.000,00")

    p = A.ItemPurchasePrice.query.one()
    assert p.unit_price_cents == 33333           # redondeado
    assert p.total_cents == 100000               # el total NO se recalcula
    assert p.unit_price_cents * p.qty == 99999   # lo que se habría perdido

    # El gasto informa el total real, no la reconstrucción desde el unitario.
    html = client.get("/metricas/costos").get_data(as_text=True)
    assert "GASTO DEL PERÍODO" in html.upper()
    assert "$ 1.000,00" in html
    # El total del registro de precios sale de la misma expresión SQL.
    assert "$ 1.000,00" in client.get("/costos/ingresos").get_data(as_text=True)

    # Matiz esperado: el VALOR DEL STOCK sí pasa por el promedio unitario, así
    # que ahí el redondeo se nota ($ 999,99). Es correcto — el gasto es un dato
    # duro y la valorización es una estimación, y así lo dice la pantalla.


def test_un_total_que_da_menos_de_un_centavo_por_unidad_se_rechaza(A, client):
    """Un unitario de cero rompería el promedio del ítem en silencio."""
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-232", name="Cable")
    s = _mk_supplier(A)

    _ingreso_total(client, s, it, 300, "0,50")   # menos de 1 centavo por metro

    assert A.Movement.query.count() == 0
    assert A.ItemPurchasePrice.query.count() == 0


def test_corregir_el_total_recalcula_el_unitario(A, client):
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-233")
    s = _mk_supplier(A)
    _ingreso_total(client, s, it, 4, "4.000.000,00")     # el cero de más

    p = A.ItemPurchasePrice.query.one()
    assert p.unit_price_cents == 100000000

    client.post(f"/costos/precios/{p.id}/editar",
                data={"line_total": "400.000,00"}, follow_redirects=True)

    p = A.ItemPurchasePrice.query.one()
    assert p.total_price_cents == 40000000
    assert p.unit_price_cents == 10000000               # 400.000 / 4
    e = A.ItemPriceEdit.query.one()
    assert e.old_total_cents == 400000000 and e.new_total_cents == 40000000
    assert e.old_unit_price_cents == 100000000 and e.new_unit_price_cents == 10000000


def test_los_precios_viejos_sin_total_siguen_andando(A, client):
    """Compatibilidad: las filas cargadas antes de este cambio no tienen
    total_price_cents y su total se reconstruye como unitario x cantidad."""
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-234")
    s = _mk_supplier(A)
    _ingreso_total(client, s, it, 5, "500,00")

    p = A.ItemPurchasePrice.query.one()
    p.total_price_cents = None                  # simula una fila vieja
    A.db.session.commit()

    assert A.ItemPurchasePrice.query.one().total_cents == 50000   # 100,00 x 5
    assert client.get("/costos/ingresos").status_code == 200
    assert "$ 500,00" in client.get("/costos/ingresos").get_data(as_text=True)


# --------------------------------------------- remito: "En concepto de" (nuevo)

def test_el_remito_de_ingreso_dice_en_concepto_de(A, client):
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-235")
    s = _mk_supplier(A, "Juan Perez")
    _ingreso_total(client, s, it, 2, "200,00")

    rem = A.Remito.query.order_by(A.Remito.id.desc()).first()
    assert rem.concept == "Ingreso de mercadería — Proveedor: Juan Perez"
    assert rem.observation is None

    html = client.get(f"/remitos/{rem.id}").get_data(as_text=True)
    assert "EN CONCEPTO DE" in html
    assert "Ingreso de mercadería" in html


def test_la_observacion_manual_queda_separada_del_concepto(A, client):
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-236")
    s = _mk_supplier(A)
    client.post("/ingresos-egresos", data={
        "tipo": "INGRESO", "supplier_id": str(s.id),
        "observation": "llegó sin caja",
        "item_id[]": [str(it.id)], "qty[]": ["2"],
        "line_serials[]": [""], "line_total[]": ["200,00"],
    }, follow_redirects=True)

    rem = A.Remito.query.order_by(A.Remito.id.desc()).first()
    assert rem.observation == "llegó sin caja"
    assert "llegó sin caja" not in rem.concept


def test_el_remito_manual_dice_traslado_interno(A, client):
    _admin(client)
    jaula = make_location(A, "Jaula TNG")
    truck = make_location(A, "Camioneta Uno", is_truck=True)
    it = make_item(A, code="CAB-237")
    resp = make_user(A, "tec_rem", "TECNICO")
    A.db.session.add(A.LocationResponsible(location_id=truck.id, user_id=resp.id))
    admin = A.User.query.filter_by(username="admin").first()
    A.db.session.add(A.LocationResponsible(location_id=jaula.id, user_id=admin.id))
    A.db.session.commit()

    with A.app.test_request_context():
        A.upsert_stock(it.id, jaula.id, 5)
        y, seq, num = A.next_movement_number()
        m = A.Movement(item_id=it.id, qty=2, from_location_id=jaula.id,
                       to_location_id=truck.id, user_id=admin.id,
                       year=y, seq=seq, number=num)
        A.db.session.add(m)
        A.db.session.commit()
        mid = m.id

    client.post("/remitos/new", data={
        "from_location_id": str(jaula.id), "to_location_id": str(truck.id),
        "responsible_from_id": str(admin.id), "responsible_to_id": str(resp.id),
        "movement_id": [str(mid)], "observation": "para la obra del centro",
    }, follow_redirects=True)

    rem = A.Remito.query.order_by(A.Remito.id.desc()).first()
    assert rem.concept == "Traslado interno"
    assert rem.observation == "para la obra del centro"


def test_los_remitos_viejos_no_muestran_la_franja(A, client):
    """Nada de lo ya emitido se reescribe: sin concepto, el remito se ve igual
    que siempre y su observación original queda al pie."""
    _admin(client)
    _base(A)
    it = make_item(A, code="CAB-238")
    s = _mk_supplier(A)
    _ingreso_total(client, s, it, 1, "100,00")

    rem = A.Remito.query.order_by(A.Remito.id.desc()).first()
    rem.concept = None                                  # simula uno viejo
    rem.observation = "Ingreso · ACME · algo viejo"
    A.db.session.commit()

    html = client.get(f"/remitos/{rem.id}").get_data(as_text=True)
    assert "EN CONCEPTO DE" not in html
    assert "Ingreso · ACME · algo viejo" in html
