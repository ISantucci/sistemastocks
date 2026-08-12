"""Proveedores asociados a un ítem (relación N a N) + export a Excel.

Cubre:
  - alta y edición del vínculo ítem <-> proveedores
  - proveedor preferido
  - que un formulario que NO gestiona proveedores no borre los existentes
  - que no se pueda vincular un proveedor dado de baja
  - export /items/export.xlsx: permisos, contenido y respeto de filtros
"""
import io

import pytest
from conftest import make_user, make_category, login


@pytest.fixture()
def env(A):
    A.app.config["WTF_CSRF_ENABLED"] = False
    make_user(A, "sup", "SUPERVISOR")
    make_user(A, "lec", "LECTOR")
    make_user(A, "tec", "TECNICO")
    cat = make_category(A, "Electricidad", "ELT")

    act1 = A.Supplier(contact_name="Juan Perez", business_name="ElectroSur", is_active=True)
    act2 = A.Supplier(contact_name="Ana Lopez", business_name="CablesYA", is_active=True)
    baja = A.Supplier(contact_name="Viejo SA", is_active=False)
    A.db.session.add_all([act1, act2, baja])

    it = A.Item(code="ELT-001", name="Cable 2.5mm", category_id=cat.id, unit="metros", stock_min=5)
    A.db.session.add(it)
    A.db.session.commit()

    c = A.app.test_client()
    return A, c, it.id, act1.id, act2.id, baja.id, cat.id


def _edit(c, item_id, **extra):
    data = {"name": "Cable 2.5mm", "is_active": "on", "unit": "metros"}
    data.update(extra)
    return c.post(f"/items/{item_id}/edit", data=data, follow_redirects=True)


def test_asigna_varios_proveedores_y_preferido(env):
    A, c, item_id, s1, s2, _baja, _cat = env
    login(c, "sup", "pass1234")
    _edit(c, item_id, manages_suppliers="1", supplier_ids=[str(s1), str(s2)],
          preferred_supplier_id=str(s2))

    it = A.db.session.get(A.Item, item_id)
    assert {l.supplier_id for l in it.supplier_links} == {s1, s2}
    assert [l.supplier_id for l in it.supplier_links if l.is_preferred] == [s2]
    # El preferido se muestra primero y marcado.
    assert A.item_supplier_names(it).startswith("CablesYA ★")


def test_permite_vaciar_todos_los_proveedores(env):
    A, c, item_id, s1, s2, _baja, _cat = env
    login(c, "sup", "pass1234")
    _edit(c, item_id, manages_suppliers="1", supplier_ids=[str(s1), str(s2)])
    assert len(A.db.session.get(A.Item, item_id).supplier_links) == 2

    # Un <select multiple> vacío no manda la clave: el marcador es lo que
    # permite distinguir "vaciar" de "este form no gestiona proveedores".
    _edit(c, item_id, manages_suppliers="1")
    assert A.db.session.get(A.Item, item_id).supplier_links == []


def test_form_sin_marcador_no_borra_proveedores(env):
    A, c, item_id, s1, _s2, _baja, _cat = env
    login(c, "sup", "pass1234")
    _edit(c, item_id, manages_suppliers="1", supplier_ids=[str(s1)])
    _edit(c, item_id, name="Cable renombrado")  # sin manages_suppliers

    it = A.db.session.get(A.Item, item_id)
    assert [l.supplier_id for l in it.supplier_links] == [s1]
    assert it.name == "Cable renombrado"


def test_no_vincula_proveedor_dado_de_baja(env):
    A, c, item_id, s1, _s2, baja, _cat = env
    login(c, "sup", "pass1234")
    _edit(c, item_id, manages_suppliers="1", supplier_ids=[str(s1), str(baja)])

    it = A.db.session.get(A.Item, item_id)
    assert [l.supplier_id for l in it.supplier_links] == [s1]


def test_preferido_debe_estar_entre_los_elegidos(env):
    A, c, item_id, s1, s2, _baja, _cat = env
    login(c, "sup", "pass1234")
    _edit(c, item_id, manages_suppliers="1", supplier_ids=[str(s1)],
          preferred_supplier_id=str(s2))  # s2 no está elegido

    it = A.db.session.get(A.Item, item_id)
    assert [l.supplier_id for l in it.supplier_links] == [s1]
    assert not any(l.is_preferred for l in it.supplier_links)


def test_alta_de_item_con_proveedores(env):
    A, c, _item_id, s1, s2, _baja, cat_id = env
    login(c, "sup", "pass1234")
    c.post("/items/new", data={
        "category_id": str(cat_id), "name": "Zapatilla 6 tomas", "unit": "unidad",
        "stock_min": "2", "manages_suppliers": "1",
        "supplier_ids": [str(s1), str(s2)], "preferred_supplier_id": str(s1),
    }, follow_redirects=True)

    nuevo = A.Item.query.filter_by(name="Zapatilla 6 tomas").first()
    assert nuevo is not None
    assert {l.supplier_id for l in nuevo.supplier_links} == {s1, s2}
    assert [l.supplier_id for l in nuevo.supplier_links if l.is_preferred] == [s1]


# ------------------ export a Excel ------------------

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.mark.parametrize("user,pw,permitido", [
    ("admin", "admin123", True),
    ("sup", "pass1234", True),
    ("lec", "pass1234", True),
    ("tec", "pass1234", False),
])
def test_export_permisos(env, user, pw, permitido):
    _A, c, *_ = env
    login(c, user, pw)
    r = c.get("/items/export.xlsx")
    if permitido:
        assert r.status_code == 200
        assert r.headers["Content-Type"].startswith(XLSX_MIME)
    else:
        assert r.status_code == 302  # redirigido a home


def test_export_incluye_proveedores(env):
    A, c, item_id, s1, s2, _baja, _cat = env
    login(c, "sup", "pass1234")
    _edit(c, item_id, manages_suppliers="1", supplier_ids=[str(s1), str(s2)],
          preferred_supplier_id=str(s1))

    openpyxl = pytest.importorskip("openpyxl")
    r = c.get("/items/export.xlsx")
    ws = openpyxl.load_workbook(io.BytesIO(r.data)).active

    encabezados = [c.value for c in ws[1]]
    assert "Proveedores" in encabezados
    col = encabezados.index("Proveedores") + 1
    assert ws.cell(row=2, column=col).value == "ElectroSur ★, CablesYA"


def test_export_respeta_filtros_y_orden(env):
    A, c, _item_id, _s1, _s2, _baja, cat_id = env
    login(c, "sup", "pass1234")
    c.post("/items/new", data={
        "category_id": str(cat_id), "name": "Zapatilla 6 tomas",
        "unit": "unidad", "stock_min": "0",
    }, follow_redirects=True)

    openpyxl = pytest.importorskip("openpyxl")

    r = c.get("/items/export.xlsx?q=Zapatilla")
    ws = openpyxl.load_workbook(io.BytesIO(r.data)).active
    assert ws.max_row == 2  # encabezado + 1 fila

    r = c.get("/items/export.xlsx?sort_by=name&sort_dir=desc")
    ws = openpyxl.load_workbook(io.BytesIO(r.data)).active
    nombres = [ws.cell(row=i, column=2).value for i in range(2, ws.max_row + 1)]
    assert nombres == ["Zapatilla 6 tomas", "Cable 2.5mm"]
