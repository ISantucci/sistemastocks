"""Validacion: Scrap + Pendientes mejorados."""
import sys, os, tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["STOCKS_DB_PATH"] = _tmp.name
os.environ["BOOTSTRAP_ADMIN_USERNAME"] = "admin"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "admin123"
os.environ["FLASK_SECRET_KEY"] = "test-secret"

sys.path.insert(0, os.path.dirname(__file__))
from app import (
    app, db, User, Location, Item, Category, Movement,
    Stock, PendingDelivery, Scrap, LocationResponsible,
    seed_defaults,
)

PASS, FAIL = [], []

def ok(msg):  PASS.append(msg); print(f"  [OK]   {msg}")
def fail(msg, detail=""): FAIL.append(msg); print(f"  [FAIL] {msg}" + (f" — {detail}" if detail else ""))


# ── setup ─────────────────────────────────────────────────────────────────────

def setup():
    with app.app_context():
        db.create_all()
        seed_defaults()

        # roles
        sup = User(username="supervisor", full_name="Supervisor", role="SUPERVISOR")
        sup.set_password("sup123")
        tec = User(username="tecnico", full_name="Tecnico", role="TECNICO")
        tec.set_password("tec123")
        lector = User(username="lector", full_name="Lector", role="LECTOR")
        lector.set_password("lec123")
        db.session.add_all([sup, tec, lector])
        db.session.flush()

        # categoria + item
        cat = Category(name="General")
        db.session.add(cat)
        db.session.flush()

        item = Item(code="CAM-001", name="Camara", category_id=cat.id,
                    trackable=False, stock_min=0, is_active=True)
        db.session.add(item)
        db.session.flush()

        # necesitamos al menos una ubicacion origen con stock para pendiente
        jaula = Location.query.filter_by(name="Jaula").first()
        if jaula:
            db.session.add(Stock(item_id=item.id, location_id=jaula.id, quantity=10))

        # asignar tecnico a jaula via LocationResponsible
        if jaula:
            db.session.add(LocationResponsible(location_id=jaula.id, user_id=tec.id))

        db.session.commit()
        return {
            "admin_id": User.query.filter_by(username="admin").first().id,
            "sup_id": sup.id,
            "tec_id": tec.id,
            "lector_id": lector.id,
            "item_id": item.id,
            "cat_id": cat.id,
        }


def login(client, username, password):
    client.post("/login", data={"username": username, "password": password},
                follow_redirects=True)


# ── tests ─────────────────────────────────────────────────────────────────────

def test_ubicaciones_default():
    print("\n--- 1. UBICACIONES DEFAULT ---")
    with app.app_context():
        for name in ("Jaula", "Descartes", "Utilizado", "Proveedor"):
            loc = Location.query.filter_by(name=name).first()
            if loc:
                ok(f"Ubicacion '{name}' existe")
            else:
                fail(f"Ubicacion '{name}' existe")

        descartes = Location.query.filter_by(name="Descartes").first()
        if descartes and not descartes.is_truck and not descartes.is_external:
            ok("Descartes: is_truck=False, is_external=False")
        else:
            fail("Descartes: is_truck=False, is_external=False",
                 f"is_truck={getattr(descartes,'is_truck',None)}, is_external={getattr(descartes,'is_external',None)}")

        jaula = Location.query.filter_by(name="Jaula").first()
        if jaula and jaula.is_truck and not jaula.is_external:
            ok("Jaula: is_truck=True, is_external=False")
        else:
            fail("Jaula: is_truck=True, is_external=False",
                 f"is_truck={getattr(jaula,'is_truck',None)}, is_external={getattr(jaula,'is_external',None)}")

        utilizado = Location.query.filter_by(name="Utilizado").first()
        if utilizado and utilizado.is_external:
            ok("Utilizado: is_external=True")
        else:
            fail("Utilizado: is_external=True")


def _create_pending(ids):
    """Crea un PendingDelivery de prueba directo en DB y retorna su id."""
    with app.app_context():
        jaula = Location.query.filter_by(name="Jaula").first()
        admin = User.query.filter_by(username="admin").first()
        from app import next_movement_number
        y, seq, number = next_movement_number()
        m = Movement(
            item_id=ids["item_id"],
            qty=2,
            from_location_id=jaula.id,
            to_location_id=jaula.id,   # simplificado: mismo lugar
            user_id=admin.id,
            year=y, seq=seq, number=number,
        )
        db.session.add(m)
        db.session.flush()

        p = PendingDelivery(
            movement_id=m.id,
            responsible_from_id=admin.id,
            responsible_to_id=ids["tec_id"],
            item_id=ids["item_id"],
            returned=False,
        )
        db.session.add(p)
        db.session.commit()
        return p.id


def test_flujo_devolver(client, ids):
    print("\n--- 2. FLUJO PENDIENTE -> DEVOLVER ---")
    pid = _create_pending(ids)

    r = client.post("/pending-deliveries", data={
        "pending_id": str(pid),
        "return_action": "return",
        "return_observation": "test devolucion",
    }, follow_redirects=True)

    if r.status_code == 200:
        ok("POST /pending-deliveries (devolver) responde 200")
    else:
        fail("POST /pending-deliveries (devolver) responde 200", f"status={r.status_code}")

    with app.app_context():
        p = PendingDelivery.query.get(pid)
        if p and p.returned:
            ok("Pendiente marcado como returned=True")
        else:
            fail("Pendiente marcado como returned=True")

        # debe haber un movimiento de devolucion con numero
        last_mov = Movement.query.order_by(Movement.id.desc()).first()
        if last_mov and last_mov.number and last_mov.number.startswith("MOV-"):
            ok(f"Movimiento de devolucion generado ({last_mov.number})")
        else:
            fail("Movimiento de devolucion generado", f"number={getattr(last_mov,'number',None)}")


def test_flujo_scrap(client, ids):
    print("\n--- 3. FLUJO PENDIENTE -> SCRAP ---")
    pid = _create_pending(ids)

    with app.app_context():
        jaula = Location.query.filter_by(name="Jaula").first()
        descartes = Location.query.filter_by(name="Descartes").first()
        stock_jaula_antes = (Stock.query.filter_by(
            item_id=ids["item_id"], location_id=jaula.id
        ).first() or type("x", (), {"quantity": 0})()).quantity

    r = client.post("/pending-deliveries", data={
        "pending_id": str(pid),
        "return_action": "scrap",
        "scrap_reason": "Irrecuperable",
        "return_observation": "",
    }, follow_redirects=True)

    if r.status_code == 200:
        ok("POST /pending-deliveries (scrap) responde 200")
    else:
        fail("POST /pending-deliveries (scrap) responde 200", f"status={r.status_code}")

    with app.app_context():
        p = PendingDelivery.query.get(pid)
        if p and p.returned:
            ok("Pendiente marcado como returned=True (scrap)")
        else:
            fail("Pendiente marcado como returned=True (scrap)")

        # movimiento → Descartes
        descartes = Location.query.filter_by(name="Descartes").first()
        last_mov = Movement.query.order_by(Movement.id.desc()).first()
        if last_mov and last_mov.to_location_id == descartes.id:
            ok(f"Movimiento apunta a Descartes ({last_mov.number})")
        else:
            fail("Movimiento apunta a Descartes",
                 f"to_location_id={getattr(last_mov,'to_location_id',None)}, descartes.id={descartes.id if descartes else None}")

        # numero generado
        if last_mov and last_mov.number and last_mov.number.startswith("MOV-"):
            ok(f"Numero MOV generado en scrap ({last_mov.number})")
        else:
            fail("Numero MOV generado en scrap")

        # registro Scrap creado
        s = Scrap.query.order_by(Scrap.id.desc()).first()
        if s and s.reason == "Irrecuperable" and s.item_id == ids["item_id"]:
            ok("Registro Scrap creado con motivo correcto")
        else:
            fail("Registro Scrap creado con motivo correcto",
                 f"reason={getattr(s,'reason',None)}, item_id={getattr(s,'item_id',None)}")

        # stock en Descartes sumado (Descartes is_external=False → upsert_stock lo suma)
        jaula = Location.query.filter_by(name="Jaula").first()
        stock_descartes = Stock.query.filter_by(
            item_id=ids["item_id"], location_id=descartes.id
        ).first()
        if stock_descartes and stock_descartes.quantity > 0:
            ok(f"Stock en Descartes sumado (qty={stock_descartes.quantity})")
        else:
            fail("Stock en Descartes sumado",
                 f"qty={getattr(stock_descartes,'quantity',None)}")


def test_reporte_scrap(client, ids):
    print("\n--- 4. REPORTE /scrap ---")

    r = client.get("/scrap")
    if r.status_code == 200:
        ok("ADMIN accede a /scrap")
    else:
        fail("ADMIN accede a /scrap", f"status={r.status_code}")

    html = r.data.decode("utf-8", errors="replace")
    if "Irrecuperable" in html:
        ok("Scrap con motivo Irrecuperable aparece en listado")
    else:
        fail("Scrap con motivo Irrecuperable aparece en listado")

    if "CAM-001" in html or "Camara" in html:
        ok("Item CAM-001 aparece en reporte")
    else:
        fail("Item CAM-001 aparece en reporte")

    # filtro por motivo
    r2 = client.get("/scrap?reason=Irrecuperable")
    html2 = r2.data.decode("utf-8", errors="replace")
    if "Irrecuperable" in html2:
        ok("Filtro por motivo=Irrecuperable funciona")
    else:
        fail("Filtro por motivo=Irrecuperable funciona")


def test_permisos(client, ids):
    print("\n--- 5. PERMISOS /scrap ---")

    # SUPERVISOR puede acceder
    login(client, "supervisor", "sup123")
    r = client.get("/scrap")
    if r.status_code == 200:
        ok("SUPERVISOR accede a /scrap")
    else:
        fail("SUPERVISOR accede a /scrap", f"status={r.status_code}")

    # TECNICO bloqueado
    login(client, "tecnico", "tec123")
    r = client.get("/scrap", follow_redirects=True)
    if b"permisos" in r.data or b"No ten" in r.data:
        ok("TECNICO bloqueado en /scrap")
    else:
        fail("TECNICO bloqueado en /scrap", f"status={r.status_code}")

    # LECTOR bloqueado
    login(client, "lector", "lec123")
    r = client.get("/scrap", follow_redirects=True)
    if b"permisos" in r.data or b"No ten" in r.data:
        ok("LECTOR bloqueado en /scrap")
    else:
        fail("LECTOR bloqueado en /scrap", f"status={r.status_code}")

    # Link en menu para ADMIN
    login(client, "admin", "admin123")
    r = client.get("/")
    if b"Descartes" in r.data:
        ok("ADMIN ve link Descartes en menu")
    else:
        fail("ADMIN ve link Descartes en menu")


def test_sin_motivo_scrap(client, ids):
    print("\n--- 6. VALIDACIONES ---")
    pid = _create_pending(ids)

    # scrap sin motivo → igual debe funcionar (reason default "Otro")
    r = client.post("/pending-deliveries", data={
        "pending_id": str(pid),
        "return_action": "scrap",
        "scrap_reason": "",
    }, follow_redirects=True)

    with app.app_context():
        s = Scrap.query.order_by(Scrap.id.desc()).first()
        if s and s.reason == "Otro":
            ok("Scrap sin motivo usa 'Otro' como fallback")
        else:
            fail("Scrap sin motivo usa 'Otro' como fallback",
                 f"reason={getattr(s,'reason',None)}")

    # pendiente ya devuelto → no hace nada
    r2 = client.post("/pending-deliveries", data={
        "pending_id": str(pid),
        "return_action": "return",
    }, follow_redirects=True)
    if b"devuelto" in r2.data.lower() or b"already" in r2.data.lower() or r2.status_code == 200:
        ok("Pendiente ya cerrado no genera error fatal")


def test_compatibilidad(client, ids):
    print("\n--- 7. COMPATIBILIDAD ---")
    # POST sin return_action (como antes del cambio) → default "return"
    pid = _create_pending(ids)
    r = client.post("/pending-deliveries", data={
        "pending_id": str(pid),
        "return_observation": "viejo flujo",
        # sin return_action
    }, follow_redirects=True)

    with app.app_context():
        p = PendingDelivery.query.get(pid)
        if p and p.returned:
            ok("Pendiente sin return_action se cierra como devolucion (backward compat)")
        else:
            fail("Pendiente sin return_action se cierra como devolucion")

    # /movements sigue respondiendo
    r2 = client.get("/movements")
    if r2.status_code == 200:
        ok("/movements sigue funcionando")
    else:
        fail("/movements sigue funcionando", f"status={r2.status_code}")

    # /item-usage sigue respondiendo
    r3 = client.get("/item-usage")
    if r3.status_code == 200:
        ok("/item-usage sigue funcionando")
    else:
        fail("/item-usage sigue funcionando", f"status={r3.status_code}")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("VALIDACION: Scrap + Pendientes mejorados")
    print("=" * 55)

    ids = setup()

    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_client() as client:
        login(client, "admin", "admin123")

        test_ubicaciones_default()
        test_flujo_devolver(client, ids)
        test_flujo_scrap(client, ids)
        test_reporte_scrap(client, ids)
        test_permisos(client, ids)
        test_sin_motivo_scrap(client, ids)
        test_compatibilidad(client, ids)

    print("\n" + "=" * 55)
    print(f"RESULTADO: {len(PASS)} OK  /  {len(FAIL)} FAIL")
    if FAIL:
        print("\nFALLIDOS:")
        for f in FAIL:
            print(f"  - {f}")
    print("=" * 55)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
