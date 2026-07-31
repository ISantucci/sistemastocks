"""Tarea 1: numeración de remitos."""


def test_primer_numero_del_anio(A):
    y, seq, number = A.next_remito_number(2030)
    assert (y, seq) == (2030, 1)
    assert number == "R-2030-0001"


def test_formato_cuatro_digitos(A):
    _, _, number = A.next_remito_number(2031)
    assert number == "R-2031-0001"
    assert number.split("-")[-1].isdigit() and len(number.split("-")[-1]) == 4


def _crear_remito(A, year, seq):
    loc_a = A.Location(name=f"A{year}{seq}")
    loc_b = A.Location(name=f"B{year}{seq}")
    A.db.session.add_all([loc_a, loc_b])
    A.db.session.flush()
    admin = A.User.query.filter_by(role="ADMIN").first()
    r = A.Remito(year=year, seq=seq, number=f"R-{year}-{seq:04d}", status="CONFIRMADO",
                 from_location_id=loc_a.id, to_location_id=loc_b.id,
                 created_by_user_id=admin.id)
    A.db.session.add(r)
    A.db.session.commit()


def test_incremento(A):
    _crear_remito(A, 2032, 1)
    y, seq, number = A.next_remito_number(2032)
    assert (y, seq, number) == (2032, 2, "R-2032-0002")


def test_separacion_por_anio(A):
    _crear_remito(A, 2033, 41)
    # 2033 sigue en 42
    assert A.next_remito_number(2033) == (2033, 42, "R-2033-0042")
    # el año siguiente arranca de nuevo en 1
    assert A.next_remito_number(2034) == (2034, 1, "R-2034-0001")


def test_uso_normal_sin_argumentos(A):
    # No debe romperse llamando sin year (usa el año actual).
    y, seq, number = A.next_remito_number()
    assert number == f"R-{y}-{seq:04d}"
