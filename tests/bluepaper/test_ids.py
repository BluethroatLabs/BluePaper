from bluepaper.ids import is_conversion_id, new_conversion_id


def test_conversion_ids_are_ulids() -> None:
    first = new_conversion_id()
    second = new_conversion_id()
    assert first != second
    assert is_conversion_id(first)
    assert first.startswith("cnv_")
    assert not is_conversion_id("scn_01J8Z3K4N5P6Q7R8S9T0")
    assert not is_conversion_id("cnv_short")


def test_conversion_id_rejects_crockford_exclusions() -> None:
    body = "0" * 25
    assert not is_conversion_id("cnv_" + body + "I")
    assert not is_conversion_id("cnv_" + body + "L")
    assert not is_conversion_id("cnv_" + body + "O")
    assert not is_conversion_id("cnv_" + body + "U")


def test_conversion_id_accepts_lowercase_body() -> None:
    sample = new_conversion_id()
    assert is_conversion_id(sample.lower())
    assert not is_conversion_id(sample.upper())


def test_conversion_id_rejects_empty_and_wrong_length() -> None:
    assert not is_conversion_id("")
    assert not is_conversion_id("cnv_")
    assert not is_conversion_id("cnv_" + "0" * 25)
    assert not is_conversion_id("cnv_" + "0" * 27)
