"""Tarea 2: importación de items (solo altas)."""
import io
from conftest import make_category, make_item, login


def _post_csv(client, raw_bytes, filename="items.csv"):
    return client.post(
        "/import/items",
        data={"file": (io.BytesIO(raw_bytes), filename)},
        content_type="multipart/form-data",
        follow_redirects=False,
    )


def _admin_client(A, client):
    login(client, "admin", "admin123")
    make_category(A, "Cables", "CAB")
    return client


def test_csv_valido_coma(A, client):
    _admin_client(A, client)
    csv = b"code,name,category\nCAB-001,Cable 1,Cables\nCAB-002,Cable 2,Cables\n"
    _post_csv(client, csv)
    assert A.Item.query.count() == 2


def test_separador_punto_y_coma(A, client):
    _admin_client(A, client)
    csv = b"code;name;category\nCAB-101;Cable A;Cables\nCAB-102;Cable B;Cables\n"
    _post_csv(client, csv)
    assert A.Item.query.count() == 2


def test_utf8_con_bom(A, client):
    _admin_client(A, client)
    csv = "code,name,category\nCAB-201,Cañería,Cables\n".encode("utf-8-sig")
    _post_csv(client, csv)
    it = A.Item.query.filter_by(code="CAB-201").first()
    assert it is not None and it.name == "Cañería"


def test_cp1252(A, client):
    _admin_client(A, client)
    csv = "code,name,category\nCAB-301,Conexión,Cables\n".encode("cp1252")
    _post_csv(client, csv)
    it = A.Item.query.filter_by(code="CAB-301").first()
    assert it is not None and it.name == "Conexión"


def test_codigo_existente_en_db(A, client):
    _admin_client(A, client)
    make_item(A, code="CAB-001", name="Ya existe")
    csv = b"code,name,category\nCAB-001,Duplicado,Cables\nCAB-999,Nuevo,Cables\n"
    _post_csv(client, csv)
    # el existente se salta; solo entra el nuevo
    assert A.Item.query.filter_by(code="CAB-001").first().name == "Ya existe"
    assert A.Item.query.filter_by(code="CAB-999").first() is not None
    assert A.Item.query.count() == 2


def test_duplicado_interno_del_csv(A, client):
    _admin_client(A, client)
    csv = b"code,name,category\nCAB-500,Uno,Cables\nCAB-500,Dos,Cables\n"
    _post_csv(client, csv)
    # solo se crea una vez, sin romper el commit por UNIQUE
    assert A.Item.query.filter_by(code="CAB-500").count() == 1


def test_categoria_inexistente(A, client):
    _admin_client(A, client)
    csv = b"code,name,category\nZZZ-001,Item,CategoriaQueNoExiste\n"
    _post_csv(client, csv)
    assert A.Item.query.filter_by(code="ZZZ-001").first() is None


def test_encabezados_faltantes(A, client):
    _admin_client(A, client)
    csv = b"code,name\nCAB-001,Sin categoria\n"  # falta 'category'
    r = _post_csv(client, csv)
    assert A.Item.query.count() == 0
    # No redirige al listado de items: vuelve a la página de importación.
    loc = r.headers.get("Location") or ""
    assert loc.endswith("/import/items")


def test_trackable_invalido(A, client):
    _admin_client(A, client)
    csv = b"code,name,category,trackable\nCAB-600,Item,Cables,quiza\n"
    _post_csv(client, csv)
    assert A.Item.query.filter_by(code="CAB-600").first() is None


def test_stock_min_negativo(A, client):
    _admin_client(A, client)
    csv = b"code,name,category,stock_min\nCAB-700,Item,Cables,-3\n"
    _post_csv(client, csv)
    assert A.Item.query.filter_by(code="CAB-700").first() is None


def test_stock_min_vacio_es_cero(A, client):
    _admin_client(A, client)
    csv = b"code,name,category,stock_min\nCAB-710,Item,Cables,\n"
    _post_csv(client, csv)
    it = A.Item.query.filter_by(code="CAB-710").first()
    assert it is not None and it.stock_min == 0


def test_reference_link_importado(A, client):
    # La columna reference_link del CSV se guarda REALMENTE en el item.
    _admin_client(A, client)
    csv = b"code,name,category,reference_link\nCAB-800,Item,Cables,http://x/y\n"
    _post_csv(client, csv)
    it = A.Item.query.filter_by(code="CAB-800").first()
    assert it is not None and it.reference_link == "http://x/y"


def test_commit_fallido_hace_rollback(A, client, monkeypatch):
    _admin_client(A, client)

    def boom():
        raise RuntimeError("db caida simulada")

    monkeypatch.setattr(A.db.session, "commit", boom)
    csv = b"code,name,category\nCAB-900,Item,Cables\n"
    _post_csv(client, csv)
    monkeypatch.undo()
    # No debe quedar nada creado ni la sesión en error
    assert A.Item.query.filter_by(code="CAB-900").first() is None


def test_archivo_vacio(A, client):
    _admin_client(A, client)
    _post_csv(client, b"")
    assert A.Item.query.count() == 0


def test_archivo_sin_headers_validos(A, client):
    _admin_client(A, client)
    _post_csv(client, b"\x00\x01basura sin encabezados\n")
    assert A.Item.query.count() == 0
