import pytest

from bluepaper.config import ZERO_HITS_CAVEAT
from bluepaper.scan.matcher import AhoCorasick
from bluepaper.scan.scanner import ScanTimeout, load_catalog, scan_bytes


def test_pdf_javascript_hit() -> None:
    data = b"%PDF-1.4\n<< /JavaScript 2 0 R /JS 3 0 R >>\n"
    result = scan_bytes(data)
    ids = {hit.id: hit for hit in result.hits}
    assert "pdf.javascript" in ids
    assert ids["pdf.javascript"].count >= 1
    assert ids["pdf.javascript"].first_offset is not None


def test_ole_magic() -> None:
    data = bytes.fromhex("d0cf11e0a1b11ae1") + b"vbaProject.bin"
    result = scan_bytes(data)
    ids = {hit.id for hit in result.hits}
    assert "office.ole_magic" in ids
    assert "office.vba_project" in ids


def test_pdf_not_at_zero() -> None:
    data = b"<html>lure</html>\n%PDF-1.7\n"
    result = scan_bytes(data)
    ids = {hit.id: hit for hit in result.hits}
    assert ids["polyglot.pdf_not_at_zero"].first_offset == data.find(b"%PDF")
    assert "polyglot.html_hta" in ids


def test_zip_and_pdf() -> None:
    data = b"PK\x03\x04" + b"xxxx" + b"%PDF-1.4"
    result = scan_bytes(data)
    ids = {hit.id for hit in result.hits}
    assert "polyglot.zip_pdf" in ids


def test_zero_hits_are_not_clean() -> None:
    result = scan_bytes(b"%PDF-1.4\nplain text document\n")
    assert result.hits == []
    assert ZERO_HITS_CAVEAT


def test_pdf_action_literals() -> None:
    data = (
        b"%PDF-1.4\n/OpenAction /Launch /EmbeddedFile /Encrypt /URI /GoToR "
        b"/SubmitForm /RichMedia /XFA /ObjStm /AA\n"
    )
    ids = {hit.id for hit in scan_bytes(data).hits}
    assert {
        "pdf.openaction",
        "pdf.launch",
        "pdf.embeddedfile",
        "pdf.encrypt",
        "pdf.uri",
        "pdf.gotor",
        "pdf.submitform",
        "pdf.richmedia",
        "pdf.xfa",
        "pdf.objstm",
        "pdf.aa",
    } <= ids


def test_office_dde_and_activex() -> None:
    data = b"DDEAUTO sheet1 activeX control"
    ids = {hit.id for hit in scan_bytes(data).hits}
    assert "office.dde" in ids
    assert "office.activex" in ids


def test_office_external_rel() -> None:
    data = b'<Relationship TargetMode="External" Target="https://evil"/>'
    ids = {hit.id for hit in scan_bytes(data).hits}
    assert "office.external_rel" in ids


def test_pdf_at_offset_zero_is_not_polyglot() -> None:
    result = scan_bytes(b"%PDF-1.4\nplain\n")
    ids = {hit.id for hit in result.hits}
    assert "polyglot.pdf_not_at_zero" not in ids
    assert "polyglot.zip_pdf" not in ids


def test_zip_without_pdf_is_not_zip_pdf() -> None:
    result = scan_bytes(b"PK\x03\x04just-a-zip")
    ids = {hit.id for hit in result.hits}
    assert "polyglot.zip_pdf" not in ids


def test_pdf_name_escape_matches_javascript() -> None:
    data = b"%PDF-1.4\n/Java#53cript\n"
    hit = next(h for h in scan_bytes(data).hits if h.id == "pdf.javascript")
    assert hit.count == 1
    assert hit.first_offset == data.find(b"/Java#53cript")


def test_pdf_name_escape_does_not_shift_a_later_literal() -> None:
    data = b"%PDF-1.4\n/Java#53cript /Launch\n"
    hits = {hit.id: hit for hit in scan_bytes(data).hits}
    assert hits["pdf.javascript"].count == 1
    assert hits["pdf.javascript"].first_offset == data.find(b"/Java#53cript")
    assert hits["pdf.launch"].first_offset == data.find(b"/Launch")
    assert hits["pdf.launch"].count == 1


def test_lowercase_hex_name_escape() -> None:
    data = b"/#4aava#53cript"
    hit = next(h for h in scan_bytes(data).hits if h.id == "pdf.javascript")
    assert hit.count == 1
    assert hit.first_offset == 0


def test_encoded_js_name() -> None:
    data = b"/J#53"
    hit = next(h for h in scan_bytes(data).hits if h.id == "pdf.javascript")
    assert hit.count == 1
    assert hit.first_offset == 0


def test_literal_and_escaped_names_both_count() -> None:
    data = b"/JavaScript /Java#53cript"
    hit = next(h for h in scan_bytes(data).hits if h.id == "pdf.javascript")
    assert hit.count == 2
    assert hit.first_offset == 0


def test_hash_outside_a_name_is_not_decoded() -> None:
    data = b"%PDF-1.4\nJava#53cript\n"
    ids = {hit.id for hit in scan_bytes(data).hits}
    assert "pdf.javascript" not in ids


def test_metadata_words_still_match_as_byte_indicators() -> None:
    data = b"%PDF-1.4\n/Title (words /JavaScript /Launch /EmbeddedFile)\n"
    ids = {hit.id for hit in scan_bytes(data).hits}
    assert {"pdf.javascript", "pdf.launch", "pdf.embeddedfile"} <= ids


def test_compressed_javascript_is_not_a_hit() -> None:
    import zlib

    data = b"%PDF-1.4\n" + zlib.compress(b"/JavaScript /Launch")
    ids = {hit.id for hit in scan_bytes(data).hits}
    assert "pdf.javascript" not in ids
    assert "pdf.launch" not in ids


def test_javascript_count_and_offset() -> None:
    data = b"%PDF-1.4\n/JavaScript /JS /JavaScript\n"
    result = scan_bytes(data)
    hit = next(h for h in result.hits if h.id == "pdf.javascript")
    assert hit.count >= 2
    assert hit.first_offset == data.find(b"/JavaScript")


def test_catalog_version() -> None:
    result = scan_bytes(b"%PDF-1.4\n")
    assert result.catalog_version == load_catalog()["version"]
    assert result.timed_out is False


def test_scan_timeout_before_work(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter([10.0, 40.0])
    monkeypatch.setattr("bluepaper.scan.scanner.time.monotonic", lambda: next(clock))
    with pytest.raises(ScanTimeout):
        scan_bytes(b"%PDF-1.4\n", timeout_seconds=1.0)


def test_scan_timeout_after_matcher(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter([10.0, 10.1, 50.0])
    monkeypatch.setattr("bluepaper.scan.scanner.time.monotonic", lambda: next(clock))
    with pytest.raises(ScanTimeout):
        scan_bytes(b"%PDF-1.4\n/JavaScript\n", timeout_seconds=5.0)


def test_aho_corasick_counts_overlapping_and_repeats() -> None:
    matcher = AhoCorasick([("a", b"a"), ("aa", b"aa")])
    hits = matcher.find(b"aaa")
    assert hits["a"] == (3, 0)
    assert hits["aa"] == (2, 0)


def test_aho_corasick_skips_empty_patterns() -> None:
    matcher = AhoCorasick([("empty", b""), ("x", b"x")])
    assert matcher.find(b"xxx") == {"x": (3, 0)}
    assert AhoCorasick([]).find(b"abc") == {}


def test_aho_corasick_suffix_outputs() -> None:
    matcher = AhoCorasick([("he", b"he"), ("she", b"she"), ("his", b"his")])
    hits = matcher.find(b"she")
    assert hits["she"] == (1, 0)
    assert hits["he"] == (1, 1)
    assert "his" not in hits
